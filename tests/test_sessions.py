"""会话绑定、乱序缓冲、重放幂等与冲突中止的专项测试。

核验口径：
- 乘积期望值只来自朴素模乘 ``(x*y) % 101``，各方输出份额用朴素公式
  ``(c_i + d*b_i + e*a_i + [i==0]*d*e) % 101`` 独立核算，
  转录机本身不参与期望生成；
- 两个合成会话 ``sA`` / ``sB`` 交错投递、乱序、整批重放；
- 三元组领取后跨会话/跨操作互斥，冲突只中止涉事操作。
"""

import random
import unittest

from sss_mult import (
    DEFAULT_SESSION_ID,
    ConflictError,
    FieldTypeError,
    IdentifierError,
    IncompleteTranscriptError,
    Message,
    OperationAbortedError,
    Transcript,
    TripleBank,
    TripleMismatchError,
    TripleReusedError,
    UnknownOperationError,
    honest_prepare,
    make_triple,
)

P = 101
SEED = 20260924
SA = "sA"
SB = "sB"


def bank_with(specs):
    """specs: list of (triple_id, a, b)。"""
    bank = TripleBank()
    rng = random.Random(SEED)
    for tid, a, b in specs:
        bank.register(make_triple(tid, a, b, rng))
    return bank, rng


def plain_party_shares(triple, d, e):
    """独立参考：由公开 d,e 与三元组分量朴素计算各方 z_i。"""
    de = (d * e) % P
    out = []
    for i in range(3):
        a_i, b_i, c_i = triple.party_view(i)
        out.append((c_i + d * b_i + e * a_i + (de if i == 0 else 0)) % P)
    return tuple(out)


class SessionBindingTest(unittest.TestCase):
    def test_message_carries_session_id_with_default_compatibility(self):
        m = Message("op", 1, "t", 3, 4)
        self.assertEqual(m.session_id, DEFAULT_SESSION_ID)
        m2 = Message("op", 1, "t", 3, 4, session_id="sA")
        self.assertEqual(m2.session_id, "sA")
        # 除 session_id 外字段相同也不是同一条消息（会话不同）。
        self.assertNotEqual(m, m2)
        # 旧的 5 参位置构造仍可用；session_id 也可按第 6 个位置参数传。
        self.assertEqual(Message("op", 1, "t", 3, 4, "sX").session_id, "sX")

    def test_bad_session_id_rejected_at_construction(self):
        for bad in ("", 7, None, True):
            with self.subTest(bad=bad):
                with self.assertRaises((IdentifierError, FieldTypeError)):
                    Message("op", 0, "t", 0, 0, session_id=bad)

    def test_read_apis_require_valid_session_id(self):
        bank, _ = bank_with([])
        t = Transcript(bank)
        with self.assertRaises(IdentifierError):
            t.is_complete("op", session_id="")
        with self.assertRaises(FieldTypeError):
            t.open("op", session_id=123)
        with self.assertRaises(IdentifierError):
            t.messages_count(session_id="")


