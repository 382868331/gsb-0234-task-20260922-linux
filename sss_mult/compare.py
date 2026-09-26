"""无符号小整数比较（位宽 <= 8）的三方加法分享模拟。

输入是两个无符号整数的**逐位三方份额**（MSB 优先），电路用 Beaver
乘法组合布尔门，最终只公开一个比特：``a < b``。

门级乘法走 :meth:`sss_mult.protocol.Transcript.open_shares` 内部路径：
每个门只公开掩蔽差值 ``d = x - a``、``e = y - b``，返回乘积份额，
**不重构** ``x``、``y`` 或乘积。每个门唯一消费一个 Beaver 三元组。

位合法性：对每个位 ``b`` 构造 ``v = b*(b-1)``（比特当且仅当
``v == 0``，因为 101 是素数），再用随机非零掩蔽 ``r`` 计算
``w = v*r`` 并公开 ``w`` 是否为零 —— 公开转录里只有"是否为零"
这个布尔结论，不直接公开 ``b``，也不公开 ``v`` 的具体值
（``v != 0`` 时 ``w`` 是均匀随机的非零域元素，不泄露 ``b``）。
检测到非位输入时返回失败报告（``ok=False``），已消费的三元组保持
消费状态，不会被失败"复活"。

安全声明：这是**本地半诚实（passive）教学模拟** —— 单进程上帝视角、
不模拟网络、不验证参与方知识一致性，**不能由此宣称恶意安全**。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .errors import FieldTypeError, FieldValueError, InsufficientTriplesError
from .field import N_PARTIES, P
from .protocol import Transcript, TripleBank
from .shares import (
    _check_share_tuple,
    check_id,
    derive_messages,
    random_share,
    reconstruct,
)

#: 比较电路支持的最大位宽。
MAX_BIT_WIDTH = 8

#: 每个位的门数：a/b 两个位的合法性各 2 门（平方 + 掩蔽），
#: 以及 ab、eq AND NOT a、term AND b、lt OR term、eq AND xnor 共 5 门。
_GATES_PER_BIT = 9


def triples_required(width: int) -> int:
    """返回 ``width`` 位比较所需的 Beaver 三元组数量（每门恰好一个）。"""
    _check_width(width)
    return _GATES_PER_BIT * width


def _check_width(width: int) -> int:
    if isinstance(width, bool) or type(width) is not int:
        raise FieldTypeError(
            f"width must be an int in [1, {MAX_BIT_WIDTH}] (bool not accepted), "
            f"got {type(width).__name__}: {width!r}"
        )
    if not (1 <= width <= MAX_BIT_WIDTH):
        raise FieldValueError(
            f"width={width} out of range [1, {MAX_BIT_WIDTH}]"
        )
    return width


@dataclass(frozen=True)
class GateRecord:
    """一条公开转录条目：只含门标签、ID 与掩蔽差值 ``d``/``e``。

    刻意不包含任何私有份额、秘密或重构中间值。
    """

    gate: str
    op_id: str
    triple_id: str
    d: int  # 公开掩蔽差值 d = x - a (mod p)
    e: int  # 公开掩蔽差值 e = y - b (mod p)


@dataclass(frozen=True)
class BitCheck:
    """一个位的合法性公开结论：只记录 ``b*(b-1)`` **是否为零**。"""

    position: int  # 位序号，0 = 最高位
    operand: str  # "a" 或 "b"
    product_is_zero: bool


@dataclass(frozen=True)
class ComparisonReport:
    """一次比较的公开报告（模拟器对外可见部分）。

    ``result`` 为最终公开的比较比特（``1`` 表示 ``a < b``）；
    失败（非位输入）时为 ``None``，``failure`` 给出可定位原因。
    报告不包含任何私有份额或中间秘密。
    """

    label: str
    width: int
    ok: bool
    result: int | None
    failure: str | None
    gates: tuple[GateRecord, ...]
    bit_checks: tuple[BitCheck, ...]


# ---- 份额级局部仿射运算（无需通信） --------------------------------------


def _affine(shares, alpha: int, beta: int):
    """公开常量仿射 ``alpha*x + beta``：全体乘 alpha，0 号方加 beta。"""
    out = [(alpha * s) % P for s in shares]
    out[0] = (out[0] + beta) % P
    return tuple(out)


def _add(x, y):
    return tuple((x[i] + y[i]) % P for i in range(N_PARTIES))


def _neg(x):
    return tuple((-s) % P for s in x)


_ZERO_SHARES = (0, 0, 0)
_ONE_SHARES = (1, 0, 0)


class _GateSession:
    """一次比较的门执行会话：每门一个 op、唯一消费一个三元组。"""

    def __init__(self, bank: TripleBank, rng: random.Random, label: str):
        self._bank = bank
        self._rng = rng
        self._label = label
        self._transcript = Transcript(
            bank,
            max_operations=triples_required(MAX_BIT_WIDTH) + 1,
            max_messages=3 * (triples_required(MAX_BIT_WIDTH) + 1),
        )
        self._gate_index = 0
        self.gates: list[GateRecord] = []

    def mul(self, gate: str, x_shares, y_shares, triple_id: str):
        """执行一个 Beaver 乘法门，只公开 d/e，返回乘积份额。"""
        triple = self._bank.get(triple_id)
        op_id = f"{self._label}-g{self._gate_index}"
        self._gate_index += 1
        messages = derive_messages(op_id, triple, x_shares, y_shares)
        self._transcript.add_messages(messages)
        opened = self._transcript.open_shares(op_id)
        self.gates.append(
            GateRecord(gate=gate, op_id=op_id, triple_id=triple_id,
                       d=opened.d, e=opened.e)
        )
        return opened.z_shares


def compare_bit_shares(
    bank: TripleBank,
    a_bit_shares,
    b_bit_shares,
    triple_ids,
    rng: random.Random,
    *,
    label: str = "cmp",
) -> ComparisonReport:
    """比较两个以逐位三方份额给出的无符号整数，公开 ``a < b``。

    :param a_bit_shares: ``width`` 个三方份额元组，MSB 优先；
        每位重构值必须为 0 或 1，否则返回 ``ok=False`` 的失败报告。
    :param b_bit_shares: 同 ``a_bit_shares``，长度必须一致。
    :param triple_ids: 供电路消费的三元组 ID，每个门唯一消费一个；
        数量不足时抛 :class:`InsufficientTriplesError`（在消费任何
        三元组之前）。ID 重复或未注册会在对应门处抛
        :class:`TripleReusedError` / :class:`UnknownTripleError`，
        已消费的三元组保持消费状态。
    :param rng: 种子确定的随机源（掩蔽与分享用）。
    :returns: :class:`ComparisonReport`；公开内容只有各门的掩蔽差值
        ``d``/``e``、各位的"乘积是否为零"结论与最终比较比特。
    """
    check_id(label, "label")
    if not isinstance(rng, random.Random):
        raise FieldTypeError(
            f"rng must be a random.Random instance, got {type(rng).__name__}"
        )
    a_bits = tuple(
        _check_share_tuple(s, f"a_bit_shares[{i}]")
        for i, s in enumerate(a_bit_shares)
    )
    b_bits = tuple(
        _check_share_tuple(s, f"b_bit_shares[{i}]")
        for i, s in enumerate(b_bit_shares)
    )
    if len(a_bits) != len(b_bits):
        raise FieldValueError(
            f"a_bit_shares and b_bit_shares must have equal width, "
            f"got {len(a_bits)} and {len(b_bits)}"
        )
    width = _check_width(len(a_bits))

    ids = list(triple_ids)
    need = triples_required(width)
    if len(ids) < need:
        raise InsufficientTriplesError(need, len(ids))
    id_iter = iter(ids)

    session = _GateSession(bank, rng, label)
    bit_checks: list[BitCheck] = []

    def check_bit(position: int, operand: str, bit) -> bool:
        """构造 b*(b-1)，掩蔽后只公开是否为零；返回是否为合法比特。"""
        # v = b * (b - 1)
        v = session.mul(f"valid-{operand}{position}-sq",
                        bit, _affine(bit, 1, -1), next(id_iter))
        # w = v * r，r 为随机非零掩蔽；w == 0 当且仅当 v == 0。
        r = rng.randrange(1, P)
        r_shares = random_share(r, rng)
        w = session.mul(f"valid-{operand}{position}-mask",
                        v, r_shares, next(id_iter))
        is_zero = reconstruct(w) == 0
        bit_checks.append(BitCheck(position, operand, is_zero))
        return is_zero

    lt = _ZERO_SHARES  # 到目前为止 a 前缀 < b 前缀
    eq = _ONE_SHARES  # 到目前为止前缀相等
    for pos in range(width):
        a_i, b_i = a_bits[pos], b_bits[pos]
        if not check_bit(pos, "a", a_i):
            return ComparisonReport(
                label=label, width=width, ok=False, result=None,
                failure=f"bit a[{pos}] is not 0/1 (b*(b-1) != 0)",
                gates=tuple(session.gates), bit_checks=tuple(bit_checks),
            )
        if not check_bit(pos, "b", b_i):
            return ComparisonReport(
                label=label, width=width, ok=False, result=None,
                failure=f"bit b[{pos}] is not 0/1 (b*(b-1) != 0)",
                gates=tuple(session.gates), bit_checks=tuple(bit_checks),
            )
        ab = session.mul(f"and-ab-{pos}", a_i, b_i, next(id_iter))
        # xnor = 1 - a - b + 2ab（局部仿射，无需门）
        xnor = _add(_add(_affine(ab, 2, 1), _neg(a_i)), _neg(b_i))
        not_a = _affine(a_i, -1, 1)
        t1 = session.mul(f"and-eq-nota-{pos}", eq, not_a, next(id_iter))
        term = session.mul(f"and-term-{pos}", t1, b_i, next(id_iter))
        # lt = lt OR term = lt + term - lt*term
        lt_term = session.mul(f"or-lt-term-{pos}", lt, term, next(id_iter))
        lt = _add(_add(lt, term), _neg(lt_term))
        eq = session.mul(f"and-eq-xnor-{pos}", eq, xnor, next(id_iter))

    result_bit = reconstruct(lt)  # 最终公开的比较比特 a < b
    return ComparisonReport(
        label=label, width=width, ok=True, result=result_bit,
        failure=None,
        gates=tuple(session.gates), bit_checks=tuple(bit_checks),
    )
