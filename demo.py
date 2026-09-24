#!/usr/bin/env python3
"""固定输入演示：带会话绑定的 Beaver 乘法离线转录（本次迭代）。

运行：``python demo.py``（约 1 秒内完成，纯标准库、离线、确定性）。

演示数据全部手算（P = 101），不依赖被测实现生成期望值；核验只用朴素
模乘 ``(x*y) % 101``。

会话 alpha，三元组 TA：a=10, b=20, c=99；x=6, y=7 → 积 42
    d_i=(1,99,98), e_i=(97,98,95), d=97, e=88, z=(47,64,32)
会话 beta， 三元组 TB：a=4, b=5, c=20；x=9, y=8 → 积 72
    d_i=(2,1,2),  e_i=(0,100,4), d=5,  e=3,  z=(23,29,20)

环节：
1. 两会话消息交错、乱序、含重复地投递，各自打开并用朴素模乘核验；
2. 重放：消息级重放幂等；整段交错流在全新转录机上重放，逐位一致；
3. 错误边界：送错会话（SessionMismatchError，无状态变更）；
4. 错误边界：冲突重复中止该操作（OperationAbortedError）、三元组烧毁，
   后续投递/打开/跨会话领取全部拒绝，无关会话与无关操作不受影响；
5. 旧入口兼容：默认会话上的 Transcript.add_message 仍按旧契约工作
   （冲突不落盘、不中止，补齐后可打开）。
"""

from sss_mult import (
    P,
    BeaverTriple,
    ConflictError,
    Message,
    OperationAbortedError,
    SessionMismatchError,
    Transcript,
    TripleBank,
    TripleReusedError,
)

ALPHA = "alpha"
BETA = "beta"

# ---- 手算夹具（见模块文档字符串中的手算明细） ---------------------------

TA = BeaverTriple(
    triple_id="TA", a=10, b=20, c=99,
    a_shares=(2, 3, 5), b_shares=(2, 7, 11), c_shares=(29, 30, 40),
)
TB = BeaverTriple(
    triple_id="TB", a=4, b=5, c=20,
    a_shares=(1, 2, 1), b_shares=(0, 3, 2), c_shares=(5, 8, 7),
)
ALPHA_D, ALPHA_E = (1, 99, 98), (97, 98, 95)
BETA_D, BETA_E = (2, 1, 2), (0, 100, 4)
ALPHA_XY = (6, 7)
BETA_XY = (9, 8)
ALPHA_Z_HAND = (47, 64, 32)
BETA_Z_HAND = (23, 29, 20)


def alpha_messages(op_id="mul"):
    return [Message(op_id, i, "TA", ALPHA_D[i], ALPHA_E[i], ALPHA) for i in range(3)]


def beta_messages(op_id="mul"):
    return [Message(op_id, i, "TB", BETA_D[i], BETA_E[i], BETA) for i in range(3)]


def fresh_world():
    bank = TripleBank()
    bank.register(TA)
    bank.register(TB)
    t = Transcript(bank)
    return bank, t, t.session(ALPHA), t.session(BETA)


def show_result(tag, res, xy, z_hand):
    z = res.output_shares()
    plain = (xy[0] * xy[1]) % P  # 独立朴素参考，不经过被测核心
    print(f"  {tag}: d={res.d}, e={res.e}, 输出份额 z={z}, 重构积={res.product}")
    print(f"        手算份额={z_hand}，朴素参考 ({xy[0]}*{xy[1]}) mod {P} = {plain}")
    assert z == z_hand and res.product == plain, "手算/朴素核验失败"
    print("        -> 三者一致。")


