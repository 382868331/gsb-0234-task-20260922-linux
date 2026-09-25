"""加法分享、Beaver 三元组与协议消息。

三方加法分享：秘密 ``s`` 被拆成 ``(s0, s1, s2)``，满足
``s0 + s1 + s2 == s (mod p)``。分享值本身不携带“归属秘密”的类型信息，
就是域元素三元组。

Beaver 三元组 ``(a, b, c)`` 满足 ``c == a*b (mod p)``，三个秘密各自做
加法分享，每个参与方 ``i`` 持有 ``(a_i, b_i, c_i)``。

打开阶段广播的消息只携带参与方自己的 ``d_i = x_i - a_i``、
``e_i = y_i - b_i``（见 :class:`Message`）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import field
from .errors import FieldTypeError, FieldValueError, IdentifierError
from .field import MAX_DOT_PRODUCT_LEN, MAX_ID_LENGTH, N_PARTIES, P, require_element


def check_id(value, what: str = "id") -> str:
    """校验操作 ID / 三元组 ID：非空 ``str``，长度不超过上限。"""
    if not isinstance(value, str):
        raise FieldTypeError(f"{what} must be a str, got {type(value).__name__}: {value!r}")
    if len(value) == 0:
        raise IdentifierError(f"{what} must be a non-empty string")
    if len(value) > MAX_ID_LENGTH:
        raise IdentifierError(
            f"{what} length {len(value)} exceeds MAX_ID_LENGTH={MAX_ID_LENGTH}"
        )
    return value


def check_party(value) -> int:
    """校验参与方编号：0/1/2 的严格整数（``bool`` 拒绝）。"""
    if isinstance(value, bool) or type(value) is not int:
        raise FieldTypeError(
            f"party must be an int in [0, {N_PARTIES - 1}] (bool not accepted), "
            f"got {type(value).__name__}: {value!r}"
        )
    if not (0 <= value < N_PARTIES):
        raise FieldValueError(
            f"party={value} out of range [0, {N_PARTIES - 1}]"
        )
    return value


def split_share(secret, r1, r2):
    """用两个给定随机域元素构造三方加法分享。

    返回 ``(s0, s1, s2)``，其中 ``s1 = r1``、``s2 = r2``、
    ``s0 = secret - r1 - r2 (mod p)``。三个分量都是合法域元素。
    """
    s = require_element(secret, "secret")
    r1 = require_element(r1, "r1")
    r2 = require_element(r2, "r2")
    s1 = r1
    s2 = r2
    s0 = (s - s1 - s2) % P
    return (s0, s1, s2)


def random_share(secret, rng: random.Random):
    """用提供的 :class:`random.Random` 生成三方加法分享。

    调用方负责传入种子确定的 ``rng``（示例与测试均如此），以保证离线、
    可复现。
    """
    if not isinstance(rng, random.Random):
        raise FieldTypeError(
            f"rng must be a random.Random instance, got {type(rng).__name__}"
        )
    return split_share(secret, rng.randrange(P), rng.randrange(P))


def reconstruct(shares) -> int:
    """重构：对分享分量求和取模。允许只传部分分量（部分重构仍为域元素）。"""
    return field.sum_mod(shares)


@dataclass(frozen=True)
class BeaverTriple:
    """一个 Beaver 三元组及其三方分享视图。

    ``a_shares[i]``、``b_shares[i]``、``c_shares[i]`` 是参与方 ``i``
    持有的分量，分别重构出 ``a``、``b``、``c``，且 ``c == a*b mod p``。
    """

    triple_id: str
    a: int
    b: int
    c: int
    a_shares: tuple[int, int, int]
    b_shares: tuple[int, int, int]
    c_shares: tuple[int, int, int]

    def party_view(self, party: int) -> tuple[int, int, int]:
        """返回参与方 ``party`` 持有的 ``(a_i, b_i, c_i)``。"""
        check_party(party)
        return (self.a_shares[party], self.b_shares[party], self.c_shares[party])


def make_triple(triple_id: str, a: int, b: int, rng: random.Random) -> BeaverTriple:
    """显式合成一个 Beaver 三元组。

    :param triple_id: 三元组 ID，全局唯一、一次性消费。
    :param a: 三元组分量 ``a``（显式给定的域元素）。
    :param b: 三元组分量 ``b``；``c`` 在函数内按 ``a*b mod p`` 精确算出，
        不由调用方提供，以杜绝不一致。
    :param rng: 种子确定的随机源，用于生成三个秘密的加法分享。
    """
    check_id(triple_id, "triple_id")
    a = require_element(a, "a")
    b = require_element(b, "b")
    if not isinstance(rng, random.Random):
        raise FieldTypeError(
            f"rng must be a random.Random instance, got {type(rng).__name__}"
        )
    c = (a * b) % P
    return BeaverTriple(
        triple_id=triple_id,
        a=a,
        b=b,
        c=c,
        a_shares=random_share(a, rng),
        b_shares=random_share(b, rng),
        c_shares=random_share(c, rng),
    )


@dataclass(frozen=True)
class Message:
    """参与方在打开阶段广播的一条转录消息。

    字段全部不可变；``(op_id, party)`` 唯一标识一条消息。重复提交内容
    完全相同的消息是幂等的；任一字段不同即构成冲突。
    """

    op_id: str
    party: int
    triple_id: str
    d: int  # d_i = x_i - a_i mod p
    e: int  # e_i = y_i - b_i mod p

    def __post_init__(self):
        check_id(self.op_id, "op_id")
        check_party(self.party)
        check_id(self.triple_id, "triple_id")
        # require_element 会在赋值前校验；frozen dataclass 用 object.__setattr__
        # 不需要——这里只做校验，不做规范化（输入已须为合法域元素）。
        require_element(self.d, "d")
        require_element(self.e, "e")


def derive_messages(
    op_id: str,
    triple: BeaverTriple,
    x_shares,
    y_shares,
) -> tuple[Message, Message, Message]:
    """诚实参与方根据自己的输入分享合成三条打开消息。

    ``d_i = x_i - a_i mod p``、``e_i = y_i - b_i mod p``。该辅助函数
    只是消息构造的语法糖，协议核心不区分消息是否由它产生。
    """
    check_id(op_id, "op_id")
    if not isinstance(triple, BeaverTriple):
        raise FieldTypeError(
            f"triple must be a BeaverTriple, got {type(triple).__name__}"
        )
    x_shares = _check_share_tuple(x_shares, "x_shares")
    y_shares = _check_share_tuple(y_shares, "y_shares")
    msgs = []
    for i in range(N_PARTIES):
        d_i = (x_shares[i] - triple.a_shares[i]) % P
        e_i = (y_shares[i] - triple.b_shares[i]) % P
        msgs.append(Message(op_id, i, triple.triple_id, d_i, e_i))
    return tuple(msgs)


def _check_share_tuple(shares, what: str) -> tuple[int, int, int]:
    try:
        t = tuple(shares)
    except TypeError:
        raise FieldTypeError(f"{what} must be an iterable of 3 field elements") from None
    if len(t) != N_PARTIES:
        raise FieldValueError(f"{what} must have exactly {N_PARTIES} components, got {len(t)}")
    for i, v in enumerate(t):
        require_element(v, f"{what}[{i}]")
    return t  # type: ignore[return-value]


def check_position(value, length: int) -> int:
    """校验点积位置：``0 <= position < length`` 的严格整数。"""
    if isinstance(value, bool) or type(value) is not int:
        raise FieldTypeError(
            f"position must be an int (bool not accepted), got "
            f"{type(value).__name__}: {value!r}"
        )
    if not (0 <= value < length):
        raise FieldValueError(
            f"position={value} out of range for dot product of length {length} "
            f"[0, {length - 1}]"
        )
    return value


def check_share_vectors(x_shares, y_shares) -> tuple[tuple, tuple]:
    """校验点积的两组“按位置排列的三方分享向量”。

    形状契约：``x_shares`` 是长度 ``n`` 的序列，每个元素是一个长度 3 的
    域元素元组（位置 ``j`` 上三方各自持有的 ``x`` 分量）；``y_shares``
    同理。两者长度必须相等，且 ``0 <= n <= MAX_DOT_PRODUCT_LEN``。
    返回规范化后的两层元组（可为空序列）。
    """
    try:
        xs = tuple(x_shares)
    except TypeError:
        raise FieldTypeError("x_shares must be an iterable of 3-tuples") from None
    try:
        ys = tuple(y_shares)
    except TypeError:
        raise FieldTypeError("y_shares must be an iterable of 3-tuples") from None
    if isinstance(len(xs), bool):  # pragma: no cover - 元组长度恒为 int
        pass
    if len(xs) != len(ys):
        raise FieldValueError(
            f"x_shares and y_shares must have equal length, got "
            f"{len(xs)} and {len(ys)}"
        )
    n = len(xs)
    if type(n) is not int or n < 0:  # pragma: no cover - tuple 长度不可能为负
        raise FieldValueError(f"bad vector length: {n!r}")
    if n > MAX_DOT_PRODUCT_LEN:
        from .errors import DotLengthError

        raise DotLengthError(
            f"dot product length {n} exceeds MAX_DOT_PRODUCT_LEN="
            f"{MAX_DOT_PRODUCT_LEN}"
        )
    norm_xs = []
    norm_ys = []
    for j in range(n):
        norm_xs.append(_check_share_tuple(xs[j], f"x_shares[{j}]"))
        norm_ys.append(_check_share_tuple(ys[j], f"y_shares[{j}]"))
    return tuple(norm_xs), tuple(norm_ys)


@dataclass(frozen=True)
class DotProductMessage:
    """点积打开阶段某参与方在某一位置上广播的一条消息。

    与 :class:`Message` 形状一致，但额外携带 ``position``；消息键为
    ``(op_id, position, party)``。``triple_id`` 必须是该点积为
    ``position`` 位置预留的那一个三元组。重复且完全一致的消息幂等，
    任一字段不同即冲突（导致整个点积终止）。
    """

    op_id: str
    position: int
    party: int
    triple_id: str
    d: int  # d_{j,i} = x_{j,i} - a_{j,i} mod p
    e: int  # e_{j,i} = y_{j,i} - b_{j,i} mod p

    def __post_init__(self):
        check_id(self.op_id, "op_id")
        check_party(self.party)
        check_id(self.triple_id, "triple_id")
        require_element(self.d, "d")
        require_element(self.e, "e")
        # 位置上限在消息构造期只能按全局长度上限校验；操作级边界
        （position < 本次点积长度）在转录投递时校验。
        check_position(self.position, MAX_DOT_PRODUCT_LEN)


def derive_dot_messages(
    op_id: str,
    triples,
    x_shares,
    y_shares,
) -> tuple[tuple[DotProductMessage, DotProductMessage, DotProductMessage], ...]:
    """诚实参与方为整个点积生成消息。

    :param triples: 长度 ``n`` 的 :class:`BeaverTriple` 序列，位置 ``j``
        的乘法独占 ``triples[j]``。
    :param x_shares: 见 :func:`check_share_vectors`。
    :param y_shares: 同上。
    :returns: 长度为 ``n`` 的元组，第 ``j`` 项是该位置的三方消息元组
        （共 ``3*n`` 条）。``n == 0`` 时返回空元组。
    """
    check_id(op_id, "op_id")
    try:
        tri = tuple(triples)
    except TypeError:
        raise FieldTypeError("triples must be an iterable of BeaverTriple") from None
    xs, ys = check_share_vectors(x_shares, y_shares)
    if len(tri) != len(xs):
        from .errors import DotLengthError

        raise DotLengthError(
            f"triples length {len(tri)} does not match share vector length {len(xs)}"
        )
    for j, tr in enumerate(tri):
        if not isinstance(tr, BeaverTriple):
            raise FieldTypeError(
                f"triples[{j}] must be a BeaverTriple, got {type(tr).__name__}"
            )
    per_position = []
    for j in range(len(tri)):
        triple = tri[j]
        msgs = tuple(
            DotProductMessage(
                op_id,
                j,
                i,
                triple.triple_id,
                (xs[j][i] - triple.a_shares[i]) % P,
                (ys[j][i] - triple.b_shares[i]) % P,
            )
            for i in range(N_PARTIES)
        )
        per_position.append(msgs)
    return tuple(per_position)
