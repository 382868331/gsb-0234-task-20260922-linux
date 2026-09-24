"""Beaver 乘法的离线转录模拟（协议核心）。

执行模型（带“上帝视角”的本地模拟器，不模拟任何网络）：

1. :class:`TripleBank` 登记显式合成的 Beaver 三元组，并维护每个
   ``triple_id`` 的一次性生命周期：
   ``free -> reserved(op) -> consumed``；若操作被冲突中止则
   ``reserved(op) -> voided``（作废，任何操作不得再领取）。
2. 每个参与方把 :class:`~sss_mult.shares.Message` 投递给
   :class:`Transcript`。消息按**会话**隔离，键为
   ``(session_id, op_id, party)``：
   重复且完全一致 → 幂等忽略；任一字段不一致 → 冲突，**该操作立即
   中止**（终态 ``aborted``），不能靠重放复活。
3. 同一操作的消息必须引用相同 ``triple_id``，否则同样中止该操作
   （:class:`TripleMismatchError`）。三元组被某操作领取（预留）后，
   其他操作（包括其他会话中的同名操作）不得使用；打开成功即永久
   消费（:class:`TripleReusedError`）。
4. 三方消息全部到齐才能 :meth:`Transcript.open`；缺方抛
   :class:`IncompleteTranscriptError` 且**不改变任何状态**（不消费三元组）。
5. 打开后各方输出份额
   ``z_i = c_i + d*b_i + e*a_i + [i==0]*d*e (mod p)``，
   其中 ``d = Σd_i``、``e = Σe_i`` 为公开值。

消息可以**乱序**投递：转录机只按 ``(session_id, op_id, party)`` 缓冲，
齐三方后顺序无关。中止只影响发生冲突的那一个操作：同一 transcript 内
其他会话/操作的缓冲与三元组状态均不受影响。

安全声明：本模块只实现被动（半诚实）模型下的算术模拟与转录一致性
检查，**不提供恶意安全**：参与方可以提交与其真实份额不符的消息，
模拟器不会验证其知识一致性，只会在转录层检出不一致并中止操作。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    ConflictError,
    IncompleteTranscriptError,
    LimitError,
    OperationAbortedError,
    TranscriptError,
    TripleMismatchError,
    TripleReusedError,
    UnknownTripleError,
)
from .field import DEFAULT_SESSION_ID, MAX_MESSAGES, MAX_OPERATIONS, N_PARTIES, P
from .shares import (
    BeaverTriple,
    Message,
    check_id,
    derive_messages,
    random_share,
    reconstruct,
)


class UnknownOperationError(TranscriptError, ValueError):
    """打开了一个该会话中从未投递过消息的 ``op_id``。"""

    def __init__(self, op_id: str, session_id: str = DEFAULT_SESSION_ID):
        super().__init__(
            f"unknown op_id {op_id!r} in session {session_id!r}: "
            f"no message has been delivered"
        )
        self.op_id = op_id
        self.session_id = session_id


@dataclass(frozen=True)
class PartyView:
    """打开后参与方 ``party`` 的完整本地视图。"""

    party: int
    a_i: int
    b_i: int
    c_i: int
    d_i: int  # 该方自己广播的份额
    e_i: int
    d: int  # 公开值（三方一致）
    e: int
    z_i: int  # 输出乘积份额


@dataclass(frozen=True)
class OperationResult:
    """一次乘法操作的打开结果（模拟器视角）。"""

    op_id: str
    triple_id: str
    d: int
    e: int
    x: int  # 重构输入 x = a + d
    y: int  # 重构输入 y = b + e
    views: tuple[PartyView, ...]
    product: int  # 重构乘积 Σ z_i
    session_id: str = DEFAULT_SESSION_ID

    def output_shares(self) -> tuple[int, int, int]:
        """按参与方编号返回输出份额 ``(z_0, z_1, z_2)``。"""
        return tuple(v.z_i for v in self.views)  # type: ignore[return-value]


class TripleBank:
    """Beaver 三元组注册表，负责 ``triple_id`` 的一次性生命周期。"""

    def __init__(self):
        self._triples: dict[str, BeaverTriple] = {}
        # triple_id -> None(free)，否则为
        # (state, session_id, op_id)，state ∈ reserved/consumed/voided。
        # 记录 session_id 是因为不同会话允许同名 op_id。
        self._state: dict[str, tuple[str, str, str] | None] = {}

    def register(self, triple: BeaverTriple) -> None:
        """登记一个三元组。重复登记同一 ``triple_id`` 抛 :class:`ConflictError`。"""
        if not isinstance(triple, BeaverTriple):
            raise TypeError(f"triple must be a BeaverTriple, got {type(triple).__name__}")
        if triple.triple_id in self._triples:
            raise ConflictError(
                f"triple_id {triple.triple_id!r} is already registered"
            )
        self._triples[triple.triple_id] = triple
        self._state[triple.triple_id] = None

    def get(self, triple_id: str) -> BeaverTriple:
        try:
            return self._triples[triple_id]
        except KeyError:
            raise UnknownTripleError(triple_id) from None

    def state(self, triple_id: str) -> str:
        """返回生命周期状态：``"free" | "reserved" | "consumed" | "voided"``。"""
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        return "free" if st is None else st[0]

    def reserve(self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> None:
        """为操作领取（预留）三元组；同操作重复领取幂等。

        ``free`` → 预留成功；被别的操作（含其他会话中的同名操作）预留/
        已消费/已作废 → :class:`TripleReusedError`。三元组不可跨操作复用。
        """
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        if st is None:
            self._state[triple_id] = ("reserved", session_id, op_id)
            return
        kind, holder_session, holder_op = st
        same_holder = holder_session == session_id and holder_op == op_id
        if kind == "reserved":
            if same_holder:
                # 同一操作重复预留：幂等。
                return
            raise TripleReusedError(
                triple_id, op_id, holder_op, consumed=False,
                holder_session=holder_session,
            )
        if kind == "voided":
            raise TripleReusedError(
                triple_id, op_id, holder_op, consumed=False, voided=True,
                holder_session=holder_session,
            )
        # consumed
        raise TripleReusedError(
            triple_id, op_id, holder_op, consumed=True,
            holder_session=holder_session,
        )

    def release(self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> None:
        """撤销某操作的预留（回滚用）；非该会话/操作持有时不动。

        已作废（voided）/已消费的三元组不会被释放：二者都是终态。
        """
        st = self._state.get(triple_id)
        if st == ("reserved", session_id, op_id):
            self._state[triple_id] = None

    def void(self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> None:
        """把操作领取的三元组作废（操作因冲突中止时调用）；终态。

        仅当三元组仍处于该操作的 ``reserved`` 状态时才转为 ``voided``；
        若操作已经打开（``consumed``），消费不可撤销，保持 ``consumed``。
        """
        st = self._state.get(triple_id)
        if st == ("reserved", session_id, op_id):
            self._state[triple_id] = ("voided", session_id, op_id)
            return
        if st == ("consumed", session_id, op_id):
            return
        # 其他状态只能说明本库内部状态机出错。
        kind = st[0] if st else None
        raise TripleReusedError(
            triple_id, op_id, st[2] if st else "<none>",
            consumed=kind == "consumed", voided=kind == "voided",
            holder_session=st[1] if st else DEFAULT_SESSION_ID,
        )

    def consume(self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> None:
        st = self._state.get(triple_id)
        if st != ("reserved", session_id, op_id):
            # 到达这里只能说明本库内部状态机出错。
            kind = st[0] if st else None
            raise TripleReusedError(
                triple_id, op_id, st[2] if st else "<none>",
                consumed=kind == "consumed", voided=kind == "voided",
                holder_session=st[1] if st else DEFAULT_SESSION_ID,
            )
        self._state[triple_id] = ("consumed", session_id, op_id)


@dataclass
class _Op:
    session_id: str
    op_id: str
    triple_id: str
    messages: dict[int, Message]
    opened: bool = False
    result: OperationResult | None = None
    aborted: bool = False
    abort_reason: str = ""


class Transcript:
    """转录机：按会话收集消息、做一致性检查、满足条件后打开。

    会话隔离：不同 ``session_id`` 下可以使用相同的 ``op_id`` 而互不
    影响；但 :class:`TripleBank` 是全局的，三元组一旦被任一操作领取，
    就不能再被其他操作（含其他会话）复用。

    原子性：单条投递的所有检查先于状态落盘；批次 :meth:`add_messages`
    中，任一消息失败时，本批次对**仍存活**的操作所做的变更全部回滚
    （消息、新建操作、三元组预留）。冲突中止是终态语义：若某操作在
    批次中被中止，该中止及其三元组作废不会被回滚（同一批次里其他
    无操作则照常回滚）。
    """

    def __init__(
        self,
        bank: TripleBank,
        *,
        max_operations: int = MAX_OPERATIONS,
        max_messages: int = MAX_MESSAGES,
    ):
        if isinstance(max_operations, bool) or type(max_operations) is not int:
            raise TypeError("max_operations must be an int (bool not accepted)")
        if isinstance(max_messages, bool) or type(max_messages) is not int:
            raise TypeError("max_messages must be an int (bool not accepted)")
        if max_operations <= 0 or max_messages <= 0:
            raise ValueError("max_operations/max_messages must be positive")
        self._bank = bank
        self._max_operations = max_operations
        self._max_messages = max_messages
        # session_id -> {op_id -> _Op}
        self._sessions: dict[str, dict[str, _Op]] = {}

    # ---- 内部定位 -----------------------------------------------------

    def _get_op(self, session_id: str, op_id: str) -> _Op | None:
        return self._sessions.get(session_id, {}).get(op_id)

    def _abort(self, op: _Op, reason: str) -> None:
        """把操作置为 aborted 终态并作废其三元组（幂等）。"""
        if not op.aborted:
            op.aborted = True
            op.abort_reason = reason
            self._bank.void(op.triple_id, op.op_id, op.session_id)

    # ---- 只读观察接口 -------------------------------------------------

    @property
    def bank(self) -> TripleBank:
        return self._bank

    def is_complete(self, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> bool:
        """三方消息是否全部到齐（该会话未投递过的操作/已中止返回 ``False``）。"""
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        return op is not None and not op.aborted and len(op.messages) == N_PARTIES

    def is_open(self, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> bool:
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        return op is not None and op.opened and not op.aborted

    def is_aborted(self, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> bool:
        """该会话中的操作是否已因冲突中止（终态）。"""
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        return op is not None and op.aborted

    def abort_reason(self, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> str | None:
        """返回中止原因；未中止或未知返回 ``None``。"""
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        if op is None or not op.aborted:
            return None
        return op.abort_reason

    def missing_parties(
        self, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> tuple[int, ...]:
        """尚未投递消息的参与方编号（升序）；未知/已中止操作的语义见调用方。

        未知操作返回全部三方；已中止操作返回中止时刻缓冲中仍缺的参与方。
        """
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        have = set() if op is None else set(op.messages)
        return tuple(i for i in range(N_PARTIES) if i not in have)

    def sessions_count(self) -> int:
        """出现过（至少一条消息）的会话数量。"""
        return len(self._sessions)

    def operations_count(self, session_id: str | None = None) -> int:
        """操作数量；``session_id=None``（默认）统计全部会话。"""
        if session_id is None:
            return sum(len(ops) for ops in self._sessions.values())
        check_id(session_id, "session_id")
        return len(self._sessions.get(session_id, ()))

    def messages_count(self, session_id: str | None = None) -> int:
        """已缓冲消息数量；``session_id=None``（默认）统计全部会话。"""
        if session_id is None:
            return sum(len(op.messages) for ops in self._sessions.values() for op in ops.values())
        check_id(session_id, "session_id")
        return sum(len(op.messages) for op in self._sessions.get(session_id, {}).values())

    def result(
        self, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> OperationResult:
        """返回已打开操作的结果；未打开/未知/已中止抛 :class:`TranscriptError`。"""
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        if op is None:
            raise UnknownOperationError(op_id, session_id)
        if op.aborted:
            raise OperationAbortedError(session_id, op_id, op.abort_reason)
        if not op.opened:
            raise TranscriptError(
                f"op {op_id!r} in session {session_id!r} has not been opened yet"
            )
        return op.result  # type: ignore[return-value]

    # ---- 消息投递 -----------------------------------------------------

    def add_message(self, message: Message) -> str:
        """投递一条消息（可乱序；按会话/操作/参与方缓冲）。

        :returns: ``"accepted"``（新消息）或 ``"duplicate"``（重复一致，幂等）。
        :raises TypeError: ``message`` 不是 :class:`Message`（其字段在构造时
            已校验，非法消息无法构造）。
        :raises ConflictError / TripleMismatchError: 冲突重复或同操作引用了
            不同 ``triple_id``。**该操作随即中止**（三元组作废、缓冲冻结），
            错误本身不把冲突消息写入缓冲。
        :raises OperationAbortedError: 向已中止的操作继续投递（任何重放，
            哪怕内容与原消息一致，都被拒绝）。
        :raises TripleReusedError / LimitError: 三元组已被其他操作领取/
            消费/作废，或超过转录机上限；这些错误不改变任何状态。
        """
        if not isinstance(message, Message):
            raise TypeError(
                f"message must be a Message, got {type(message).__name__}"
            )
        sid = message.session_id
        ops = self._sessions.get(sid)
        op = None if ops is None else ops.get(message.op_id)

        if op is None:
            # 新操作：先做全部前置校验，再落盘。
            if self.operations_count() >= self._max_operations:
                raise LimitError(
                    f"operation limit reached: max_operations={self._max_operations}"
                )
            if self.messages_count() >= self._max_messages:
                raise LimitError(f"message limit reached: max_messages={self._max_messages}")
            # reserve 可能抛 TripleReusedError / UnknownTripleError，均在落盘前。
            self._bank.reserve(message.triple_id, message.op_id, sid)
            if ops is None:
                ops = {}
                self._sessions[sid] = ops
            ops[message.op_id] = _Op(sid, message.op_id, message.triple_id, {message.party: message})
            return "accepted"

        # 既有操作：已中止则任何投递（含一致重放）都拒绝。
        if op.aborted:
            raise OperationAbortedError(sid, message.op_id, op.abort_reason)

        # 既有操作：三元组引用必须一致，否则中止该操作。
        if message.triple_id != op.triple_id:
            reason = (
                f"message from party {message.party} references triple "
                f"{message.triple_id!r} but the op already uses "
                f"{op.triple_id!r}"
            )
            self._abort(op, reason)
            raise TripleMismatchError(
                f"op {message.op_id!r} in session {sid!r} aborted: {reason}"
            )

        existing = op.messages.get(message.party)
        if existing is not None:
            if existing == message:
                return "duplicate"
            reason = (
                f"conflicting message from party {message.party}: "
                f"stored d={existing.d}, e={existing.e}; "
                f"got d={message.d}, e={message.e}"
            )
            # 冲突：中止操作、作废三元组；冲突消息不落盘。
            self._abort(op, reason)
            raise ConflictError(
                f"op {message.op_id!r} in session {sid!r} aborted: {reason}"
            )

        if self.messages_count() >= self._max_messages:
            raise LimitError(f"message limit reached: max_messages={self._max_messages}")
        op.messages[message.party] = message
        return "accepted"

    def add_messages(self, messages) -> list[str]:
        """批量投递；任一条失败时回滚本批次对存活操作的全部变更。

        返回与输入等长、等序的 ``"accepted" | "duplicate"`` 列表。

        回滚边界：若失败原因是冲突，被冲突中止的操作及其三元组作废是
        **终态**，不会被回滚（冲突是针对已观察到的缓冲内容做出的判定）；
        本批次为其他操作新建的记录与预留则全部撤销。
        """
        seq = list(messages)
        for i, m in enumerate(seq):
            if not isinstance(m, Message):
                raise TypeError(f"messages[{i}] must be a Message, got {type(m).__name__}")

        # (session_id, op_id, party) 本批次新接受的消息；本批次新建的操作。
        inserted: list[tuple[str, str, int]] = []
        created: list[tuple[str, str]] = []
        results: list[str] = []
        try:
            for m in seq:
                key = (m.session_id, m.op_id)
                existed = self._get_op(m.session_id, m.op_id) is not None
                status = self.add_message(m)
                if not existed:
                    created.append(key)
                if status == "accepted":
                    inserted.append((m.session_id, m.op_id, m.party))
                results.append(status)
        except BaseException:
            # 逆序回滚本批次接受的消息；随后删除本批次新建且未中止的操作，
            # 并释放其预留。已中止的操作保留（终态），其三元组保持 voided。
            for sid, op_id, party in reversed(inserted):
                op = self._get_op(sid, op_id)
                if op is not None and not op.aborted:
                    op.messages.pop(party, None)
            for sid, op_id in reversed(created):
                ops_map = self._sessions.get(sid)
                if ops_map is None:
                    continue
                op = ops_map.get(op_id)
                if op is None or op.aborted:
                    continue
                ops_map.pop(op_id, None)
                if not ops_map:
                    del self._sessions[sid]
                self._bank.release(op.triple_id, op_id, sid)
            raise
        return results

    # ---- 打开 ---------------------------------------------------------

    def open(self, op_id: str, session_id: str = DEFAULT_SESSION_ID) -> OperationResult:
        """齐三方后打开操作并计算各方视图；重复打开返回缓存结果（幂等）。

        :raises OperationAbortedError: 操作已因冲突中止；重放不能复活。
        :raises IncompleteTranscriptError: 参与方未齐，且不改变任何状态。
        """
        check_id(session_id, "session_id")
        check_id(op_id, "op_id")
        op = self._get_op(session_id, op_id)
        if op is None:
            raise UnknownOperationError(op_id, session_id)
        if op.aborted:
            raise OperationAbortedError(session_id, op_id, op.abort_reason)
        if op.opened:
            return op.result  # type: ignore[return-value]

        missing = tuple(i for i in range(N_PARTIES) if i not in op.messages)
        if missing:
            # 关键：缺方不消费三元组、不缓存任何结果。
            raise IncompleteTranscriptError(op_id, missing, session_id)

        triple = self._bank.get(op.triple_id)
        msgs = [op.messages[i] for i in range(N_PARTIES)]
        d = sum(m.d for m in msgs) % P
        e = sum(m.e for m in msgs) % P
        de = (d * e) % P

        views: list[PartyView] = []
        for i in range(N_PARTIES):
            a_i, b_i, c_i = triple.party_view(i)
            z_i = (c_i + d * b_i + e * a_i + (de if i == 0 else 0)) % P
            views.append(PartyView(i, a_i, b_i, c_i, msgs[i].d, msgs[i].e, d, e, z_i))

        product = reconstruct(v.z_i for v in views)
        result = OperationResult(
            op_id=op_id,
            triple_id=op.triple_id,
            d=d,
            e=e,
            x=(triple.a + d) % P,
            y=(triple.b + e) % P,
            views=tuple(views),
            product=product,
            session_id=session_id,
        )
        # 计算完成后再消费并落盘。
        self._bank.consume(op.triple_id, op_id, session_id)
        op.opened = True
        op.result = result
        return result


def honest_prepare(
    op_id: str,
    triple: BeaverTriple,
    x: int,
    y: int,
    rng,
    *,
    session_id: str = DEFAULT_SESSION_ID,
):
    """诚实模拟的语法糖：对显式输入 ``x, y`` 生成分享与三条打开消息。

    返回 ``(x_shares, y_shares, messages)``。不直接投递，调用方可自行
    乱序/重复/篡改后投递，以测试转录机行为。

    :param session_id: 会话 ID（仅限关键字）；默认 ``"default"``。
    """
    x_shares = random_share(x, rng)
    y_shares = random_share(y, rng)
    messages = derive_messages(
        op_id, triple, x_shares, y_shares, session_id=session_id
    )
    return x_shares, y_shares, messages