def main() -> int:
    print("=" * 72)
    print("sss_mult 演示：会话绑定 + 乱序缓冲 + 幂等重放 + 冲突中止")
    print("=" * 72)

    # ---- [1] 两个合成会话交错、乱序、含重复 ------------------------------
    bank, t, sa, sb = fresh_world()
    ma, mb = alpha_messages(), beta_messages()
    print("\n[1] 两会话交错乱序投递（alpha 的 P0、beta 的 P0、alpha 的 P1、")
    print("    beta 的 P1、alpha-P0 重放、beta 的 P2、alpha 的 P2、beta-P0 重放）")
    stream = [ma[0], mb[0], ma[1], mb[1], ma[0], mb[2], ma[2], mb[0]]
    statuses = []
    for m in stream:
        statuses.append((sa if m.session_id == ALPHA else sb).add_message(m))
    print(f"    投递状态序列: {statuses}")
    assert statuses == ["accepted", "accepted", "accepted", "accepted",
                        "duplicate", "accepted", "accepted", "duplicate"]
    print(f"    齐三方: alpha={sa.is_complete('mul')}, beta={sb.is_complete('mul')}")
    ra = sa.open("mul")
    rb = sb.open("mul")
    show_result("alpha (x=6,y=7)", ra, ALPHA_XY, ALPHA_Z_HAND)
    show_result("beta  (x=9,y=8)", rb, BETA_XY, BETA_Z_HAND)
    print(f"    三元组状态: TA={bank.state('TA')}, TB={bank.state('TB')}")

    # ---- [2] 重放核验 ----------------------------------------------------
    print("\n[2] 重放核验")
    for m in ma:
        assert sa.add_message(m) == "duplicate"
    print("    消息级重放：alpha 三条全部返回 'duplicate'，"
          f"消息计数仍为 {sa.messages_count()}")
    assert sa.open("mul") is ra and sb.open("mul") is rb
    print("    打开级重放：返回缓存的同一结果对象")

    # 转录级重放：同一交错流在全新的转录机/银行上再跑一遍。
    _, t2, sa2, sb2 = fresh_world()
    replay_stream = [ma[0], mb[1], ma[2], mb[0], ma[1], mb[2]]
    for m in replay_stream:
        (sa2 if m.session_id == ALPHA else sb2).add_message(m)
    ra2, rb2 = sa2.open("mul"), sb2.open("mul")
    assert ra2.output_shares() == ra.output_shares()
    assert rb2.output_shares() == rb.output_shares()
    print(f"    交错流重放到全新转录机（不同乱序）：z_alpha={ra2.output_shares()}, "
          f"z_beta={rb2.output_shares()}，与首次逐位一致")

    # ---- [3] 错误边界：送错会话 ------------------------------------------
    print("\n[3] 错误边界：消息路由错误")
    _, t3, sa3, sb3 = fresh_world()
    try:
        sb3.add_message(alpha_messages()[0])  # alpha 的消息误投 beta
    except SessionMismatchError as exc:
        print(f"    捕获 {type(exc).__name__}: {exc}")
    print(f"    无状态变更：总消息数={t3.messages_count()}，"
          f"TA 状态={t3.bank.state('TA')!r}")
    assert t3.messages_count() == 0 and t3.bank.state("TA") == "free"

    # ---- [4] 错误边界：冲突重复中止操作、烧毁三元组 ----------------------
    print("\n[4] 错误边界：冲突重复 -> 中止该操作")
    bank4, t4, sa4, sb4 = fresh_world()
    ma4, mb4 = alpha_messages(), beta_messages()
    sb4.add_messages([mb4[0], mb4[1]])   # 先让无关会话占上两条
    sa4.add_messages([ma4[0], ma4[1]])
    bad = Message("mul", 0, "TA", (ALPHA_D[0] + 1) % P, ALPHA_E[0], ALPHA)
    try:
        sa4.add_message(bad)  # P0 的冲突重复（d 被篡改）
    except OperationAbortedError as exc:
        print(f"    捕获 {type(exc).__name__}: {exc}")
    print(f"    操作终态: is_aborted={sa4.is_aborted('mul')}，"
          f"中止原因: {sa4.abort_reason('mul')}")
    print(f"    三元组 TA 被烧毁: bank.state('TA') = {bank4.state('TA')!r}")

    for label, call in (
        ("重放原消息", lambda: sa4.add_message(ma4[0])),
        ("补投其他方", lambda: sa4.add_message(ma4[2])),
        ("再次打开", lambda: sa4.open("mul")),
    ):
        try:
            call()
        except OperationAbortedError as exc:
            print(f"    中止后{label} -> 仍捕获 {type(exc).__name__}")

    # 烧毁的三元组不能被任何新操作领取（即使在另一个会话）。
    try:
        sb4.add_message(Message("other", 0, "TA", 1, 1, BETA))
    except TripleReusedError as exc:
        print(f"    beta 新操作领取烧毁的 TA -> {type(exc).__name__}: "
              f"terminal_state={exc.terminal_state!r}")
    assert sb4.missing_parties("other") == (0, 1, 2)
    assert sb4.operations_count() == 1  # 只有 beta 自己的 mul，other 未创建
    # 无关会话 beta 补齐后照常打开。
    sb4.add_message(mb4[2])
    res_beta = sb4.open("mul")
    assert res_beta.product == (9 * 8) % P
    print(f"    无关会话不受影响：beta 打开积={res_beta.product} "
          f"(= 9*8 mod {P})，TB 状态={bank4.state('TB')!r}")

    # ---- [5] 旧入口与旧契约仍然可用 --------------------------------------
    print("\n[5] 旧入口兼容：Transcript 默认会话、冲突不落盘不中止")
    bank5 = TripleBank()
    bank5.register(TA)
    t5 = Transcript(bank5)
    legacy = [Message("mul", i, "TA", ALPHA_D[i], ALPHA_E[i]) for i in range(3)]
    t5.add_message(legacy[0])
    try:
        t5.add_message(Message("mul", 0, "TA", 40, ALPHA_E[0]))
    except ConflictError as exc:
        print(f"    旧转录机捕获 {type(exc).__name__}（不中止操作）")
    assert not t5.is_aborted("mul") and bank5.state("TA") == "reserved"
    t5.add_messages([legacy[1], legacy[2]])
    res = t5.open("mul")
    assert res.product == (6 * 7) % P
    print(f"    补齐三方后正常打开：积={res.product} (= 6*7 mod {P})，"
          f"TA={bank5.state('TA')!r}")

    print("\n演示结束：交错乱序正确重构、重放幂等且逐位一致、")
    print("路由错误与冲突中止均为实时触发，无关状态无污染。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
