#!/usr/bin/env python3
"""固定输入演示：带会话绑定的 Beaver 三元组乘法离线转录。

运行：``python demo.py``（远小于 90 秒预算，纯标准库、离线、确定性）。

三部分，全部为实时计算：
1. 两个合成会话 ``sA`` / ``sB``（含同名操作 ``mul``）严格交错、乱序投递，
   分别打开，用独立朴素模乘核对重构乘积与各方份额。
2. 把两个会话的全部消息整批重放：相同重复一律幂等（``duplicate``），
   重复打开返回同一缓存结果。
3. 实际错误边界：向 ``sA/mul`` 重放一条内容冲突的消息 → 冲突中止该操作、
   三元组作废；之后任何重放（哪怕与原消息一致）都被
   ``OperationAbortedError`` 拒绝；同一 transcript 里的 ``sB/mul`` 不受
   影响，照常打开。
"""

import random

from sss_mult import (
    P,
    ConflictError,
    OperationAbortedError,
    Transcript,
    TripleBank,
    honest_prepare,
    make_triple,
)

SEED = 20260924
SA, SB = "sA", "sB"


def check_product(result, x, y, tag):
    plain = (x * y) % P  # 独立朴素参考，不经过被测核心
    print(f"  [{tag}] 重构 {result.session_id}/mul = {result.product}，"
          f"朴素参考 ({x}*{y}) mod {P} = {plain}")
    assert result.product == plain


def main() -> int:
    print("=" * 72)
    print("sss_mult 演示：会话绑定 + 乱序缓冲 + 重放幂等 + 冲突中止")
    print("=" * 72)

    bank = TripleBank()
    rng = random.Random(SEED)
    bank.register(make_triple("triple-A", a=17, b=9, rng=rng))
    bank.register(make_triple("triple-B", a=33, b=25, rng=rng))
    bank.register(make_triple("triple-A2", a=2, b=3, rng=rng))
    bank.register(make_triple("triple-B2", a=4, b=5, rng=rng))

    # ---- [1] 两个合成会话严格交错、乱序 -----------------------------------
    xA, yA = 12, 8
    xB, yB = 11, 7
    _, _, msgsA = honest_prepare(
        "mul", bank.get("triple-A"), xA, yA, rng, session_id=SA
    )
    _, _, msgsB = honest_prepare(
        "mul", bank.get("triple-B"), xB, yB, rng, session_id=SB
    )

    transcript = Transcript(bank)
    print("\n[1] 两会话交错乱序投递（顺序 A2, B1, A0, B2, B0, A1）：")
    stream = [msgsA[2], msgsB[1], msgsA[0], msgsB[2], msgsB[0], msgsA[1]]
    for i, m in enumerate(stream, 1):
        status = transcript.add_message(m)
        print(f"  第{i}条: session={m.session_id} P{m.party} -> {status!r}")
    print(f"  会话数={transcript.sessions_count()}，"
          f"各会话消息数 sA={transcript.messages_count(SA)}, "
          f"sB={transcript.messages_count(SB)}（同名 op='mul' 互不干扰）")

    resultA = transcript.open("mul", session_id=SA)
    resultB = transcript.open("mul", session_id=SB)
    check_product(resultA, xA, yA, SA)
    check_product(resultB, xB, yB, SB)
    print(f"  三元组状态: triple-A={bank.state('triple-A')}, "
          f"triple-B={bank.state('triple-B')}")

    # ---- [2] 整批重放：相同重复幂等 ---------------------------------------
    print("\n[2] 六个消息打乱后整批重放：")
    replay = [msgsB[1], msgsA[1], msgsA[0], msgsB[0], msgsA[2], msgsB[2]]
    statuses = [transcript.add_message(m) for m in replay]
    print(f"  返回状态: {statuses}")
    assert all(s == "duplicate" for s in statuses)
    print(f"  消息总数仍为 {transcript.messages_count()}（没有重复落盘）")
    assert transcript.open("mul", session_id=SA) is resultA
    assert transcript.open("mul", session_id=SB) is resultB
    print("  重复打开返回同一缓存结果对象（打开也是幂等的）。")

    # ---- [3] 冲突重复中止该操作，重放不能复活；另一会话不受影响 -----------
    print("\n[3] 错误边界：新转录机中制造冲突重复")
    t2 = Transcript(bank)
    _, _, cA = honest_prepare(
        "mul", bank.get("triple-A2"), 4, 5, rng, session_id=SA
    )
    _, _, cB = honest_prepare(
        "mul", bank.get("triple-B2"), 9, 3, rng, session_id=SB
    )
    # 两会话先交错投递：A 收 P0/P2，B 收 P0/P1。
    for m in (cA[0], cB[0], cA[2], cB[1]):
        t2.add_message(m)

    from sss_mult import Message
    clash = Message("mul", 0, "triple-A2", (cA[0].d + 1) % P, cA[0].e,
                    session_id=SA)
    print(f"  向 sA/mul 的 P0 重放一条 d 被篡改的消息 ...")
    try:
        t2.add_message(clash)
    except ConflictError as exc:
        print(f"  捕获 {type(exc).__name__}: {exc}")
    else:  # pragma: no cover - 防御性：冲突必须被检出
        raise AssertionError("冲突重复应当被拒绝")

    print(f"  sA/mul is_aborted={t2.is_aborted('mul', SA)}，"
          f"原因: {t2.abort_reason('mul', SA)}")
    print(f"  triple-A2 状态={bank.state('triple-A2')}（随中止作废）；"
          f"triple-B2 状态={bank.state('triple-B2')}（无关，保持预留）")

    # 重放原消息（哪怕完全一致）也不能复活已中止操作。
    try:
        t2.add_message(cA[0])
    except OperationAbortedError as exc:
        print(f"  重放原 P0 一致消息 -> 捕获 {type(exc).__name__}: {exc}")
    else:  # pragma: no cover
        raise AssertionError("已中止操作必须拒绝一切重放")
    try:
        t2.open("mul", session_id=SA)
    except OperationAbortedError as exc:
        print(f"  尝试打开 sA/mul -> 捕获 {type(exc).__name__}")

    # 同 transcript 的另一会话不受影响：补齐后正常打开。
    t2.add_message(cB[2])
    resB = t2.open("mul", session_id=SB)
    check_product(resB, 9, 3, SB)
    print(f"  中止隔离生效：sB/mul 仍正常，triple-B2={bank.state('triple-B2')}")
    print(f"  转录机操作总数={t2.operations_count()}（中止操作仍可观测，"
          f"但不可用；须换新 op_id 与新三元组重做）")

    print("\n演示结束：两会话交错正确重构 + 重放全幂等 + 冲突中止且隔离。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
