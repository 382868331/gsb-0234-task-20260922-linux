#!/usr/bin/env python3
"""固定输入演示：Beaver 三元组乘法转录模拟。

运行：``python demo.py``（约 1 秒内完成，纯标准库、离线、确定性）。

演示两部分：
1. 正常路径：显式合成输入 x=12, y=8，消息乱序 + 一条重复消息投递，
   打开后打印各方视图并用独立朴素模乘 (x*y) % 101 核对重构结果。
2. 拒绝边界：尝试在第二个操作中复用已消费的 triple_id，转录机抛出
   :class:`TripleReusedError`，演示一次性消费语义。
"""

import random

from sss_mult import (
    P,
    Transcript,
    TripleBank,
    TripleReusedError,
    honest_prepare,
    make_triple,
)

SEED = 20260923


def print_views(result) -> None:
    print(f"  公开值: d = {result.d}, e = {result.e}")
    print(f"  重构输入: x = a + d = {result.x}, y = b + e = {result.y}")
    print("  各方视图 (a_i, b_i, c_i) 与输出份额 z_i:")
    for v in result.views:
        extra = "  (含常量 d*e)" if v.party == 0 else ""
        print(
            f"    P{v.party}: a_i={v.a_i:3d}  b_i={v.b_i:3d}  c_i={v.c_i:3d}"
            f"  d_i={v.d_i:3d}  e_i={v.e_i:3d}  ->  z_i={v.z_i:3d}{extra}"
        )


def main() -> int:
    print("=" * 68)
    print("sss_mult 演示：p = 101 三方加法分享 + Beaver 三元组离线转录")
    print("=" * 68)

    bank = TripleBank()
    rng = random.Random(SEED)
    # 显式合成两个三元组：(a,b) 给定，c = a*b mod 101 由模块精确算出。
    bank.register(make_triple("demo-triple-1", a=17, b=9, rng=rng))
    bank.register(make_triple("demo-triple-2", a=33, b=25, rng=rng))

    transcript = Transcript(bank)

    # ---- 第一部分：正常重构 ------------------------------------------------
    x, y = 12, 8
    triple = bank.get("demo-triple-1")
    _, _, messages = honest_prepare("mul-demo", triple, x, y, rng)

    print(f"\n[1] 正常乘法: x = {x}, y = {y}, op_id = 'mul-demo', "
          f"triple_id = 'demo-triple-1'")
    print("  乱序投递 (P2, P0)，再重复投递一条 P0（幂等），最后投递 P1 ...")
    transcript.add_message(messages[2])
    transcript.add_message(messages[0])
    status = transcript.add_message(messages[0])  # 重复一致
    print(f"  重复 P0 消息的返回状态: {status!r}（消息计数仍为 "
          f"{transcript.messages_count()}）")
    print(f"  齐三方前 is_complete = {transcript.is_complete('mul-demo')}")
    transcript.add_message(messages[1])
    print(f"  齐三方后 is_complete = {transcript.is_complete('mul-demo')}")

    result = transcript.open("mul-demo")
    print_views(result)

    z0, z1, z2 = result.output_shares()
    plain_reference = (x * y) % P  # 独立朴素参考，不经过被测核心
    print(f"  重构: z_0 + z_1 + z_2 = {z0} + {z1} + {z2} "
          f"= {sum(result.output_shares())} ≡ {result.product} (mod {P})")
    print(f"  独立朴素参考: ({x} * {y}) mod {P} = {plain_reference}")
    assert result.product == plain_reference
    print("  -> 一致，重构正确。")
    print(f"  三元组生命周期状态: demo-triple-1 = {bank.state('demo-triple-1')}")

    # ---- 第二部分：复用拒绝（实际触发的异常边界） --------------------------
    print("\n" + "-" * 68)
    print("[2] 拒绝边界：尝试在新操作 'mul-reuse' 中复用已消费的 "
          "'demo-triple-1'")
    _, _, reuse_messages = honest_prepare("mul-reuse", triple, x=5, y=6, rng=rng)
    try:
        transcript.add_messages(reuse_messages)
    except TripleReusedError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
        print("  -> 复用被拒绝；操作未创建，转录状态无部分变更：")
        print(f"     operations_count = {transcript.operations_count()}（仍为 1）")
        print(f"     'mul-reuse' 缺方 = {transcript.missing_parties('mul-reuse')}")
        print(f"     demo-triple-1 状态 = {bank.state('demo-triple-1')}")
    else:  # pragma: no cover - 防御性：拒绝必须发生
        raise AssertionError("复用已消费三元组应当被拒绝")

    # 第二个三元组仍可用于一个全新的串操作，证明拒绝没有污染无关状态。
    _, _, fresh = honest_prepare("mul-fresh", bank.get("demo-triple-2"), 11, 7, rng)
    transcript.add_messages(fresh)
    fresh_res = transcript.open("mul-fresh")
    assert fresh_res.product == (11 * 7) % P
    print(f"\n  无关三元组仍可用：'mul-fresh' 重构 = {fresh_res.product} "
          f"= 11*7 mod {P}（操作计数 {transcript.operations_count()}）")

    print("\n演示结束：一次正确重构 + 一次真实复用拒绝，均为实时计算结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
