"""离线转录模拟的协议核心：参与方、消息、转录板与打开。"""

from dataclasses import dataclass

from .errors import (
    FieldValueError,
    IncompleteTranscriptError,
    MessageConflictError,
    ProtocolStateError,
    TripleReuseError,
    UnknownPartyError,
)
from .field import P, check_field_element
from .sharing import NUM_PARTIES
from .triples import MAX_ID_LEN, TripleShare

_D = "d"
_E = "e"
_KINDS = (_D, _E)


def check_identifier(value, name):
    """op_id / triple_id：非空 str，长度不超过 MAX_ID_LEN。"""
    if isinstance(value, bool) or not isinstance(value, str):
        raise FieldValueError(f"{name}: 必须是 str，得到 {type(value).__name__}: {value!r}")
    if not value:
        raise FieldValueError(f"{name}: 不能为空字符串")
    if len(value) > MAX_ID_LEN:
        raise FieldValueError(f"{name}: 长度 {len(value)} 超过上限 {MAX_ID_LEN}")
    return value


def check_party_index(index, num_parties, name="party"):
    """参与方编号：非 bool 的 int，且在 [0, num_parties)。"""
    if isinstance(index, bool) or not isinstance(index, int):
        raise FieldValueError(f"{name}: 必须是 int，得到 {type(index).__name__}: {index!r}")
    if not 0 <= index < num_parties:
        raise FieldValueError(f"{name}: 参与方编号 {index} 越界，须在 [0, {num_parties})")
    return index


@dataclass(frozen=True)
class Message:
    """转录板上的一条广播消息：某操作中某方公布的 d_i 或 e_i。"""

    op_id: str
    sender: int
    kind: str  # "d" 或 "e"
    value: int

    def __post_init__(self):
        check_identifier(self.op_id, "op_id")
        # 线缆记录只校验发送方是“非负 int”；是否为本次转录的合法参与方
        # 由 Transcript 按其参与方集合判定（UnknownPartyError）。
        if isinstance(self.sender, bool) or not isinstance(self.sender, int):
            raise FieldValueError(
                f"sender: 必须是 int，得到 {type(self.sender).__name__}: {self.sender!r}"
            )
        if not 0 <= self.sender < P:
            raise FieldValueError(f"sender: 编号 {self.sender} 越界，须在 [0, {P})")
        if self.kind not in _KINDS:
            raise FieldValueError(f"kind: 必须是 'd' 或 'e'，得到 {self.kind!r}")
        check_field_element(self.value, "value")