class TwoSessionInterleavingTest(unittest.TestCase):
    def setUp(self):
        # 两个会话各一个同名操作 op="mul"，各领一个三元组。
        self.bank, self.rng = bank_with([
            ("tA", 17, 9),
            ("tB", 33, 25),
            ("tC", 5, 6),
        ])
        self.xA, self.yA = 12, 8
        self.xB, self.yB = 11, 7
        _, _, self.msgsA = honest_prepare(
            "mul", self.bank.get("tA"), self.xA, self.yA, self.rng, session_id=SA
        )
        _, _, self.msgsB = honest_prepare(
            "mul", self.bank.get("tB"), self.xB, self.yB, self.rng, session_id=SB
        )

    def test_interleaved_out_of_order_delivery_across_sessions(self):
        t = Transcript(self.bank)
        # 严格交错且各自乱序：A2, B1, A0, B2, B0, A1。
        order = [
            (SA, self.msgsA[2]), (SB, self.msgsB[1]),
            (SA, self.msgsA[0]), (SB, self.msgsB[2]),
            (SB, self.msgsB[0]), (SA, self.msgsA[1]),
        ]
        for sid, m in order:
            self.assertEqual(t.add_message(m), "accepted")
            # 投递过程中两个会话的缺方集合互不干扰。
        self.assertTrue(t.is_complete("mul", session_id=SA))
        self.assertTrue(t.is_complete("mul", session_id=SB))
        self.assertEqual(t.sessions_count(), 2)
        self.assertEqual(t.operations_count(), 2)
        self.assertEqual(t.operations_count(SA), 1)
        self.assertEqual(t.messages_count(SA), 3)
        self.assertEqual(t.messages_count(SB), 3)

        rA = t.open("mul", session_id=SA)
        rB = t.open("mul", session_id=SB)
        # 结果对象记住自己的会话。
        self.assertEqual(rA.session_id, SA)
        self.assertEqual(rB.session_id, SB)
        self.assertEqual(rA.triple_id, "tA")
        self.assertEqual(rB.triple_id, "tB")
        # 独立朴素预言。
        self.assertEqual(rA.product, (self.xA * self.yA) % P)
        self.assertEqual(rB.product, (self.xB * self.yB) % P)
        # 各方份额独立朴素公式核验（含 d*e 只给 0 号）。
        for res, triple in ((rA, self.bank.get("tA")), (rB, self.bank.get("tB"))):
            expect = plain_party_shares(triple, res.d, res.e)
            self.assertEqual(res.output_shares(), expect)
            for i, v in enumerate(res.views):
                self.assertEqual(v.z_i, expect[i])
                self.assertEqual((v.d_i, v.e_i), (
                    (self.msgsA if res.session_id == SA else self.msgsB)[i].d,
                    (self.msgsA if res.session_id == SA else self.msgsB)[i].e,
                ))
        # 两个三元组都被各自操作消费（不可跨操作复用）。
        self.assertEqual(self.bank.state("tA"), "consumed")
        self.assertEqual(self.bank.state("tB"), "consumed")

    def test_default_session_is_isolated_from_named_sessions(self):
        # 不带 session_id 的旧调用进入 default，与 sA 互不可见。
        bank, rng = bank_with([("d0", 1, 2), ("n0", 3, 4)])
        _, _, m_default = honest_prepare("op", bank.get("d0"), 5, 6, rng)
        _, _, m_named = honest_prepare(
            "op", bank.get("n0"), 7, 8, rng, session_id=SA
        )
        t = Transcript(bank)
        t.add_messages(m_default)
        t.add_messages(m_named)
        self.assertEqual(t.open("op").product, 30)           # default
        self.assertEqual(t.open("op", session_id=SA).product, (7 * 8) % P)
        # 各会话只看到自己的操作。
        self.assertEqual(t.missing_parties("op", SA), ())
        with self.assertRaises(UnknownOperationError):
            t.result("op", session_id=SB)

    def test_triple_claim_is_global_across_sessions(self):
        t = Transcript(self.bank)
        t.add_message(self.msgsA[0])  # sA 领取 tA
        self.assertEqual(self.bank.state("tA"), "reserved")
        # sB 的新操作引用 tA：跨会话领取被拒，holder 指明 sA/mul。
        _, _, grab = honest_prepare(
            "other", self.bank.get("tA"), 3, 4, self.rng, session_id=SB
        )
        with self.assertRaises(TripleReusedError) as cm:
            t.add_message(grab[0])
        self.assertFalse(cm.exception.consumed)
        self.assertFalse(cm.exception.voided)
        self.assertEqual(cm.exception.holder, "mul")
        self.assertEqual(cm.exception.holder_session, SA)
        # 被拒操作未创建；sA 操作不受影响，补齐后正常打开。
        self.assertEqual(t.operations_count(), 1)
        t.add_messages([self.msgsA[1], self.msgsA[2]])
        self.assertEqual(t.open("mul", session_id=SA).product,
                         (self.xA * self.yA) % P)
        # 打开消费后再领取：consumed=True。
        _, _, grab2 = honest_prepare(
            "other2", self.bank.get("tA"), 3, 4, self.rng, session_id=SB
        )
        with self.assertRaises(TripleReusedError) as cm2:
            t.add_messages(grab2)
        self.assertTrue(cm2.exception.consumed)
        self.assertEqual(cm2.exception.holder_session, SA)
        self.assertEqual(t.operations_count(), 1)

    def test_triple_cannot_be_reused_between_serial_ops_in_one_session(self):
        t = Transcript(self.bank)
        t.add_messages(self.msgsA)
        self.assertEqual(t.open("mul", SA).product, (self.xA * self.yA) % P)
        # 同会话新 op 复用已消费的 tA。
        _, _, again = honest_prepare(
            "mul2", self.bank.get("tA"), 1, 1, self.rng, session_id=SA
        )
        with self.assertRaises(TripleReusedError) as cm:
            t.add_messages(again)
        self.assertTrue(cm.exception.consumed)
        self.assertEqual(t.operations_count(SA), 1)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.bank, self.rng = bank_with([("rA", 17, 9), ("rB", 33, 25)])
        _, _, self.mA = honest_prepare(
            "mul", self.bank.get("rA"), 12, 8, self.rng, session_id=SA
        )
        _, _, self.mB = honest_prepare(
            "mul", self.bank.get("rB"), 11, 7, self.rng, session_id=SB
        )

    def test_full_replay_is_idempotent_before_and_after_open(self):
        t = Transcript(self.bank)
        # 首轮交错投递。
        stream = [self.mA[2], self.mB[0], self.mA[0], self.mB[2],
                  self.mB[1], self.mA[1]]
        self.assertEqual([t.add_message(m) for m in stream],
                         ["accepted"] * 6)
        # 整批重放（相同重复）：全部 duplicate，计数不增。
        replay = [self.mB[1], self.mA[1], self.mA[0], self.mB[0],
                  self.mA[2], self.mB[2]]
        self.assertEqual([t.add_message(m) for m in replay],
                         ["duplicate"] * 6)
        self.assertEqual(t.messages_count(), 6)
        rA = t.open("mul", SA)
        rB = t.open("mul", SB)
        # 打开后再重放消息仍幂等，重复打开返回同一缓存对象。
        self.assertEqual([t.add_message(m) for m in self.mA],
                         ["duplicate"] * 3)
        self.assertIs(t.open("mul", SA), rA)
        self.assertIs(t.open("mul", SB), rB)
        self.assertEqual(rA.product, (12 * 8) % P)
        self.assertEqual(rB.product, (11 * 7) % P)

    def test_replay_after_missing_party_then_complete(self):
        t = Transcript(self.bank)
        t.add_message(self.mA[0])
        t.add_message(self.mA[2])
        with self.assertRaises(IncompleteTranscriptError) as cm:
            t.open("mul", SA)
        self.assertEqual(cm.exception.missing, (1,))
        self.assertEqual(cm.exception.session_id, SA)
        # 重放已有的 P0 不改变缺方集合；补 P1 后打开。
        self.assertEqual(t.add_message(self.mA[0]), "duplicate")
        self.assertEqual(t.missing_parties("mul", SA), (1,))
        t.add_message(self.mA[1])
        self.assertEqual(t.open("mul", SA).product, (12 * 8) % P)
        self.assertEqual(self.bank.state("rA"), "consumed")


