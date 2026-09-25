"""Beaver 乘法的离线转录模拟（协议核心）。

执行模型（带“上帝视角”的本地模拟器，不模拟任何网络）：

1. :class:`TripleBank` 登记显式合成的 Beaver 三元组，并维护每个
   ``triple_id`` 的一次性生命周期：``free -> reserved(op) -> consumed``。
2. 每个参与方把 :class:`~sss_mult.shares.Message` 投递给
   :class:`Transcript`。消息键为 ``(op_id, party)``：
   重复且一致 → 幂等忽略；任一字段不一致 → :class:`ConflictError`。
3. 同一操作的消息必须引用相同 ``triple_id``，否则
   :class:`TripleMismatchError`。三元组被某操作预留后，其他操作不得使用，
   打开后即永久消费（:class:`TripleReusedError`）。
4. 三方消息全部到齐才能 :meth:`Transcript.open`；缺方抛
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
    TranscriptError,
    TripleMismatchError,
    TripleReusedError,
    UnknownTripleError,
)
from .errors import FieldValueError
from .field import (
    MAX_DOT_PRODUCT_LEN,
    MAX_MESSAGES,
    MAX_OPERATIONS,
    N_PARTIES,
    P,
)
from .shares import (
    BeaverTriple,
    DotMessage,
    Message,
    check_id,
    derive_dot_messages,
    derive_messages,
    random_share,
    reconstruct,
)


class UnknownOperationError(TranscriptError, ValueError):
    """打开了一个从未投递过消息的 ``op_id``。"""

    def __init__(self, op_id: str):
        super().__init__(f"unknown op_id {op_id!r}: no message has been delivered")
        self.op_id = op_id


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

    def output_shares(self) -> tuple[int, int, int]:
        """按参与方编号返回输出份额 ``(z_0, z_1, z_2)``。"""
        return tuple(v.z_i for v in self.views)  # type: ignore[return-value]


class TripleBank:
    """Beaver 三元组注册表，负责 ``triple_id`` 的一次性生命周期。"""

    def __init__(self):
        self._triples: dict[str, BeaverTriple] = {}
        # triple_id -> None(free) / ("reserved", op_id) / ("consumed", op_id)
        self._state: dict[str, tuple[str, str] | None] = {}

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
        """返回生命周期状态：``"free" | "reserved" | "consumed"``。"""
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        return "free" if st is None else st[0]

    def reserve(self, triple_id: str, op_id: str) -> None:
        """为操作预留三元组；同操作重复预留幂等。"""
        if triple_id not in self._state:
            raise UnknownTripleError(triple_id)
        st = self._state[triple_id]
        if st is None:
            self._state[triple_id] = ("reserved", op_id)
        elif st[0] == "reserved":
            if st[1] != op_id:
                raise TripleReusedError(triple_id, op_id, st[1], consumed=False)
            # 同一操作重复预留：幂等。
        else:  # consumed
            raise TripleReusedError(triple_id, op_id, st[1], consumed=True)

    def release(self, triple_id: str, op_id: str) -> None:
        """撤销某操作的预留（回滚用）；非该操作持有时不动。"""
        st = self._state.get(triple_id)
        if st is not None and st == ("reserved", op_id):
            self._state[triple_id] = None

    def consume(self, triple_id: str, op_id: str) -> None:
        st = self._state.get(triple_id)
        if st != ("reserved", op_id):
            # 到达这里只能说明本库内部状态机出错。
            raise TripleReusedError(
                triple_id, op_id, st[1] if st else "<none>", consumed=st is not None and st[0] == "consumed"
            )
        self._state[triple_id] = ("consumed", op_id)


@dataclass
class _Op:
    triple_id: str
    messages: dict[int, Message]
    opened: bool = False
    result: OperationResult | None = None


class Transcript:
    """转录机：收集消息、做一致性检查、满足条件后打开。

    所有变更方法都是原子的：校验全部通过后才落盘状态；批次方法
    :meth:`add_messages` 失败时回滚本批次已做的全部变更（含三元组预留）。
    """

    def __init__(
        self,
        bank: TripleBank,
        *,
        max_operations: int = MAX_OPERATIONS,
        max_messages: int = MAX_MESSAGES,
        max_dot_product_len: int = MAX_DOT_PRODUCT_LEN,
    ):
        if isinstance(max_operations, bool) or type(max_operations) is not int:
            raise TypeError("max_operations must be an int (bool not accepted)")
        if isinstance(max_messages, bool) or type(max_messages) is not int:
            raise TypeError("max_messages must be an int (bool not accepted)")
        if isinstance(max_dot_product_len, bool) or type(max_dot_product_len) is not int:
            raise TypeError("max_dot_product_len must be an int (bool not accepted)")
        if max_operations <= 0 or max_messages <= 0:
            raise ValueError("max_operations/max_messages must be positive")
        if max_dot_product_len < 0:
            raise ValueError("max_dot_product_len must be non-negative")
        self._bank = bank
        self._max_operations = max_operations
        self._max_messages = max_messages
        self._max_dot_product_len = max_dot_product_len
        self._ops: dict[str, _Op] = {}
        self._dot_ops: dict[str, _DotOp] = {}

    # ---- 只读观察接口 -------------------------------------------------

    @property
    def bank(self) -> TripleBank:
        return self._bank

    def is_complete(self, op_id: str) -> bool:
        """三方消息是否全部到齐（未投递过的操作返回 ``False``）。"""
        check_id(op_id, "op_id")
        op = self._ops.get(op_id)
        return op is not None and len(op.messages) == N_PARTIES

    def is_open(self, op_id: str) -> bool:
        check_id(op_id, "op_id")
        op = self._ops.get(op_id)
        return op is not None and op.opened

    def missing_parties(self, op_id: str) -> tuple[int, ...]:
        """尚未投递消息的参与方编号（升序）；未知操作返回全部三方。"""
        check_id(op_id, "op_id")
        op = self._ops.get(op_id)
        have = set() if op is None else set(op.messages)
        return tuple(i for i in range(N_PARTIES) if i not in have)

    def operations_count(self) -> int:
        return len(self._ops) + len(self._dot_ops)

    def messages_count(self) -> int:
        return sum(len(op.messages) for op in self._ops.values()) + sum(
            len(op.messages) for op in self._dot_ops.values()
        )

    def result(self, op_id: str) -> OperationResult:
        """返回已打开操作的结果；未打开抛 :class:`TranscriptError`。"""
        check_id(op_id, "op_id")
        op = self._ops.get(op_id)
        if op is None:
            raise UnknownOperationError(op_id)
        if not op.opened:
            raise TranscriptError(f"op {op_id!r} has not been opened yet")
        return op.result  # type: ignore[return-value]

    # ---- 消息投递 -----------------------------------------------------

    def add_message(self, message: Message) -> str:
        """投递一条消息。

        :returns: ``"accepted"``（新消息）或 ``"duplicate"``（重复一致，幂等）。
        :raises TypeError: ``message`` 不是 :class:`Message`（其字段在构造时
            已校验，非法消息无法构造）。
        :raises TripleMismatchError / ConflictError / TripleReusedError /
            LimitError: 见模块文档；这些错误均不改变转录状态。
        """
        if not isinstance(message, Message):
            raise TypeError(
                f"message must be a Message, got {type(message).__name__}"
            )
        op = self._ops.get(message.op_id)
        if op is None:
            # 新操作：先做全部前置校验，再落盘。
            if len(self._ops) >= self._max_operations:
                raise LimitError(
                    f"operation limit reached: max_operations={self._max_operations}"
                )
            if self.messages_count() >= self._max_messages:
                raise LimitError(f"message limit reached: max_messages={self._max_messages}")
            # reserve 可能抛 TripleReusedError / UnknownTripleError，均在落盘前。
            self._bank.reserve(message.triple_id, message.op_id)
            self._ops[message.op_id] = _Op(message.triple_id, {message.party: message})
            return "accepted"

        # 既有操作：三元组引用必须一致。
        if message.triple_id != op.triple_id:
            raise TripleMismatchError(
                f"op {message.op_id!r} already uses triple {op.triple_id!r}, "
                f"refusing message from party {message.party} referencing "
                f"{message.triple_id!r}"
            )
        existing = op.messages.get(message.party)
        if existing is not None:
            if existing == message:
                return "duplicate"
            raise ConflictError(
                f"conflicting message for (op={message.op_id!r}, party={message.party}): "
                f"stored triple={existing.triple_id!r}, d={existing.d}, e={existing.e}; "
                f"got triple={message.triple_id!r}, d={message.d}, e={message.e}"
            )
        if self.messages_count() >= self._max_messages:
            raise LimitError(f"message limit reached: max_messages={self._max_messages}")
        op.messages[message.party] = message
        return "accepted"

    def add_messages(self, messages) -> list[str]:
        """批量投递；任一条失败则回滚本批次全部已应用变更。

        返回与输入等长、等序的 ``"accepted" | "duplicate"`` 列表。
        """
        seq = list(messages)
        for i, m in enumerate(seq):
            if not isinstance(m, Message):
                raise TypeError(f"messages[{i}] must be a Message, got {type(m).__name__}")

        created_ops: list[str] = []
        inserted: list[tuple[str, int]] = []
        results: list[str] = []
        try:
            for m in seq:
                before_op = m.op_id in self._ops
                status = self.add_message(m)
                if not before_op:
                    created_ops.append(m.op_id)
                if status == "accepted":
                    inserted.append((m.op_id, m.party))
                results.append(status)
        except Exception:
            # 逆序回滚：删消息、删操作、释放预留。
            for op_id, party in reversed(inserted):
                self._ops[op_id].messages.pop(party, None)
            for op_id in reversed(created_ops):
                op = self._ops.pop(op_id, None)
                if op is not None:
                    self._bank.release(op.triple_id, op_id)
            raise
        return results

    # ---- 打开 ---------------------------------------------------------

    def open(self, op_id: str) -> OperationResult:
        """齐三方后打开操作并计算各方视图；重复打开返回缓存结果（幂等）。"""
        check_id(op_id, "op_id")
        op = self._ops.get(op_id)
        if op is None:
            raise UnknownOperationError(op_id)
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
        )
        # 计算完成后再消费并落盘。
        self._bank.consume(op.triple_id, op_id)
        op.opened = True
        op.result = result
        return result

    # ---- 点积 -----------------------------------------------------------

    def begin_dot_product(self, op_id: str, triple_ids) -> int:
        """声明一个点积操作：先完整验证，再整批预留全部三元组（原子）。

        验证顺序（全部通过前不改变任何状态）：

        1. ``op_id`` 合法且未被任何乘法/点积操作占用；
        2. 长度 ``len(triple_ids)`` 不超过 ``max_dot_product_len``（可为 0）；
        3. 每个 ``triple_id`` 合法、批内无重复、已注册且当前空闲。

        全部通过后一次性预留整批并创建操作；此后这些三元组**永不释放**——
        即使操作中途失败终止，也不能重新用于另一秘密运算。

        :returns: 点积长度。
        """
        check_id(op_id, "op_id")
        if op_id in self._ops or op_id in self._dot_ops:
            raise ConflictError(f"op_id {op_id!r} is already in use")
        triple_ids = tuple(triple_ids)
        if len(triple_ids) > self._max_dot_product_len:
            raise LimitError(
                f"dot product length {len(triple_ids)} exceeds "
                f"max_dot_product_len={self._max_dot_product_len}"
            )
        for k, tid in enumerate(triple_ids):
            check_id(tid, f"triple_ids[{k}]")
        # 批内重复：在任何预留之前发现。
        seen: set[str] = set()
        for tid in triple_ids:
            if tid in seen:
                raise ConflictError(
                    f"duplicate triple_id {tid!r} within dot product batch {op_id!r}"
                )
            seen.add(tid)
        # 可用性：全部已注册且空闲（任一不满足则整批不预留）。
        for tid in triple_ids:
            self._bank.get(tid)  # UnknownTripleError
            st = self._bank._state[tid]
            if st is not None:
                raise TripleReusedError(tid, op_id, st[1], consumed=st[0] == "consumed")
        if len(self._ops) + len(self._dot_ops) >= self._max_operations:
            raise LimitError(
                f"operation limit reached: max_operations={self._max_operations}"
            )
        # 验证全部通过：整批预留（预留本身此时不可能失败）。
        for tid in triple_ids:
            self._bank.reserve(tid, op_id)
        self._dot_ops[op_id] = _DotOp(op_id, triple_ids, {}, [])
        return len(triple_ids)


    def add_dot_message(self, message: DotMessage) -> str:
        """投递一条点积消息；语义与 :meth:`Transcript.add_message` 对齐。

        消息键为 ``(op_id, pos, party)``：重复一致幂等；冲突拒绝并把冲突
        消息记入该操作的失败证据（可通过 ``dot_conflicts`` 读取）。
        """
        if not isinstance(message, DotMessage):
            raise TypeError(
                f"message must be a DotMessage, got {type(message).__name__}"
            )
        op = self._dot_ops.get(message.op_id)
        if op is None:
            raise UnknownOperationError(message.op_id)
        if op.opened:
            raise TranscriptError(
                f"dot product op {message.op_id!r} is already opened"
            )
        if message.pos >= len(op.triple_ids):
            raise FieldValueError(
                f"pos={message.pos} out of range for dot product {message.op_id!r} "
                f"of length {len(op.triple_ids)}"
            )
        expected_tid = op.triple_ids[message.pos]
        if message.triple_id != expected_tid:
            raise TripleMismatchError(
                f"op {message.op_id!r} pos {message.pos} uses triple "
                f"{expected_tid!r}, refusing message from party {message.party} "
                f"referencing {message.triple_id!r}"
            )
        key = (message.pos, message.party)
        existing = op.messages.get(key)
        if existing is not None:
            if existing == message:
                return "duplicate"
            op.conflicts.append(message)  # 保留失败证据
            raise ConflictError(
                f"conflicting dot message for (op={message.op_id!r}, "
                f"pos={message.pos}, party={message.party}): stored "
                f"d={existing.d}, e={existing.e}; got d={message.d}, e={message.e}"
            )
        if self.messages_count() >= self._max_messages:
            raise LimitError(f"message limit reached: max_messages={self._max_messages}")
        op.messages[key] = message
        return "accepted"


    def add_dot_messages(self, messages) -> list[str]:
        """批量投递点积消息；任一条失败则回滚本批次已插入的消息。

        注意：冲突消息仍会留在失败证据中（它本来就没有改变协议状态）。
        """
        seq = list(messages)
        for i, m in enumerate(seq):
            if not isinstance(m, DotMessage):
                raise TypeError(
                    f"messages[{i}] must be a DotMessage, got {type(m).__name__}"
                )
        inserted: list[tuple[str, tuple[int, int]]] = []
        results: list[str] = []
        try:
            for m in seq:
                status = self.add_dot_message(m)
                if status == "accepted":
                    inserted.append((m.op_id, (m.pos, m.party)))
                results.append(status)
        except Exception:
            for op_id, key in reversed(inserted):
                self._dot_ops[op_id].messages.pop(key, None)
            raise
        return results


    def open_dot(self, op_id: str) -> DotProductResult:
        """所有位置三方齐全后打开整个点积，**只发布一次**总结果。

        任一位置缺方都抛 :class:`IncompleteTranscriptError`（``missing``
        为 ``(pos, party)`` 二元组），且不改变任何状态、不消费三元组。
        重复打开返回缓存结果（幂等）。
        """
        check_id(op_id, "op_id")
        op = self._dot_ops.get(op_id)
        if op is None:
            raise UnknownOperationError(op_id)
        if op.opened:
            return op.result  # type: ignore[return-value]

        length = len(op.triple_ids)
        missing = tuple(
            (pos, party)
            for pos in range(length)
            for party in range(N_PARTIES)
            if (pos, party) not in op.messages
        )
        if missing:
            raise IncompleteTranscriptError(op_id, missing)

        positions: list[DotPositionResult] = []
        party_sums = [0, 0, 0]
        for pos in range(length):
            triple = self._bank.get(op.triple_ids[pos])
            msgs = [op.messages[(pos, i)] for i in range(N_PARTIES)]
            d = sum(m.d for m in msgs) % P
            e = sum(m.e for m in msgs) % P
            de = (d * e) % P
            z_sum = 0
            for i in range(N_PARTIES):
                a_i, b_i, c_i = triple.party_view(i)
                z_i = (c_i + d * b_i + e * a_i + (de if i == 0 else 0)) % P
                party_sums[i] = (party_sums[i] + z_i) % P
                z_sum = (z_sum + z_i) % P
            positions.append(DotPositionResult(pos, triple.triple_id, d, e, z_sum))

        result = DotProductResult(
            op_id=op_id,
            length=length,
            positions=tuple(positions),
            party_sums=tuple(party_sums),  # type: ignore[arg-type]
            value=reconstruct(party_sums),
        )
        # 全部计算完成后才消费整批三元组并落盘。
        for tid in op.triple_ids:
            self._bank.consume(tid, op_id)
        op.opened = True
        op.result = result
        return result


    def dot_conflicts(self, op_id: str) -> tuple[DotMessage, ...]:
        """返回该点积操作被拒绝的冲突消息（失败证据，只读）。"""
        check_id(op_id, "op_id")
        op = self._dot_ops.get(op_id)
        if op is None:
            raise UnknownOperationError(op_id)
        return tuple(op.conflicts)


    def is_dot_complete(self, op_id: str) -> bool:
        """点积所有位置的三方消息是否全部到齐（未声明返回 ``False``）。"""
        check_id(op_id, "op_id")
        op = self._dot_ops.get(op_id)
        if op is None:
            return False
        return len(op.messages) == len(op.triple_ids) * N_PARTIES


    def dot_product(
        self: Transcript,
        op_id: str,
        triple_ids,
        x_shares_list,
        y_shares_list,
    ) -> DotProductResult:
        """一站式诚实点积：声明操作 → 由分享派生消息 → 投递 → 打开。

        ``x_shares_list[k]``、``y_shares_list[k]`` 是第 ``k`` 个位置的
        三方加法分享；``triple_ids[k]`` 独占该位置的乘法。空列表返回零值
        结果且不消耗任何三元组。需要乱序/冲突等交错行为时，请改用
        ``begin_dot_product`` + ``add_dot_message(s)`` + ``open_dot``。
        """
        triple_ids = tuple(triple_ids)
        x_shares_list = tuple(x_shares_list)
        y_shares_list = tuple(y_shares_list)
        if not (len(triple_ids) == len(x_shares_list) == len(y_shares_list)):
            raise FieldValueError(
                f"triple_ids/x_shares_list/y_shares_list length mismatch: "
                f"{len(triple_ids)}/{len(x_shares_list)}/{len(y_shares_list)}"
            )
        self.begin_dot_product(op_id, triple_ids)
        triples = [self._bank.get(tid) for tid in triple_ids]
        messages = derive_dot_messages(op_id, triples, x_shares_list, y_shares_list)
        self.add_dot_messages(messages)
        return self.open_dot(op_id)


def honest_prepare(op_id: str, triple: BeaverTriple, x: int, y: int, rng):
    """诚实模拟的语法糖：对显式输入 ``x, y`` 生成分享与三条打开消息。

    返回 ``(x_shares, y_shares, messages)``。不直接投递，调用方可自行
    乱序/重复/篡改后投递，以测试转录机行为。
    """
    x_shares = random_share(x, rng)
    y_shares = random_share(y, rng)
    messages = derive_messages(op_id, triple, x_shares, y_shares)
    return x_shares, y_shares, messages


# ---------------------------------------------------------------------------
# 点积（一批独占三元组的乘法门，一次发布）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DotPositionResult:
    """点积中单个位置（一个乘法门）的打开结果。"""

    pos: int
    triple_id: str
    d: int
    e: int
    product: int  # 该位置重构乘积 x_pos * y_pos mod p


@dataclass(frozen=True)
class DotProductResult:
    """整个点积的唯一一次发布结果（模拟器视角）。"""

    op_id: str
    length: int
    positions: tuple[DotPositionResult, ...]
    party_sums: tuple[int, int, int]  # 各方把自己各位置的 z_i 相加后的总份额
    value: int  # 重构点积 = Σ party_sums = Σ x_pos*y_pos (mod p)

    def output_shares(self) -> tuple[int, int, int]:
        """按参与方编号返回点积输出总份额。"""
        return self.party_sums


@dataclass
class _DotOp:
    op_id: str
    triple_ids: tuple[str, ...]
    messages: dict[tuple[int, int], DotMessage]  # (pos, party) -> message
    conflicts: list[DotMessage]  # 被拒绝的冲突消息（失败证据，保留）
    opened: bool = False
    result: DotProductResult | None = None


def dot_product_prepare(op_id: str, triples, x_values, y_values, rng):
    """诚实模拟的语法糖：对显式输入向量生成分享与全部点积打开消息。

    返回 ``(x_shares_list, y_shares_list, messages)``。不直接投递。
    """
    x_values = tuple(x_values)
    y_values = tuple(y_values)
    triples = tuple(triples)
    if not (len(triples) == len(x_values) == len(y_values)):
        raise FieldValueError(
            f"triples/x_values/y_values length mismatch: "
            f"{len(triples)}/{len(x_values)}/{len(y_values)}"
        )
    x_shares_list = [random_share(x, rng) for x in x_values]
    y_shares_list = [random_share(y, rng) for y in y_values]
    messages = derive_dot_messages(op_id, triples, x_shares_list, y_shares_list)
    return x_shares_list, y_shares_list, messages