class Party:
    """一个参与方的本地状态：持有的三元组份额与本操作的局部计算。"""

    def __init__(self, index):
        self.index = check_party_index(index, NUM_PARTIES)
        self._triples = {}
        self._used_triples = set()
        # op_id -> (triple_id, d_i, e_i)
        self._contributions = {}

    def receive_triple(self, share):
        """接收离线阶段分配的三元组份额。同 id 同份额幂等，不一致拒绝。"""
        if not isinstance(share, TripleShare):
            raise FieldValueError(f"share: 必须是 TripleShare，得到 {type(share).__name__}")
        check_identifier(share.triple_id, "triple_id")
        if share.triple_id in self._triples:
            if self._triples[share.triple_id] != share:
                raise MessageConflictError(
                    f"参与方 {self.index} 已持有 triple_id {share.triple_id!r} 的不同份额"
                )
            return
        self._triples[share.triple_id] = share

    def contribute(self, op_id, triple_id, x_share, y_share):
        """由本地输入份额 x_i、y_i 生成 d_i=x_i-a_i、e_i=y_i-b_i 两条消息。

        同一 (op_id, 相同参数) 重复调用幂等；同一 op_id 换参数拒绝；
        同一 triple_id 用于第二个 op_id 视为复用并拒绝。
        """
        check_identifier(op_id, "op_id")
        check_identifier(triple_id, "triple_id")
        check_field_element(x_share, "x_share")
        check_field_element(y_share, "y_share")

        previous = self._contributions.get(op_id)
        if previous is not None:
            old_triple, old_d, old_e = previous
            if old_triple != triple_id:
                raise ProtocolStateError(
                    f"op_id {op_id!r} 已绑定 triple_id {old_triple!r}，不能改绑 {triple_id!r}"
                )
            d_i = (x_share - self._triples[triple_id].a) % P
            e_i = (y_share - self._triples[triple_id].b) % P
            if (d_i, e_i) != (old_d, old_e):
                raise MessageConflictError(
                    f"参与方 {self.index} 对 op_id {op_id!r} 的贡献与已生成消息冲突"
                )
            return [
                Message(op_id, self.index, _D, old_d),
                Message(op_id, self.index, _E, old_e),
            ]

        if triple_id in self._used_triples:
            raise TripleReuseError(
                f"参与方 {self.index} 的 triple_id {triple_id!r} 已用于另一操作，拒绝复用"
            )
        share = self._triples.get(triple_id)
        if share is None:
            raise ProtocolStateError(
                f"参与方 {self.index} 未持有 triple_id {triple_id!r} 的份额"
            )

        d_i = (x_share - share.a) % P
        e_i = (y_share - share.b) % P
        self._contributions[op_id] = (triple_id, d_i, e_i)
        self._used_triples.add(triple_id)
        return [
            Message(op_id, self.index, _D, d_i),
            Message(op_id, self.index, _E, e_i),
        ]


@dataclass(frozen=True)
class PartyView:
    """打开后单个参与方可见的全部信息。"""

    party: int
    triple_id: str
    a_i: int
    b_i: int
    c_i: int
    d_i: int
    e_i: int
    d: int
    e: int
    z_i: int


@dataclass(frozen=True)
class OpResult:
    """一次乘法转录打开的结果：各方视图与重构积。"""

    op_id: str
    triple_id: str
    d: int
    e: int
    views: tuple
    z: int


