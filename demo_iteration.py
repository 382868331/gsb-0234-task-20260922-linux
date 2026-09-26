#!/usr/bin/env python3
"""比较电路演示：三方加法分享上的无符号小整数 a < b。

运行：``python demo_iteration.py``（约 1 秒内完成，纯标准库、离线、
确定性；无 sleep、无预录结果、无账号/密钥/网络）。

演示三部分：
1. 正常路径：8 位输入 a=37, b=52，逐位份额进入比较电路，最终只公开
   一个比特 a<b，并与朴素整数比较独立对照。
2. 非法位边界：把某个位换成重构值为 2 的份额，电路返回失败报告，
   已消费的三元组保持消费状态（不复活）。
3. 三元组不足边界：少给一个三元组，电路在消费任何资源前拒绝。

说明：这是本地半诚实教学模拟，不能由此宣称恶意安全。
"""

import random

from sss_mult import (
    InsufficientTriplesError,
    P,
    TripleBank,
    compare_bit_shares,
    make_triple,
    random_share,
    triples_required,
)

SEED = 20260926


def bits_of(value: int, width: int):
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def share_bits(bits, rng):
    return tuple(random_share(b, rng) for b in bits)


def make_bank(label, count, rng):
    bank = TripleBank()
    ids = []
    for k in range(count):
        tid = f"{label}-t{k}"
        bank.register(make_triple(tid, rng.randrange(P), rng.randrange(P), rng))
        ids.append(tid)
    return bank, ids


def main() -> int:
    print("=" * 70)
    print("sss_mult 比较电路演示：GF(101) 三方加法分享上的无符号 a < b（位宽 <= 8）")
    print("本地半诚实教学模拟；不模拟网络，不宣称恶意安全。")
    print("=" * 70)

    rng = random.Random(SEED)

    # ---- [1] 正常比较 ------------------------------------------------
    width, a, b = 8, 37, 52
    bank, ids = make_bank("demo1", triples_required(width), rng)
    a_sh = share_bits(bits_of(a, width), rng)
    b_sh = share_bits(bits_of(b, width), rng)
    report = compare_bit_shares(bank, a_sh, b_sh, ids, rng, label="demo1")

    print(f"\n[1] 正常比较: a = {a}, b = {b}, 位宽 = {width}")
    print(f"  门数 = {len(report.gates)}（每门唯一消费一个三元组，"
          f"共 {triples_required(width)} 个）")
    print(f"  位合法性检查 {len(report.bit_checks)} 项，全部为零乘积: "
          f"{all(c.product_is_zero for c in report.bit_checks)}")
    sample = report.gates[0]
    print(f"  公开转录样例（只含掩蔽差值）: gate={sample.gate!r} "
          f"d={sample.d} e={sample.e}")
    plain = int(a < b)  # 独立朴素参考，不经过被测核心
    print(f"  最终公开比特 a<b = {report.result}；朴素参考 {a} < {b} = {plain}")
    assert report.ok and report.result == plain
    consumed = sum(1 for t in ids if bank.state(t) == "consumed")
    print(f"  三元组状态: {consumed}/{len(ids)} 已消费 -> 一致。")

    # ---- [2] 非法位返回失败 ------------------------------------------
    print("\n" + "-" * 70)
    print("[2] 非法位边界：把 a 的第 0 位换成重构值为 2 的份额")
    width2 = 4
    bank2, ids2 = make_bank("demo2", triples_required(width2), rng)
    a_sh2 = ((2, 0, 0),) + share_bits(bits_of(5, width2)[1:], rng)
    b_sh2 = share_bits(bits_of(9, width2), rng)
    bad = compare_bit_shares(bank2, a_sh2, b_sh2, ids2, rng, label="demo2")
    print(f"  ok = {bad.ok}, result = {bad.result}, failure = {bad.failure!r}")
    used = sum(1 for t in ids2 if bank2.state(t) == "consumed")
    print(f"  失败前已执行 {len(bad.gates)} 个门；已消费三元组 {used} 个，"
          f"保持 consumed（失败不复活资源）: "
          f"{all(bank2.state(t) == 'consumed' for t in ids2[:used])}")
    assert not bad.ok and used == len(bad.gates)

    # ---- [3] 三元组不足 ----------------------------------------------
    print("\n" + "-" * 70)
    print("[3] 三元组不足边界：少提供一个三元组")
    need = triples_required(8)
    bank3, ids3 = make_bank("demo3", need - 1, rng)
    try:
        compare_bit_shares(
            bank3,
            share_bits(bits_of(1, 8), rng),
            share_bits(bits_of(2, 8), rng),
            ids3, rng, label="demo3",
        )
    except InsufficientTriplesError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
        print(f"  拒绝发生在消费任何三元组之前: "
              f"{all(bank3.state(t) == 'free' for t in ids3)}")
    else:  # pragma: no cover - 防御性
        raise AssertionError("三元组不足应当被拒绝")

    print("\n演示结束：一次正确比较 + 非法位失败 + 三元组不足拒绝，均为实时计算。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