class ConflictAbortSessionScopeTest(unittest.TestCase):
    def setUp(self):
        self.bank, self.rng = bank_with([
            ("cA", 2, 3), ("cB", 4, 5), ("cA2", 6, 7),
        ])
        _, _, self.mA = honest_prepare(
            "mul", self.bank.get("cA"), 4, 5, self.rng, session_id=SA
        )
        _, _, self.mB = honest_prepare(
            "mul", self.bank.get("cB"), 9, 3, self.rng, session_id=SB
        )

    def test_conflict_in_one_session_does_not_touch_the_other(self):
        t = Transcript(self.bank)
        # 两会话交错：A 收到 P0/P2，B 收到 P0/P1。
        t.add_message(self.mA[0])
        t.add_message(self.mB[0])
        t.add_message(self.mA[2])
        t.add_message(self.mB[1])
        # A 发生冲突（伪造 P0 的 d）。
        clash = Message("mul", 0, "cA", (self.mA[0].d + 1) % P,
                        self.mA[0].e, session_id=SA)
        with self.assertRaises(ConflictError):
            t.add_message(clash)
        # 只有 sA/mul 中止；sB/mul 完好无损。
        self.assertTrue(t.is_aborted("mul", SA))
        self.assertFalse(t.is_aborted("mul", SB))
        self.assertEqual(self.bank.state("cA"), "voided")
        self.assertEqual(self.bank.state("cB"), "reserved")
        self.assertIsNotNone(t.abort_reason("mul", SA))
        self.assertIsNone(t.abort_reason("mul", SB))
        # B 补齐 P2 正常打开，且 A 的中止没有污染 B 的计数/缓冲。
        t.add_message(self.mB[2])
        resB = t.open("mul", SB)
        self.assertEqual(resB.product, (9 * 3) % P)
        self.assertEqual(self.bank.state("cB"), "consumed")
        # A 在任何重放下都保持中止。
        with self.assertRaises(OperationAbortedError):
            t.add_message(self.mA[0])
        with self.assertRaises(OperationAbortedError):
            t.add_message(self.mA[1])
        with self.assertRaises(OperationAbortedError):
            t.open("mul", SA)

    def test_same_op_id_conflict_isolated_per_session(self):
        # 相同 (op_id, party) 的冲突消息只在所属会话内中止。
        t = Transcript(self.bank)
        t.add_messages(self.mB)
        clash_B = Message("mul", 1, "cB", self.mB[1].d,
                          (self.mB[1].e + 2) % P, session_id=SB)
        with self.assertRaises(ConflictError):
            t.add_message(clash_B)
        self.assertTrue(t.is_aborted("mul", SB))
        # sA 的同名操作仍可正常建立、打开（用独立三元组）。
        t.add_messages(self.mA)
        self.assertEqual(t.open("mul", SA).product, 20)
        self.assertEqual(self.bank.state("cA"), "consumed")
        self.assertEqual(self.bank.state("cB"), "voided")

    def test_mismatched_triple_in_named_session_aborts_only_that_op(self):
        t = Transcript(self.bank)
        t.add_message(self.mA[0])
        # 伪造一条引用 cB 的消息投给 sA/mul。
        fake = Message("mul", 1, "cB", 0, 0, session_id=SA)
        with self.assertRaises(TripleMismatchError):
            t.add_message(fake)
        self.assertTrue(t.is_aborted("mul", SA))
        self.assertEqual(self.bank.state("cA"), "voided")
        # 冲突消息引用的 cB 从未被该操作领取：仍空闲，可供 sB 使用。
        self.assertEqual(self.bank.state("cB"), "free")
        t.add_messages(self.mB)
        self.assertEqual(t.open("mul", SB).product, (9 * 3) % P)

    def test_aborted_op_blocks_replay_inside_batch_and_rolls_back_new(self):
        t = Transcript(self.bank)
        t.add_messages(self.mA)  # sA/mul 完整
        clash = Message("mul", 2, "cA", self.mA[2].d,
                        (self.mA[2].e + 1) % P, session_id=SA)
        # 批次夹带：一个新会话操作（领 cA2）+ 对 sA/mul 的冲突。
        _, _, m_other = honest_prepare(
            "n", self.bank.get("cA2"), 2, 2, self.rng, session_id=SB
        )
        with self.assertRaises(ConflictError):
            t.add_messages([m_other[0], clash, m_other[1]])
        # sA/mul 中止且 cA 作废（终态）；为 sB/n 做的预留与消息全部回滚。
        self.assertTrue(t.is_aborted("mul", SA))
        self.assertEqual(self.bank.state("cA"), "voided")
        self.assertEqual(self.bank.state("cA2"), "free")
        self.assertEqual(t.operations_count(), 1)
        self.assertEqual(t.missing_parties("n", SB), (0, 1, 2))

    def test_batch_two_new_ops_same_session_failure_rolls_both_back(self):
        # 同一会话内批次新建两个操作，第三条因三元组被占失败：
        # 两个新操作与其预留都必须回滚（旧实现会在空会话字典上出错）。
        bank, rng = bank_with([("k1", 1, 1), ("k2", 2, 2), ("khot", 3, 3)])
        t = Transcript(bank)
        # khot 先被既有操作领取。
        _, _, hold = honest_prepare("holder", bank.get("khot"), 1, 1, rng,
                                    session_id=SA)
        t.add_messages(hold)
        _, _, n1 = honest_prepare("n1", bank.get("k1"), 2, 3, rng,
                                  session_id=SB)
        _, _, n2 = honest_prepare("n2", bank.get("k2"), 4, 5, rng,
                                  session_id=SB)
        _, _, grab = honest_prepare("n3", bank.get("khot"), 6, 7, rng,
                                    session_id=SB)
        with self.assertRaises(TripleReusedError):
            t.add_messages([n1[0], n2[0], n2[1], grab[0]])
        self.assertEqual(t.operations_count(), 1)  # 只剩 holder
        self.assertEqual(t.operations_count(SB), 0)
        self.assertEqual(bank.state("k1"), "free")
        self.assertEqual(bank.state("k2"), "free")
        self.assertEqual(bank.state("khot"), "reserved")
        # 回滚后的三元组仍可正常使用。
        _, _, again = honest_prepare("n1", bank.get("k1"), 2, 3, rng,
                                     session_id=SB)
        t.add_messages(again)
        self.assertEqual(t.open("n1", SB).product, 6)

    def test_conflict_after_open_aborts_but_keeps_triple_consumed(self):
        # 边界：操作已成功打开（三元组 consumed）后又收到冲突重复。
        # 操作仍须中止，但消费不可撤销：三元组保持 consumed。
        t = Transcript(self.bank)
        t.add_messages(self.mB)
        res = t.open("mul", SB)
        self.assertEqual(res.product, (9 * 3) % P)
        self.assertEqual(self.bank.state("cB"), "consumed")
        clash = Message("mul", 0, "cB", (self.mB[0].d + 1) % P,
                        self.mB[0].e, session_id=SB)
        with self.assertRaises(ConflictError):
            t.add_message(clash)
        self.assertTrue(t.is_aborted("mul", SB))
        self.assertFalse(t.is_open("mul", SB))
        self.assertEqual(self.bank.state("cB"), "consumed")
        with self.assertRaises(OperationAbortedError):
            t.open("mul", SB)
        with self.assertRaises(OperationAbortedError):
            t.result("mul", SB)
        # consumed 三元组本来就不能被任何操作再领取。
        _, _, grab = honest_prepare(
            "z", self.bank.get("cB"), 1, 1, self.rng, session_id=SA
        )
        with self.assertRaises(TripleReusedError) as cm:
            t.add_messages(grab)
        self.assertTrue(cm.exception.consumed)


