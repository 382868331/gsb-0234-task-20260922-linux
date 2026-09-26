"""无符号小整数比较电路（半诚实教学模拟）。

在三方加法分享与 Beaver 乘法转录机之上，按位（位宽 <= 8）比较两个
无符号整数 a、b，最终只公开一个比特：a < b。

输入是每一位的三方份额（每 bit 一个三分量元组，LSB 在前）。电路：

1. 位合法性：对每一位 b_i 计算 v = b_i * (b_i - 1) 并只公开
   “是否为零”。v == 0 当且仅当 b_i 属于 {0, 1}；非位输入抛
   NonBinaryBitError，且不直接公开 b_i 本身。
2. 布尔门：AND 用 Beaver 乘法（经 Transcript.open_shares 内部路径，
   只取乘积份额，不重构中间秘密），NOT/常量倍乘/加法是本地线性操作。
   从最高位向下逐位迭代（下标以 LSB 为 0）::

       x_i  = (1 - a_i) * b_i            # AND 门
       ab_i = a_i * b_i                  # AND 门
       eqb_i = 1 - a_i - b_i + 2*ab_i    # 线性
       t_i  = eq * x_i                   # AND 门（lt 与 t 互斥，OR 即加法）
       lt   = lt + t_i                   # 线性
       eq   = eq * eqb_i                 # AND 门
3. 最终公开：重构 lt 得到唯一的公开比特 a < b。

资源语义：每个门唯一消费一个三元组（6 * width 个）；任何失败都不会
复活已消费的三元组。公开报告 ComparisonReport 只包含掩蔽差值
(d, e)、位合法性布尔值与最终比较位，不拼接私有份额或中间秘密。

安全声明：这是本地半诚实（被动）教学模拟，不能由此宣称恶意安全。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ComparisonError, FieldTypeError, NonBinaryBitError
from .field import N_PARTIES, P
from .protocol import Transcript, TripleBank
from .shares import _check_share_tuple, check_id, derive_messages, reconstruct

#: 比较电路支持的最大位宽。
MAX_WIDTH = 8

#: 每一位消耗的乘法门数：2 个位合法性门 + 4 个电路门。
_GATES_PER_BIT = 6


def required_triples(width: int) -> int:
    """返回比较 width 位无符号整数所需的三元组数量（6 * width）。"""
    _check_width(width)
    return _GATES_PER_BIT * width


@dataclass(frozen=True)
class GateRecord:
    """一个乘法门在公开转录中的记录（只含掩蔽后的公开值）。"""

    op_id: str
    triple_id: str
    purpose: str  # 门的用途说明（不含任何秘密值）
    d: int  # 公开掩蔽差值 d = x - a（a 为三元组随机掩码）
    e: int  # 公开掩蔽差值 e = y - b


@dataclass(frozen=True)
class ComparisonReport:
    """一次比较的公开报告。

    只包含：位宽、各门的掩蔽差值记录、每位合法性布尔值、最终比较位。
    不包含任何私有份额、输入整数本身或中间秘密的重构值。
    """

    width: int
    gates: tuple
    valid_bits_a: tuple  # LSB 在前
    valid_bits_b: tuple
    result: int  # 最终公开位：1 当且仅当 a < b


def _check_width(width: int) -> int:
    if isinstance(width, bool) or type(width) is not int:
        raise FieldTypeError(
            f"width must be an int in [1, {MAX_WIDTH}] (bool not accepted), "
            f"got {type(width).__name__}: {width!r}"
        )
    if not (1 <= width <= MAX_WIDTH):
        raise ComparisonError(f"width={width} out of range [1, {MAX_WIDTH}]")
    return width


def _check_bit_shares(bits, what: str) -> list:
    try:
        items = list(bits)
    except TypeError:
        raise FieldTypeError(f"{what} must be an iterable of share triples") from None
    for i, sh in enumerate(items):
        _check_share_tuple(sh, f"{what}[{i}]")
    return [tuple(sh) for sh in items]


# ---- 份额上的本地线性操作（不需要三元组） ---------------------------------


def _add_const(shares, c: int) -> tuple:
    return ((shares[0] + c) % P, shares[1], shares[2])


def _add_shares(x, y) -> tuple:
    return tuple((x[i] + y[i]) % P for i in range(N_PARTIES))


def _neg_shares(x) -> tuple:
    return tuple((-x[i]) % P for i in range(N_PARTIES))


def _mul_const(shares, k: int) -> tuple:
    return tuple((s * k) % P for s in shares)


_ZERO_SHARES = (0, 0, 0)
_ONE_SHARES = (1, 0, 0)


def less_than(
    a_bits,
    b_bits,
    *,
    bank: TripleBank,
    transcript: Transcript,
    triple_ids,
    op_prefix: str = "cmp",
) -> ComparisonReport:
    """比较两个按位分享的无符号整数，公开返回 a < b。

    :param a_bits: a 的位份额序列，LSB 在前，每项是三方份额三元组。
    :param b_bits: b 的位份额序列，形状同 a_bits，长度须一致。
    :param bank: 已注册足够三元组的注册表。
    :param transcript: 转录机；每个门是一个独立操作。
    :param triple_ids: 恰好 required_triples(width) 个互不相同的
        三元组 ID，按顺序唯一消费；数量不符抛 ComparisonError，
        未注册抛 UnknownTripleError。
    :param op_prefix: 各门 op_id 的前缀。
    :returns: ComparisonReport，result 为公开的 a < b 位。
    :raises NonBinaryBitError: 某位不是比特；已消费三元组保持消费。
    """
    if not isinstance(bank, TripleBank):
        raise FieldTypeError(f"bank must be a TripleBank, got {type(bank).__name__}")
    if not isinstance(transcript, Transcript):
        raise FieldTypeError(
            f"transcript must be a Transcript, got {type(transcript).__name__}"
        )
    check_id(op_prefix, "op_prefix")
    a = _check_bit_shares(a_bits, "a_bits")
    b = _check_bit_shares(b_bits, "b_bits")
    if len(a) != len(b):
        raise ComparisonError(
            f"a_bits and b_bits must have equal width, got {len(a)} and {len(b)}"
        )
    width = _check_width(len(a))
    need = required_triples(width)
    ids = list(triple_ids)
    for i, tid in enumerate(ids):
        check_id(tid, f"triple_ids[{i}]")
    if len(set(ids)) != len(ids):
        raise ComparisonError("triple_ids must be pairwise distinct")
    if len(ids) != need:
        raise ComparisonError(
            f"width {width} requires exactly {need} triples, got {len(ids)}"
        )

    gates: list = []
    remaining = iter(ids)

    def run_gate(x_shares, y_shares, op_id: str, purpose: str) -> tuple:
        triple = bank.get(next(remaining))  # UnknownTripleError 自然传播
        messages = derive_messages(op_id, triple, x_shares, y_shares)
        transcript.add_messages(messages)
        z = transcript.open_shares(op_id)
        d = sum(m.d for m in messages) % P
        e = sum(m.e for m in messages) % P
        gates.append(GateRecord(op_id, triple.triple_id, purpose, d, e))
        return z

    # ---- 阶段 1：位合法性（只公开“是否为零”） ----------------------------
    valid_a: list = []
    valid_b: list = []
    for operand, bits, valid in (("a", a, valid_a), ("b", b, valid_b)):
        for i, bit_shares in enumerate(bits):
            minus_one = _add_const(bit_shares, -1 % P)
            v_shares = run_gate(
                bit_shares, minus_one,
                f"{op_prefix}-valid-{operand}-{i}",
                f"validity {operand}[{i}]: v = bit*(bit-1)",
            )
            # 零检测：只把“是否为零”记入公开报告，不公开位本身。
            if reconstruct(v_shares) != 0:
                raise NonBinaryBitError(operand, i)
            valid.append(True)

    # ---- 阶段 2：从最高位向下的比较电路 ----------------------------------
    lt = _ZERO_SHARES
    eq = _ONE_SHARES
    for i in reversed(range(width)):
        not_a = _add_const(_neg_shares(a[i]), 1)
        x = run_gate(not_a, b[i], f"{op_prefix}-and-notab-{i}",
                     f"and[{i}]: (1-a)*b")
        ab = run_gate(a[i], b[i], f"{op_prefix}-and-ab-{i}",
                      f"and[{i}]: a*b")
        eq_bit = _add_const(
            _add_shares(
                _add_shares(_neg_shares(a[i]), _neg_shares(b[i])),
                _mul_const(ab, 2),
            ),
            1,
        )
        t = run_gate(eq, x, f"{op_prefix}-and-lt-{i}", f"and[{i}]: eq*x")
        lt = _add_shares(lt, t)  # lt 与 t 互斥，OR 即加法
        eq = run_gate(eq, eq_bit, f"{op_prefix}-and-eq-{i}", f"and[{i}]: eq*eqbit")

    # ---- 阶段 3：公开最终比较位 ------------------------------------------
    result = reconstruct(lt)
    return ComparisonReport(
        width=width,
        gates=tuple(gates),
        valid_bits_a=tuple(valid_a),
        valid_bits_b=tuple(valid_b),
        result=result,
    )