class Transcript:
    """中心化的转录板（教学模拟）：收集消息，收齐后才能打开。

    - 消息按 (op_id, sender, kind) 去重：一致重复幂等，不一致冲突拒绝；
    - 冲突/非法消息不会改变转录板状态；
    - post_bulk 整体校验通过后才落盘，失败不留部分变更；
    - 打开时必须三方 d、e 共 6 条齐备，否则抛 IncompleteTranscriptError。
    """

    def __init__(self, parties):
        parties = list(parties)
        if not parties:
            raise FieldValueError("parties: 至少需要一个参与方")
        indices = []
        for party in parties:
            if not isinstance(party, Party):
                raise FieldValueError(f"parties: 必须是 Party 实例，得到 {type(party).__name__}")
            indices.append(party.index)
        if len(set(indices)) != len(indices):
            raise FieldValueError(f"parties: 参与方编号重复 {sorted(indices)}")
        required = set(range(NUM_PARTIES))
        if set(indices) != required:
            raise FieldValueError(
                f"parties: 本题固定 {NUM_PARTIES} 方且编号须恰为 {sorted(required)}，"
                f"得到 {sorted(indices)}"
            )
        self._parties = {p.index: p for p in parties}
        # op_id -> triple_id
        self._ops = {}
        # (op_id, sender, kind) -> value
        self._messages = {}

    @property
    def parties(self):
        return tuple(sorted(self._parties))

    def begin(self, op_id, triple_id):
        """登记一个乘法操作。同参重复登记幂等，同 op 改绑 triple 拒绝。"""
        check_identifier(op_id, "op_id")
        check_identifier(triple_id, "triple_id")
        existing = self._ops.get(op_id)
        if existing is not None and existing != triple_id:
            raise ProtocolStateError(
                f"op_id {op_id!r} 已绑定 triple_id {existing!r}，不能改绑 {triple_id!r}"
            )
        self._ops[op_id] = triple_id

    def post(self, message):
        """投递一条消息；返回 True 表示新收录，False 表示幂等重复。"""
        planned = self._plan([message])
        self._messages.update(planned)
        return bool(planned)

    def post_bulk(self, messages):
        """批量投递：全部合法且互不冲突时才一次性收录，否则状态不变。"""
        messages = list(messages)
        planned = self._plan(messages)
        self._messages.update(planned)
        return len(planned)

    def _plan(self, messages):
        """模拟一次收录：返回可新增的 {key: value}；任何问题直接报错不写状态。"""
        candidate = dict(self._messages)
        planned = {}
        for message in messages:
            if not isinstance(message, Message):
                raise FieldValueError(
                    f"message: 必须是 Message，得到 {type(message).__name__}"
                )
            op_id = message.op_id
            if op_id not in self._ops:
                raise ProtocolStateError(f"op_id {op_id!r} 尚未 begin，不能投递消息")
            if message.sender not in self._parties:
                raise UnknownPartyError(
                    f"op_id {op_id!r}: 未知参与方 {message.sender}，"
                    f"合法集合为 {sorted(self._parties)}"
                )
            key = (op_id, message.sender, message.kind)
            old = candidate.get(key)
            if old is not None and old != message.value:
                raise MessageConflictError(
                    f"op_id {op_id!r} 参与方 {message.sender} 的 {message.kind} 消息冲突: "
                    f"已记录 {old}，新消息 {message.value}"
                )
            if old is None:
                candidate[key] = message.value
                planned[key] = message.value
        return planned

    def is_complete(self, op_id):
        """该操作的三方 d/e 消息是否已收齐。"""
        return not self.missing(op_id)

    def missing(self, op_id):
        """返回尚缺的 (sender, kind) 列表，供定位 IncompleteTranscriptError。"""
        check_identifier(op_id, "op_id")
        if op_id not in self._ops:
            raise ProtocolStateError(f"op_id {op_id!r} 尚未 begin")
        return [
            (sender, kind)
            for sender in self._parties
            for kind in _KINDS
            if (op_id, sender, kind) not in self._messages
        ]

    def open(self, op_id):
        """收齐后打开：计算公开 d、e，各方输出份额并重构乘积。"""
        check_identifier(op_id, "op_id")
        triple_id = self._ops.get(op_id)
        if triple_id is None:
            raise ProtocolStateError(f"op_id {op_id!r} 尚未 begin")
        missing = self.missing(op_id)
        if missing:
            raise IncompleteTranscriptError(
                f"op_id {op_id!r} 消息未收齐，缺少 {missing}"
            )

        d = sum(self._messages[(op_id, i, _D)] for i in self._parties) % P
        e = sum(self._messages[(op_id, i, _E)] for i in self._parties) % P

        views = []
        for i in sorted(self._parties):
            party = self._parties[i]
            share = party._triples.get(triple_id)
            if share is None:
                raise ProtocolStateError(
                    f"参与方 {i} 缺少 triple_id {triple_id!r} 的份额，无法打开"
                )
            d_i = self._messages[(op_id, i, _D)]
            e_i = self._messages[(op_id, i, _E)]
            z_i = (share.c + d * share.b + e * share.a) % P
            if i == 0:
                # 常量 d*e 按契约只加给 0 号方。
                z_i = (z_i + d * e) % P
            views.append(
                PartyView(
                    party=i,
                    triple_id=triple_id,
                    a_i=share.a,
                    b_i=share.b,
                    c_i=share.c,
                    d_i=d_i,
                    e_i=e_i,
                    d=d,
                    e=e,
                    z_i=z_i,
                )
            )

        z = sum(v.z_i for v in views) % P
        return OpResult(
            op_id=op_id,
            triple_id=triple_id,
            d=d,
            e=e,
            views=tuple(views),
            z=z,
        )
