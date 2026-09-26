"""刷新（refresh）迭代演示：换份额、不换秘密，随后照常做 Beaver 乘法。

真实离线计算：固定种子的 random.Random，无网络、无账号、无密钥、
无 sleep、无预录结果。运行时间远低于 90 秒。
"""

import random

from sss_mult import (
    MaskBook,
    RefreshConflictError,
    RefreshManager,
    RefreshPlan,
    Transcript,
    TripleBank,
    derive_messages,
    make_triple,
    random_share,
)

P = 101


def main():
    rng = random.Random(20260926)

    secret = 42
    shares = random_share(secret, rng)
    print(f"秘密 s = {secret}（仅演示打印；协议对象不持有它）")
    print(f"世代 0 份额: {shares}，朴素求和校验 {sum(shares) % P} == {secret}")

    # --- 第 1 轮刷新：显式注入掩码 -------------------------------------
    manager = RefreshManager(shares)
    plan1 = RefreshPlan(refresh_id="refresh-1", old_generation=0, new_generation=1)
    masks1 = MaskBook({(0, 1): 11, (0, 2): 22, (1, 2): 33})
    manager.begin(plan1)
    for party in (2, 0, 1):  # 乱序确认
        manager.confirm(party, plan1)
    new_shares = manager.finalize(masks1)
    print(f"世代 1 份额: {new_shares}，朴素求和校验 {sum(new_shares) % P} == {secret}")
    assert sum(new_shares) % P == secret and new_shares != shares

    # --- 冲突注入：同一刷新 ID 换参数，本轮失败、旧世代保留 -------------
    try:
        manager.begin(RefreshPlan("refresh-1", old_generation=1, new_generation=2))
    except RefreshConflictError as exc:
        print(f"重放/换参被拒绝（世代仍为 {manager.generation}）: {exc}")

    # --- 第 2 轮刷新：随机掩码 -----------------------------------------
    plan2 = RefreshPlan("refresh-2", old_generation=1, new_generation=2)
    masks2 = MaskBook.random(rng)
    manager.begin(plan2)
    for party in (0, 1, 2):
        manager.confirm(party, plan2)
    shares_g2 = manager.finalize(masks2)
    print(f"世代 2 份额: {shares_g2}，朴素求和校验 {sum(shares_g2) % P} == {secret}")
    assert sum(shares_g2) % P == secret

    # --- 刷新后照常做 Beaver 乘法 --------------------------------------
    x_shares = shares_g2
    y = 8
    y_shares = random_share(y, rng)
    bank = TripleBank()
    bank.register(make_triple("t1", a=17, b=9, rng=rng))
    msgs = derive_messages("mul-after-refresh", bank.get("t1"), x_shares, y_shares)
    t = Transcript(bank)
    t.add_messages(msgs)
    res = t.open("mul-after-refresh")
    print(f"刷新后乘法: x={res.x}, y={res.y}, 重构乘积 = {res.product} "
          f"(期望 {(secret * y) % P})")
    assert res.product == (secret * y) % P

    # --- 公开转录不含掩码与份额 ----------------------------------------
    for rec in manager.public_record():
        print(f"公开记录: id={rec.plan.refresh_id} "
              f"gen {rec.plan.old_generation}->{rec.plan.new_generation} "
              f"confirmed_by={rec.confirmed_by} status={rec.status}")
    print("演示完成：秘密不变、份额换新、刷新后乘法正确。")


if __name__ == "__main__":
    main()
