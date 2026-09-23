"""固定输入的离线演示：一次正确重构 + 一次实际触发的复用拒绝。

运行：python demo.py   （纯标准库，确定性输出，无网络/无 sleep/无预录）
"""

import random

from beaver_transcript import (
    Message,
    Party,
    Transcript,
    TripleSource,
    reconstruct,
    share_secret,
)
from beaver_transcript.errors import MessageConflictError, TripleReuseError

# 全部输入均为显式合成常量，种子固定，输出可复现。
SEED = 20260922
X, Y = 37, 42          # 秘密输入
A, B = 11, 25          # Beaver 三元组的 a、b（c = a*b mod 101 由库内计算）
TRIPLE_ID = "demo-triple-1"
OP_ID = "demo-mul-1"


def normal_run():
    print("=== 正常流程：三方加法分享 + Beaver 乘法 ===")
    source = TripleSource(random.Random(SEED))
    triple_shares = source.issue_values(TRIPLE_ID, A, B)
    x_shares = share_secret(X, random.Random(SEED + 1))
    y_shares = share_secret(Y, random.Random(SEED + 2))
    print(f"输入 x={X}, y={Y}；三元组 a={A}, b={B}, c=a*b mod 101={A * B % 101}")
    print(f"x 的份额: {x_shares}  重构={reconstruct(x_shares)}")
    print(f"y 的份额: {y_shares}  重构={reconstruct(y_shares)}")

    parties = [Party(i) for i in range(3)]
    transcript = Transcript(parties)
    transcript.begin(OP_ID, TRIPLE_ID)

    messages = []
    for i, party in enumerate(parties):
        party.receive_triple(triple_shares[i])
        pair = party.contribute(OP_ID, TRIPLE_ID, x_shares[i], y_shares[i])
        messages.extend(pair)
        print(f"参与方 {i} 广播: d_{i}={pair[0].value}, e_{i}={pair[1].value}")

    # 故意乱序投递，并夹带一条一致重复消息（幂等）。
    order = [messages[2], messages[0], messages[5], messages[1], messages[4], messages[3]]
    transcript.post_bulk(order)
    duplicate = transcript.post(messages[0])
    print(f"乱序投递完成；一致重复消息被幂等忽略（post 返回 {duplicate}）")

    result = transcript.open(OP_ID)
    print(f"公开值 d={result.d}, e={result.e}")
    for view in result.views:
        print(
            f"  参与方 {view.party} 视图: "
            f"a_i={view.a_i}, b_i={view.b_i}, c_i={view.c_i}, "
            f"d_i={view.d_i}, e_i={view.e_i} -> z_i={view.z_i}"
        )
    print(f"重构 z = {result.z}，域乘法对照 {X}*{Y} mod 101 = {X * Y % 101}")
    assert result.z == X * Y % 101
    print("正常流程校验通过。\n")
    return result


def rejection_run():
    print("=== 拒绝边界 1：三元组复用 ===")
    source = TripleSource(random.Random(SEED + 10))
    source.issue_values(TRIPLE_ID, A, B)
    try:
        source.issue_values(TRIPLE_ID, 1, 2)
    except TripleReuseError as exc:
        print(f"触发 TripleReuseError: {exc}")

    print("=== 拒绝边界 2：冲突消息 ===")
    parties = [Party(i) for i in range(3)]
    transcript = Transcript(parties)
    transcript.begin("demo-mul-2", "demo-triple-2")
    transcript.post(Message("demo-mul-2", 0, "d", 10))
    try:
        transcript.post(Message("demo-mul-2", 0, "d", 11))
    except MessageConflictError as exc:
        print(f"触发 MessageConflictError: {exc}")
    # 冲突被拒后原值仍在：一致重复依旧幂等。
    print(f"原消息仍可幂等重投（post 返回 {transcript.post(Message('demo-mul-2', 0, 'd', 10))}）")


if __name__ == "__main__":
    normal_run()
    rejection_run()
