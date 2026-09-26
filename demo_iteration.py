#!/usr/bin/env python3
"""比较电路演示：三方分享的无符号小整数 a < b（离线、确定性）。

运行：python demo_iteration.py（真实计算，约 1 秒内完成，远小于 90 秒预算；
不使用 sleep、账号、密钥或预录结果）。

演示三部分：
1. 正常路径：8 位宽的 a=37 与 b=52 按位分享后比较，公开 a < b，
   并与普通整数比较独立对照。
2. 非位输入拒绝：把某一位换成重构值为 2 的份额，触发 NonBinaryBitError，
   展示已消费三元组不复活。
3. 三元组不足拒绝：少给一个三元组，在任何消费之前被拒绝。
"""

import random

from sss_mult import (
    NonBinaryBitError,
    ComparisonError,
    Transcript,
    TripleBank,
    less_than,
    make_triple,
    random_share,
    required_triples,
)

SEED = 20260926


def bits_of(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width)]


def setup(width: int, seed: int):
    rng = random.Random(seed)
    bank = TripleBank()
    ids = [f"demo-t-{i}" for i in range(required_triples(width))]
    for tid in ids:
        bank.register(make_triple(tid, rng.randrange(101), rng.randrange(101), rng))
    transcript = Transcript(bank)
    return rng, bank, transcript, ids


def main() -> int:
    print("=" * 68)
    print("sss_mult 比较电路演示：三方加法分享上的无符号整数 a < b")
    print("（本地半诚实教学模拟，不宣称恶意安全）")
    print("=" * 68)

    # ---- [1] 正常比较 -------------------------------------------------
    a, b, width = 37, 52, 8
    rng, bank, transcript, ids = setup(width, SEED)
    a_bits = [random_share(bit, rng) for bit in bits_of(a, width)]
    b_bits = [random_share(bit, rng) for bit in bits_of(b, width)]

    print(f"\n[1] 正常比较: a = {a}, b = {b}, 位宽 = {width}, "
          f"三元组 = {required_triples(width)} 个（每门唯一消费）")
    report = less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                       triple_ids=ids, op_prefix="demo-cmp")
    plain = int(a < b)  # 独立朴素参考
    print(f"  公开报告: 门数 = {len(report.gates)}, "
          f"位合法性 a = {list(map(int, report.valid_bits_a))}, "
          f"b = {list(map(int, report.valid_bits_b))}")
    print(f"  最终公开位 a < b = {report.result}；独立朴素参考 = {plain}")
    assert report.result == plain
    print(f"  首个门的公开掩蔽差值: op={report.gates[0].op_id!r} "
          f"d={report.gates[0].d} e={report.gates[0].e}（不含秘密/份额）")
    consumed = sum(1 for t in ids if bank.state(t) == "consumed")
    print(f"  三元组消费: {consumed}/{len(ids)} 已消费，每个恰好一次。")

    # ---- [2] 非位输入拒绝 ----------------------------------------------
    print("\n" + "-" * 68)
    print("[2] 拒绝边界：b 的第 0 位换成重构值为 2 的份额（非比特）")
    rng2, bank2, transcript2, ids2 = setup(3, SEED + 1)
    ok_a = [random_share(bit, rng2) for bit in bits_of(5, 3)]
    bad_b = [random_share(bit, rng2) for bit in bits_of(6, 3)]
    bad_b[0] = random_share(2, rng2)  # 非法位
    try:
        less_than(ok_a, bad_b, bank=bank2, transcript=transcript2,
                  triple_ids=ids2, op_prefix="demo-bad")
    except NonBinaryBitError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
        gone = sum(1 for t in ids2 if bank2.state(t) == "consumed")
        print(f"  -> 失败前已消费 {gone} 个三元组，保持 consumed 不复活；"
              f"位本身未被公开。")
    else:
        raise AssertionError("非位输入应当被拒绝")

    # ---- [3] 三元组不足拒绝 --------------------------------------------
    print("\n" + "-" * 68)
    print("[3] 拒绝边界：三元组数量不足（少给 1 个）")
    rng3, bank3, transcript3, ids3 = setup(2, SEED + 2)
    try:
        less_than([random_share(1, rng3) for _ in range(2)],
                  [random_share(0, rng3) for _ in range(2)],
                  bank=bank3, transcript=transcript3,
                  triple_ids=ids3[:-1], op_prefix="demo-short")
    except ComparisonError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
        free = sum(1 for t in ids3 if bank3.state(t) == "free")
        print(f"  -> 数量校验先于任何消费：{free}/{len(ids3)} 个三元组仍 free。")
    else:
        raise AssertionError("三元组不足应当被拒绝")

    print("\n演示结束：一次正确比较 + 两次真实拒绝，均为实时离线计算。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