class HandComputedExampleTest(unittest.TestCase):
    def test_small_hand_computed_two_session_example(self):
        # 完全手算的小例子（随机分享分量仍由库生成，但所有期望值用
        # 朴素算术在本测试内独立计算）。
        bank = TripleBank()
        rng = random.Random(4242)
        # 选 a=0,b=0 的三元组：d=x, e=y，便于手算公开值。
        bank.register(make_triple("hA", 0, 0, rng))
        bank.register(make_triple("hB", 1, 1, rng))
        x, y = 3, 4
        _, _, msgs = honest_prepare(
            "m", bank.get("hA"), x, y, rng, session_id=SA
        )
        t = Transcript(bank)
        # 乱序：1, 2, 0。
        for i in (1, 2, 0):
            t.add_message(msgs[i])
        res = t.open("m", SA)
        triple = bank.get("hA")
        # 独立手算：d = Σ(x_i - a_i) = x - a = 3；e = 4。
        d = sum(m.d for m in msgs) % P
        e = sum(m.e for m in msgs) % P
        self.assertEqual((d, e), (3, 4))
        self.assertEqual((res.x, res.y), (3, 4))
        self.assertEqual(res.product, 12)
        self.assertEqual(res.output_shares(), plain_party_shares(triple, 3, 4))
        self.assertEqual(sum(res.output_shares()) % P, 12)

        # 第二个会话用 hB（a=b=1），x=2,y=2 => d=e=1, product=4。
        _, _, msgs2 = honest_prepare(
            "m", bank.get("hB"), 2, 2, rng, session_id=SB
        )
        t.add_messages(msgs2)
        r2 = t.open("m", SB)
        self.assertEqual((r2.d, r2.e), (1, 1))
        self.assertEqual((r2.x, r2.y), (2, 2))
        self.assertEqual(r2.product, 4)
        # d*e = 1 只进入 P0：z_0 比朴素 c_0+b_0+a_0 恰好多 1。
        triple2 = bank.get("hB")
        base = [
            (triple2.c_shares[i] + triple2.b_shares[i] + triple2.a_shares[i]) % P
            for i in range(3)
        ]
        shares = r2.output_shares()
        self.assertEqual(shares[0], (base[0] + 1) % P)
        self.assertEqual(shares[1], base[1])
        self.assertEqual(shares[2], base[2])


if __name__ == "__main__":
    unittest.main()
