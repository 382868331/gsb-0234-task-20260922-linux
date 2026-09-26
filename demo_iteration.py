#!/usr/bin/env python3
"""固定输入演示：份额刷新（refresh）+ 刷新后乘法。

运行：python demo_iteration.py（约 1 秒内完成，纯标准库、离线、确定性，
无 sleep、无预录结果、不依赖账号/密钥/网络/外部服务）。

演示三部分：
1. 正常刷新：调用方注入掩码，各方只用与自己相关的掩码视图本地更新份额，
   乱序确认 + 一条重复确认（幂等），收齐后原子切换世代；用独立朴素模加法
   核对秘密不变。
2. 刷新后乘法：用新世代的份额走既有 Beaver 转录，重构乘积与朴素
   (x*y) % 101 一致；旧世代标签被明确拒绝。
3. 拒绝边界：冲突确认使一轮刷新失败、旧世代完整保留；同一 refresh_id
   换参数不能再次生效。
"""

import random

from sss_mult import (
    P,
    RefreshConfirmation,
    RefreshConflictError,
    RefreshCoordinator,
    StaleGenerationError,
    Transcript,
    TripleBank,
    derive_messages,
    make_masks,
    make_triple,
    mask_view,
    random_share,
    refresh_share,
)

SEED = 20260926
PARTIES = (0, 1, 2)
PAIRS = ((0, 1), (0, 2), (1, 2))


def plain_sum(shares):
    """独立参考：朴素模加法，不经过被测核心。"""
    total = 0
    for s in shares:
        total = (total + s) % P
    return total


def main():
    print("=" * 68)
    print("sss_mult 演示：三方加法分享的世代刷新（换份额、不换秘密）")
    print("=" * 68)

    rng = random.Random(SEED)
    secret = 37
    shares = random_share(secret, rng)
    coordinator = RefreshCoordinator()
    print(f"\n[1] 正常刷新: secret = {secret}（仅演示视角可见）")
    print(f"  刷新前份额: {shares}，独立模加法重构 = {plain_sum(shares)}")

    masks = make_masks(PAIRS, rng)  # 掩码由调用方注入
    print(f"  注入掩码: {masks}")
    old_gen = coordinator.current_generation
    coordinator.begin("refresh-1", old_gen, old_gen + 1)

    # 各方只用自己的掩码视图在本地更新份额，互不接触对方的掩码。
    new_shares = []
    for p in PARTIES:
        view = mask_view(masks, p)
        print(f"  P{p} 的私有掩码视图: {view}")
        new_shares.append(refresh_share(shares[p], p, view))
    new_shares = tuple(new_shares)

    # 公开确认乱序到达，含一条重复确认（幂等）。
    print("  乱序投递公开确认 (P2, P0)，重复 P0（幂等），再 P1 ...")
    coordinator.confirm(RefreshConfirmation("refresh-1", 2, old_gen, old_gen + 1, PARTIES))
    coordinator.confirm(RefreshConfirmation("refresh-1", 0, old_gen, old_gen + 1, PARTIES))
    dup = coordinator.confirm(
        RefreshConfirmation("refresh-1", 0, old_gen, old_gen + 1, PARTIES)
    )
    print(f"  重复 P0 确认返回: {dup!r}，缺方 = {coordinator.missing_parties('refresh-1')}")
    coordinator.confirm(RefreshConfirmation("refresh-1", 1, old_gen, old_gen + 1, PARTIES))

    result = coordinator.commit("refresh-1")
    print(f"  收齐确认，原子切换: 世代 {result.old_generation} -> {result.new_generation}")
    print(f"  刷新后份额: {new_shares}，独立模加法重构 = {plain_sum(new_shares)}")
    assert plain_sum(new_shares) == secret
    print("  -> 秘密不变，份额已换新。")

    # ---- 第二部分：刷新后乘法 ---------------------------------------------
    print("\n" + "-" * 68)
    print("[2] 刷新后乘法：新世代份额进入 Beaver 转录")
    x, y = 12, 8
    bank = TripleBank()
    bank.register(make_triple("it-triple", a=17, b=9, rng=rng))
    triple = bank.get("it-triple")
    x_shares = random_share(x, rng)
    y_shares = random_share(y, rng)

    # 把 x 的份额也刷新到世代 2，再用新份额做乘法。
    masks2 = make_masks(PAIRS, rng)
    gen = coordinator.current_generation
    coordinator.begin("refresh-2", gen, gen + 1)
    x_new = tuple(refresh_share(x_shares[p], p, mask_view(masks2, p)) for p in PARTIES)
    for p in PARTIES:
        coordinator.confirm(
            RefreshConfirmation("refresh-2", p, gen, gen + 1, PARTIES)
        )
    coordinator.commit("refresh-2")
    print(f"  x 的份额刷新到世代 {coordinator.current_generation}，"
          f"独立重构 = {plain_sum(x_new)}（应等于 x = {x}）")
    assert plain_sum(x_new) == x

    try:
        coordinator.check_generation(gen)  # 旧世代标签
    except StaleGenerationError as exc:
        print(f"  旧世代标签被拒绝: {exc}")
    coordinator.check_generation(coordinator.current_generation)

    transcript = Transcript(bank)
    transcript.add_messages(derive_messages("mul-after-refresh", triple, x_new, y_shares))
    opened = transcript.open("mul-after-refresh")
    reference = (x * y) % P
    print(f"  重构乘积 = {opened.product}，朴素参考 ({x}*{y}) mod {P} = {reference}")
    assert opened.product == reference
    print("  -> 一致，刷新后乘法正确。")

    # ---- 第三部分：冲突失败与 ID 复用拒绝 ----------------------------------
    print("\n" + "-" * 68)
    print("[3] 拒绝边界：冲突确认使本轮失败，旧世代完整保留")
    gen = coordinator.current_generation
    coordinator.begin("refresh-bad", gen, gen + 1)
    coordinator.confirm(RefreshConfirmation("refresh-bad", 0, gen, gen + 1, PARTIES))
    try:
        # P1 的确认绑定了不同的新世代号：冲突。
        coordinator.confirm(RefreshConfirmation("refresh-bad", 1, gen, gen + 2, PARTIES))
    except RefreshConflictError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
    print(f"  当前世代仍为 {coordinator.current_generation}（旧世代完整保留）")
    assert coordinator.current_generation == gen
    try:
        coordinator.begin("refresh-bad", gen, gen + 1)
    except Exception as exc:
        print(f"  同一 refresh_id 再次生效被拒绝: {type(exc).__name__}: {exc}")

    print("\n演示结束：一次正常刷新 + 一次刷新后乘法 + 一次真实冲突失败，"
          "均为实时计算。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

