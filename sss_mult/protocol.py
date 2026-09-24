"""Beaver 乘法的离线转录模拟（协议核心）。

执行模型（带“上帝视角”的本地模拟器，不模拟任何网络）：

1. :class:`TripleBank` 登记显式合成的 Beaver 三元组，并维护每个
   ``triple_id`` 的一次性生命周期：
   ``free -> reserved(session, op) -> consumed``（正常打开）或
   ``free -> reserved(session, op) -> aborted``（操作因冲突重复被中止，
   三元组一并**烧毁**、永不再分配给任何操作/会话）。
2. 每条 :class:`~sss_mult.shares.Message` 绑定三元组
   ``(session_id, op_id, party)``。投递层有两种策略：

   - :class:`Transcript`（即时转录机，旧契约）：乱序收集消息，三方齐后
     打开；冲突重复直接拒绝且**不落盘**（保持历史行为）。
   - :class:`Session`（会话缓冲视图，本次新增）：在共享的
     :class:`Transcript` 上按固定 ``session_id`` 路由，同样允许乱序投递，
     但**冲突重复会把整个操作置为 aborted 终态**（消息缓冲关闭、三元组
     烧毁），相同重复仍然幂等。
3. 同一操作的消息必须引用相同 ``triple_id``；三元组一旦被某操作领取
   （预留），其他操作（含其他会话）不得使用，打开后永久消费、中止后
   永久烧毁（:class:`TripleReusedError`）。
4. 三方消息全部到齐才能打开；缺方抛
   :class:`IncompleteTranscriptError` 且**不改变任何状态**（不消费三元组）。
5. 打开后各方输出份额
   ``z_i = c_i + d*b_i + e*a_i + [i==0]*d*e (mod p)``，
   其中 ``d = Σd_i``、``e = Σe_i`` 为公开值。

安全声明：本模块只实现被动（半诚实）模型下的算术模拟与转录一致性
检查，**不提供恶意安全**：参与方可以提交与其真实份额不符的消息，
模拟器不会验证其知识一致性。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    ConflictError,
    IncompleteTranscriptError,
    LimitError,
    OperationAbortedError,
    SessionMismatchError,
    TranscriptError,
    TripleMismatchError,
    TripleReusedError,
    UnknownTripleError,
)
from .field import MAX_MESSAGES, MAX_OPERATIONS, N_PARTIES, P
from .shares import (
    DEFAULT_SESSION_ID,
    BeaverTriple,
    Message,
    check_id,
    derive_messages,
    random_share,
    reconstruct,
)


def _holder_label(session_id: str, op_id: str) -> str:
    """三元组持有方在错误信息中的标签：默认会话只显示 op_id（旧契约）。"""
    return op_id if session_id == DEFAULT_SESSION_ID else f"{session_id}/{op_id}"


class UnknownOperationError(TranscriptError, ValueError):
    """打开了一个从未投递过消息的 ``(session_id, op_id)``。"""

    def __init__(self, op_id: str, session_id: str = DEFAULT_SESSION_ID):
        where = op_id if session_id == DEFAULT_SESSION_ID else f"{session_id}/{op_id}"
        super().__init__(f"unknown op {where!r}: no message has been delivered")
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
    """Beaver 三元组注册表，负责 ``triple_id`` 的一次性生命周期。

    合法状态转移：

    - ``free -> reserved(session, op) -> consumed``（正常打开，三元组只
      消费一次）；
    - ``free -> reserved(session, op) -> aborted``（操作因冲突重复被中止，
      三元组**烧毁**：不会再分配给任何操作或会话）。

    ``consumed`` 与 ``aborted`` 都是终态；领取（reserve）处于任一终态的
    三元组都会抛 :class:`TripleReusedError`。
    """

    def __init__(self):
        self._triples: dict[str, BeaverTriple] = {}
        # triple_id -> None(free) 或 (state, session_id, op_id)
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
        """返回生命周期状态：``"free" | "reserved" | "consumed" | "aborted"``。"""
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        return "free" if st is None else st[0]

    def reserve(
        self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> None:
        """为操作领取（预留）三元组；同操作同会话重复预留幂等。

        :raises TripleReusedError: 三元组已被其他操作预留，或处于
            ``consumed`` / ``aborted`` 终态。
        """
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        if st is None:
            self._state[triple_id] = ("reserved", session_id, op_id)
            return
        state, holder_session, holder_op = st
        same_holder = holder_session == session_id and holder_op == op_id
        if state == "reserved":
            if same_holder:
                return  # 同一操作重复领取：幂等。
            raise TripleReusedError(
                triple_id,
                op_id,
                _holder_label(holder_session, holder_op),
                consumed=False,
            )
        # consumed / aborted：终态，永不再分配。
        raise TripleReusedError(
            triple_id,
            op_id,
            _holder_label(holder_session, holder_op),
            consumed=True,
            terminal_state=state,
        )

    def release(
        self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> None:
        """撤销某操作的预留（回滚用）；非该操作持有或已终态时不动。"""
        st = self._state.get(triple_id)
        if st is not None and st == ("reserved", session_id, op_id):
            self._state[triple_id] = None

    def consume(
        self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> None:
        st = self._state.get(triple_id)
        if st != ("reserved", session_id, op_id):
            # 到达这里只能说明本库内部状态机出错。
            if st is None:
                raise TripleReusedError(triple_id, op_id, "<none>", consumed=False)
            raise TripleReusedError(
                triple_id,
                op_id,
                _holder_label(st[1], st[2]),
                consumed=st[0] in ("consumed", "aborted"),
                terminal_state=st[0],
            )
        self._state[triple_id] = ("consumed", session_id, op_id)

    def abort(
        self, triple_id: str, op_id: str, session_id: str = DEFAULT_SESSION_ID
    ) -> None:
        """把操作领取的三元组标记为 ``aborted``（烧毁，终态）。

        允许的转移：``reserved -> aborted``、``consumed -> aborted``
        （已打开操作若再收到冲突重复，同样烧毁，二者都只能由持有操作
        触发）。同操作重复中止幂等；其他持有的操作无权中止。
        """
        st = self._state.get(triple_id)
        if st is None:
            raise UnknownTripleError(triple_id)
        state, holder_session, holder_op = st
        same_holder = holder_session == session_id and holder_op == op_id
        if not same_holder:
            raise TripleReusedError(
                triple_id,
                op_id,
                _holder_label(holder_session, holder_op),
                consumed=state in ("consumed", "aborted"),
                terminal_state=state,
            )
        if state in ("reserved", "consumed"):
            self._state[triple_id] = ("aborted", session_id, op_id)
        # state == "aborted"：终态，幂等返回。


@dataclass
class _Op:
    session_id: str
    op_id: str
    triple_id: str
    messages: dict[int, Message]
    opened: bool = False
    result: OperationResult | None = None
    aborted: bool = False
    abort_reason: str | None = None
    abort_party: int | None = None


# 冲突处理策略：
_REJECT = "reject"  # 旧契约：拒绝且不落盘
_ABORT = "abort"    # 新语义：中止该操作（烧毁三元组）


class Transcript:
    """转录机：跨会话收集消息、做一致性检查、满足条件后打开。

    操作按 ``(session_id, op_id)`` 唯一索引：不同会话可以使用相同的
    ``op_id`` 而互不干扰；三元组领取在全局 :class:`TripleBank` 层面互斥，
    因此跨会话复用仍会被拒绝。

    变更方法都是原子的：校验全部通过后才落盘状态；批次方法
    :meth:`add_messages` 失败时回滚本批次已做的全部变更（含三元组预留），
    唯一例外是冲突中止策略（见 :class:`Session`）——中止本身是题面规定的
    终态落盘。
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
        self._ops: dict[tuple[str, str], _Op] = {}

    # ---- 工厂 ---------------------------------------------------------

    def session(self, session_id: str) -> "Session":
        """在本转录机上打开一个固定会话视图（多个会话共享底层状态）。"""
        return Session(session_id, self)

    # ---- 只读观察接口 -------------------------------------------------

    @property
    def bank(self) -> TripleBank:
        return self._bank

    def _get(self, op_id: str, session_id: str) -> _Op | None:
        return self._ops.get((session_id, op_id))

    def is_complete(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> bool:
        """三方消息是否全部到齐（未投递过的操作返回 ``False``）。"""
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        return op is not None and not op.aborted and len(op.messages) == N_PARTIES

    def is_open(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> bool:
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        # 中止是终态：即便此前打开过，也不再视为有效打开。
        return op is not None and op.opened and not op.aborted

    def is_aborted(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> bool:
        """操作是否已因冲突重复被中止（终态）。"""
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        return op is not None and op.aborted

    def abort_reason(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> str | None:
        """中止原因；未中止返回 ``None``。"""
        check_id(op_id, "op_id")
        op = self._get(op_id, session_id)
        return None if op is None else op.abort_reason

    def missing_parties(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> tuple[int, ...]:
        """尚未投递消息的参与方编号（升序）；未知/中止操作返回全部三方。"""
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        have = set() if op is None or op.aborted else set(op.messages)
        return tuple(i for i in range(N_PARTIES) if i not in have)

    def operations_count(self) -> int:
        return len(self._ops)

    def messages_count(self) -> int:
        return sum(len(op.messages) for op in self._ops.values())

    def aborted_operations(self, *, session_id: str | None = None) -> tuple[str, ...]:
        """已中止操作的 ``op_id`` 列表（可按会话过滤），按首次出现顺序。"""
        out = []
        for (sid, op_id), op in self._ops.items():
            if op.aborted and (session_id is None or sid == session_id):
                out.append(op_id)
        return tuple(out)

    def result(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> OperationResult:
        """返回已打开操作的结果；未打开抛 :class:`TranscriptError`。"""
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        if op is None:
            raise UnknownOperationError(op_id, session_id)
        if op.aborted:
            raise OperationAbortedError(session_id, op_id, op.abort_reason or "aborted", op.abort_party)
        if not op.opened:
            raise TranscriptError(
                f"op {_holder_label(session_id, op_id)!r} has not been opened yet"
            )
        return op.result  # type: ignore[return-value]

    # ---- 消息投递 -----------------------------------------------------

    def add_message(self, message: Message) -> str:
        """投递一条消息（旧契约：冲突重复直接拒绝且不改变状态）。

        :returns: ``"accepted"``（新消息）或 ``"duplicate"``（重复一致，幂等）。
        :raises TypeError: ``message`` 不是 :class:`Message`（其字段在构造时
            已校验，非法消息无法构造）。
        :raises TripleMismatchError / ConflictError / TripleReusedError /
            LimitError: 见模块文档；这些错误均不改变转录状态。
        """
        return self._deliver(message, _REJECT)

    def add_messages(self, messages) -> list[str]:
        """批量投递；任一条失败则回滚本批次全部已应用变更。

        返回与输入等长、等序的 ``"accepted" | "duplicate"`` 列表。
        """
        return self._deliver_many(messages, _REJECT)

    def _deliver(self, message: Message, policy: str) -> str:
        if not isinstance(message, Message):
            raise TypeError(
                f"message must be a Message, got {type(message).__name__}"
            )
        key = (message.session_id, message.op_id)
        op = self._ops.get(key)
        if op is None:
            # 新操作：先做全部前置校验，再落盘。
            if len(self._ops) >= self._max_operations:
                raise LimitError(
                    f"operation limit reached: max_operations={self._max_operations}"
                )
            if self.messages_count() >= self._max_messages:
                raise LimitError(f"message limit reached: max_messages={self._max_messages}")
            # reserve 可能抛 TripleReusedError / UnknownTripleError，均在落盘前。
            self._bank.reserve(message.triple_id, message.op_id, message.session_id)
            self._ops[key] = _Op(
                message.session_id, message.op_id, message.triple_id,
                {message.party: message},
            )
            return "accepted"

        # 已中止操作：缓冲永久关闭。
        if op.aborted:
            raise OperationAbortedError(
                op.session_id, op.op_id, op.abort_reason or "aborted", op.abort_party
            )

        # 既有操作：三元组引用必须一致。
        if message.triple_id != op.triple_id:
            return self._conflict(
                policy, op, message,
                reason=(
                    f"triple_id mismatch: operation already uses {op.triple_id!r}, "
                    f"message references {message.triple_id!r}"
                ),
            )
        existing = op.messages.get(message.party)
        if existing is not None:
            if existing == message:
                return "duplicate"
            return self._conflict(
                policy, op, message,
                reason=(
                    f"conflicting message from party {message.party}: "
                    f"stored d={existing.d}, e={existing.e}; "
                    f"got d={message.d}, e={message.e}"
                ),
            )
        if self.messages_count() >= self._max_messages:
            raise LimitError(f"message limit reached: max_messages={self._max_messages}")
        op.messages[message.party] = message
        return "accepted"

    def _conflict(self, policy: str, op: _Op, message: Message, *, reason: str) -> str:
        """按策略处理冲突重复。reject 策略下落盘前抛错；abort 策略下置终态。"""
        if policy == _REJECT:
            # 旧契约：构造与历史一致的错误类型，不改变任何状态。
            if message.triple_id != op.triple_id:
                raise TripleMismatchError(
                    f"op {message.op_id!r} already uses triple {op.triple_id!r}, "
                    f"refusing message from party {message.party} referencing "
                    f"{message.triple_id!r}"
                )
            existing = op.messages[message.party]
            raise ConflictError(
                f"conflicting message for (op={message.op_id!r}, party={message.party}): "
                f"stored triple={existing.triple_id!r}, d={existing.d}, e={existing.e}; "
                f"got triple={message.triple_id!r}, d={message.d}, e={message.e}"
            )
        # abort 策略：中止是题面规定的终态，必须先烧毁三元组再标记操作。
        self._bank.abort(op.triple_id, op.op_id, op.session_id)
        op.aborted = True
        op.abort_reason = reason
        op.abort_party = message.party
        raise OperationAbortedError(op.session_id, op.op_id, reason, message.party)

    def _deliver_many(self, messages, policy: str) -> list[str]:
        seq = list(messages)
        for i, m in enumerate(seq):
            if not isinstance(m, Message):
                raise TypeError(f"messages[{i}] must be a Message, got {type(m).__name__}")

        created_keys: list[tuple[str, str]] = []
        inserted: list[tuple[str, str, int]] = []
        results: list[str] = []
        try:
            for m in seq:
                key = (m.session_id, m.op_id)
                existed = key in self._ops
                status = self._deliver(m, policy)
                if not existed:
                    created_keys.append(key)
                if status == "accepted":
                    inserted.append((m.session_id, m.op_id, m.party))
                results.append(status)
        except OperationAbortedError as exc:
            # 中止是题面规定的终态（烧毁三元组、置 aborted），不回滚；
            # 只回滚本批次对*其他*操作的变更。中止操作本批次已缓冲的消息
            # 作为终态状态保留（冲突消息本身从未落盘）。
            aborted_key = (exc.session_id, exc.op_id)
            for sid, op_id, party in reversed(inserted):
                if (sid, op_id) == aborted_key:
                    continue
                self._ops[(sid, op_id)].messages.pop(party, None)
            for key in reversed(created_keys):
                if key == aborted_key:
                    continue
                op = self._ops.pop(key, None)
                if op is not None:
                    self._bank.release(op.triple_id, op.op_id, op.session_id)
            raise
        except Exception:
            # 普通失败：整批逆序回滚：删消息、删操作、释放预留。
            for sid, op_id, party in reversed(inserted):
                self._ops[(sid, op_id)].messages.pop(party, None)
            for key in reversed(created_keys):
                op = self._ops.pop(key, None)
                if op is not None:
                    self._bank.release(op.triple_id, op.op_id, op.session_id)
            raise
        return results

    # ---- 打开 ---------------------------------------------------------

    def open(
        self, op_id: str, *, session_id: str = DEFAULT_SESSION_ID
    ) -> OperationResult:
        """齐三方后打开操作并计算各方视图；重复打开返回缓存结果（幂等）。"""
        check_id(op_id, "op_id")
        check_id(session_id, "session_id")
        op = self._get(op_id, session_id)
        if op is None:
            raise UnknownOperationError(op_id, session_id)
        if op.aborted:
            raise OperationAbortedError(
                session_id, op_id, op.abort_reason or "aborted", op.abort_party
            )
        if op.opened:
            return op.result  # type: ignore[return-value]

        missing = tuple(i for i in range(N_PARTIES) if i not in op.messages)
        if missing:
            # 关键：缺方不消费三元组、不缓存任何结果。
            raise IncompleteTranscriptError(op_id, missing)

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
            session_id=op.session_id,
        )
        # 计算完成后再消费并落盘。
        self._bank.consume(op.triple_id, op_id, op.session_id)
        op.opened = True
        op.result = result
        return result


class Session:
    """固定 ``session_id`` 的会话缓冲视图， backed by a shared :class:`Transcript`.

    语义（本次迭代新增）：

    - 消息按会话路由：``message.session_id`` 必须等于本会话 ID，否则抛
      :class:`SessionMismatchError`，且不改变任何状态（送错会话的消息可以
      改送到正确会话）。
    - 允许任意乱序投递，消息在底层转录机中按 ``(session_id, op_id)``
      缓冲，齐三方后 :meth:`open`。
    - 完全相同的重复消息幂等忽略（``"duplicate"``）。
    - **冲突重复（同参与方内容不一致，或同操作引用不同三元组）永久中止
      该操作**：后续该操作的一切投递/打开都抛
      :class:`OperationAbortedError`，已领取的三元组烧毁；同会话的其他
      操作、其他会话均不受影响。
    - 三元组在全局银行层面一次性领取：跨操作、跨会话复用一律拒绝。

    多个 :class:`Session` 共享同一个 :class:`Transcript` 与
    :class:`TripleBank`，因此可以把两个会话的消息交错投递后再各自打开、
    重放核验。
    """

    def __init__(self, session_id: str, transcript: Transcript):
        check_id(session_id, "session_id")
        if not isinstance(transcript, Transcript):
            raise TypeError(
                f"transcript must be a Transcript, got {type(transcript).__name__}"
            )
        self._session_id = session_id
        self._transcript = transcript

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transcript(self) -> Transcript:
        return self._transcript

    # ---- 内部路由 -----------------------------------------------------

    def _route(self, message: Message) -> Message:
        if message.session_id != self._session_id:
            raise SessionMismatchError(self._session_id, message.session_id)
        return message

    # ---- 投递 ---------------------------------------------------------

    def add_message(self, message: Message) -> str:
        """投递一条消息到本会话（语义见类文档）。"""
        self._route(message)
        return self._transcript._deliver(message, _ABORT)

    def add_messages(self, messages) -> list[str]:
        """批量投递到本会话；任一会话不符即整体拒绝（无状态变更）。

        批次中出现冲突重复时，该操作按题面进入 aborted 终态（三元组烧毁，
        不回滚）；本批次对其他操作的变更全部回滚，不存在半更新。
        """
        seq = list(messages)
        for i, m in enumerate(seq):
            if not isinstance(m, Message):
                raise TypeError(f"messages[{i}] must be a Message, got {type(m).__name__}")
            if m.session_id != self._session_id:
                raise SessionMismatchError(self._session_id, m.session_id)
        return self._transcript._deliver_many(seq, _ABORT)

    # ---- 打开与观察 ---------------------------------------------------

    def open(self, op_id: str) -> OperationResult:
        check_id(op_id, "op_id")
        return self._transcript.open(op_id, session_id=self._session_id)

    def is_complete(self, op_id: str) -> bool:
        return self._transcript.is_complete(op_id, session_id=self._session_id)

    def is_open(self, op_id: str) -> bool:
        return self._transcript.is_open(op_id, session_id=self._session_id)

    def is_aborted(self, op_id: str) -> bool:
        return self._transcript.is_aborted(op_id, session_id=self._session_id)

    def abort_reason(self, op_id: str) -> str | None:
        return self._transcript.abort_reason(op_id, session_id=self._session_id)

    def missing_parties(self, op_id: str) -> tuple[int, ...]:
        return self._transcript.missing_parties(op_id, session_id=self._session_id)

    def result(self, op_id: str) -> OperationResult:
        return self._transcript.result(op_id, session_id=self._session_id)

    def operations_count(self) -> int:
        """本会话缓冲中的操作数（含已打开与已中止）。"""
        return sum(
            1 for (sid, _) in self._transcript._ops if sid == self._session_id
        )

    def messages_count(self) -> int:
        """本会话已缓冲的消息条数（含中止操作中已落盘的消息）。"""
        return sum(
            len(op.messages)
            for (sid, _), op in self._transcript._ops.items()
            if sid == self._session_id
        )

    def aborted_operations(self) -> tuple[str, ...]:
        return self._transcript.aborted_operations(session_id=self._session_id)


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

    返回 ``(x_shares, y_shares, messages)``。消息绑定到 ``session_id``
    （缺省默认会话）。不直接投递，调用方可自行乱序/重复/篡改后投递，
    以测试会话缓冲与中止行为。
    """
    x_shares = random_share(x, rng)
    y_shares = random_share(y, rng)
    messages = derive_messages(
        op_id, triple, x_shares, y_shares, session_id=session_id
    )
    return x_shares, y_shares, messages
