"""会话缓冲层测试：会话/操作/参与方绑定、乱序、幂等重放、冲突中止、
三元组跨操作跨会话一次性领取。

所有协议期望值（``d/e/z_i/product``）均为**手算字面量**，三元组与分享
也是手算夹具（直接构造 :class:`BeaverTriple` / :class:`Message`），
不调用被测实现生成期望值。

手算明细（P = 101）：

会话 ``alpha``（三元组 ``TA``：a=10, b=20, c=a*b=200 mod 101 = 99）：
    a 分享 (2, 3, 5)；b 分享 (2, 7, 11)；c 分享 (29, 30, 40)
    x=6 分享 (3, 1, 2)；y=7 分享 (99, 4, 5)
    d_i = x_i-a_i：(1, 99, 98)，d = 198 mod 101 = 97（a+d=107≡6）
    e_i = y_i-b_i：(97, 98, 95)，e = 290 mod 101 = 88（b+e=108≡7）
    d*e = 97*88 = 8536 ≡ 52
    z0 = 29 + 97*2 + 88*2 + 52 = 451 ≡ 47
    z1 = 30 + 97*7 + 88*3 = 973 ≡ 64
    z2 = 40 + 97*11 + 88*5 = 1547 ≡ 32
    z0+z1+z2 = 143 ≡ 42 = 6*7 mod 101

会话 ``beta``（三元组 ``TB``：a=4, b=5, c=20）：
    a 分享 (1, 2, 1)；b 分享 (0, 3, 2)；c 分享 (5, 8, 7)
    x=9 分享 (3, 3, 3)；y=8 分享 (0, 2, 6)
    d = 2+1+2 = 5；e = 0+100+4 = 104 ≡ 3；d*e = 15
    z0 = 5 + 5*0 + 3*1 + 15 = 23
    z1 = 8 + 5*3 + 3*2 = 29
    z2 = 7 + 5*2 + 3*1 = 20
    和 = 72 = 9*8
"""

import random
import unittest

from sss_mult import (
    DEFAULT_SESSION_ID,
    BeaverTriple,
    ConflictError,
    FieldTypeError,
    IdentifierError,
    IncompleteTranscriptError,
    LimitError,
    Message,
    OperationAbortedError,
    SessionMismatchError,
    Transcript,
    TripleBank,
    TripleReusedError,
    UnknownOperationError,
    UnknownTripleError,
    honest_prepare,
    make_triple,
)

P = 101
ALPHA = "alpha"
BETA = "beta"

# ---- 手算夹具 -----------------------------------------------------------

TA = BeaverTriple(
    triple_id="TA", a=10, b=20, c=99,
    a_shares=(2, 3, 5), b_shares=(2, 7, 11), c_shares=(29, 30, 40),
)
TB = BeaverTriple(
    triple_id="TB", a=4, b=5, c=20,
    a_shares=(1, 2, 1), b_shares=(0, 3, 2), c_shares=(5, 8, 7),
)

ALPHA_X_SHARES = (3, 1, 2)
ALPHA_Y_SHARES = (99, 4, 5)
ALPHA_D = (1, 99, 98)
ALPHA_E = (97, 98, 95)
ALPHA_D_PUB, ALPHA_E_PUB = 97, 88
ALPHA_Z = (47, 64, 32)
ALPHA_PRODUCT = 42

BETA_X_SHARES = (3, 3, 3)
BETA_Y_SHARES = (0, 2, 6)
BETA_D = (2, 1, 2)
BETA_E = (0, 100, 4)
BETA_D_PUB, BETA_E_PUB = 5, 3
BETA_Z = (23, 29, 20)
BETA_PRODUCT = 72


def alpha_messages(op_id="mul"):
    return tuple(
        Message(op_id, i, "TA", ALPHA_D[i], ALPHA_E[i], ALPHA) for i in range(3)
    )


def beta_messages(op_id="mul"):
    return tuple(
        Message(op_id, i, "TB", BETA_D[i], BETA_E[i], BETA) for i in range(3)
    )


def build_transcript(*triples):
    bank = TripleBank()
    for t in triples:
        bank.register(t)
    return Transcript(bank), bank


def assert_hand_alpha(test, res):
    test.assertEqual(res.session_id, ALPHA)
    test.assertEqual(res.triple_id, "TA")
    test.assertEqual(res.d, ALPHA_D_PUB)
    test.assertEqual(res.e, ALPHA_E_PUB)
    test.assertEqual(res.x, 6)
    test.assertEqual(res.y, 7)
    test.assertEqual(res.output_shares(), ALPHA_Z)
    test.assertEqual(res.product, ALPHA_PRODUCT)
    # 独立朴素参考。
    test.assertEqual(res.product, (6 * 7) % P)
    for i, v in enumerate(res.views):
        test.assertEqual(v.party, i)
        test.assertEqual(v.d_i, ALPHA_D[i])
        test.assertEqual(v.e_i, ALPHA_E[i])
        test.assertEqual(v.z_i, ALPHA_Z[i])


def assert_hand_beta(test, res):
    test.assertEqual(res.session_id, BETA)
    test.assertEqual(res.d, BETA_D_PUB)
    test.assertEqual(res.e, BETA_E_PUB)
    test.assertEqual(res.output_shares(), BETA_Z)
    test.assertEqual(res.product, BETA_PRODUCT)
    test.assertEqual(res.product, (9 * 8) % P)


# ---- 正常路径：绑定、乱序、交错 -----------------------------------------

class SessionBindingTest(unittest.TestCase):
    def test_message_carries_session_and_default_session(self):
        m = Message("op", 0, "TA", 1, 2)  # 旧的位置参数构造
        self.assertEqual(m.session_id, DEFAULT_SESSION_ID)
        m2 = Message("op", 0, "TA", 1, 2, ALPHA)
        self.assertEqual(m2.session_id, ALPHA)
        self.assertNotEqual(m, m2)  # 会话不同即不同消息
        with self.assertRaises(IdentifierError):
            Message("op", 0, "TA", 1, 2, "")
        with self.assertRaises(FieldTypeError):
            Message("op", 0, "TA", 1, 2, 7)

    def test_hand_computed_interleaved_sessions_out_of_order(self):
        t, bank = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma, mb = alpha_messages(), beta_messages()

        # 两个会话的消息任意交错、乱序投递。
        schedule = [ma[0], mb[0], ma[1], mb[1], ma[0], mb[2], ma[2], mb[0]]
        statuses = []
        for m in schedule:
            statuses.append((sa if m.session_id == ALPHA else sb).add_message(m))
        # 交错序列中第 5、第 8 条是重放（索引 4、7）。
        self.assertEqual(
            statuses,
            ["accepted", "accepted", "accepted", "accepted",
             "duplicate", "accepted", "accepted", "duplicate"],
        )
        self.assertTrue(sa.is_complete("mul"))
        self.assertTrue(sb.is_complete("mul"))
        assert_hand_alpha(self, sa.open("mul"))
        assert_hand_beta(self, sb.open("mul"))
        self.assertEqual(bank.state("TA"), "consumed")
        self.assertEqual(bank.state("TB"), "consumed")

    def test_same_op_id_in_two_sessions_is_independent(self):
        t, bank = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        for m in alpha_messages("same-name"):
            sa.add_message(m)
        for m in beta_messages("same-name"):
            sb.add_message(m)
        ra = sa.open("same-name")
        rb = sb.open("same-name")
        self.assertEqual(ra.product, ALPHA_PRODUCT)
        self.assertEqual(rb.product, BETA_PRODUCT)
        self.assertIsNot(ra, rb)
        # 转录机层面按 (session, op) 区分，共 2 个操作。
        self.assertEqual(t.operations_count(), 2)

    def test_default_session_transcript_and_session_view_interoperate(self):
        # 旧入口 Transcript.add_message 与 Session("default") 看到同一操作。
        t, bank = build_transcript(TA)
        msgs = alpha_messages("legacy")
        default_msgs = [
            Message(m.op_id, m.party, m.triple_id, m.d, m.e) for m in msgs
        ]
        t.add_message(default_msgs[0])
        sd = t.session(DEFAULT_SESSION_ID)
        self.assertEqual(sd.add_message(default_msgs[1]), "accepted")
        sd.add_message(default_msgs[2])
        res_old = t.open("legacy")
        res_session = sd.open("legacy")
        self.assertIs(res_old, res_session)
        self.assertEqual(res_old.product, ALPHA_PRODUCT)
        self.assertEqual(res_old.session_id, DEFAULT_SESSION_ID)

    def test_honest_prepare_binds_session_keyword(self):
        bank = TripleBank()
        rng = random.Random(20260924)
        bank.register(make_triple("h1", 11, 13, rng))
        t = Transcript(bank)
        s = t.session("honest")
        _, _, msgs = honest_prepare("hm", bank.get("h1"), 5, 9, rng, session_id="honest")
        self.assertTrue(all(m.session_id == "honest" for m in msgs))
        s.add_messages(msgs)
        res = s.open("hm")
        self.assertEqual(res.product, (5 * 9) % P)


# ---- 重放核验 -----------------------------------------------------------

class ReplayTest(unittest.TestCase):
    def test_replay_after_open_is_idempotent(self):
        t, _ = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma, mb = alpha_messages(), beta_messages()
        for m in ma:
            sa.add_message(m)
        for m in mb:
            sb.add_message(m)
        ra1 = sa.open("mul")
        rb1 = sb.open("mul")
        # 消息级重放：全部再投一遍，全部幂等，计数不变。
        for m in ma:
            self.assertEqual(sa.add_message(m), "duplicate")
        for m in mb:
            self.assertEqual(sb.add_message(m), "duplicate")
        self.assertEqual(sa.messages_count(), 3)
        self.assertEqual(sb.messages_count(), 3)
        # 打开级重放：缓存同一结果。
        self.assertIs(sa.open("mul"), ra1)
        self.assertIs(sb.open("mul"), rb1)

    def test_full_interleaved_stream_replayed_into_fresh_transcript(self):
        # 转录级重放：同一组消息流在全新的转录机/银行上重放，结果逐位一致。
        def run():
            t, _ = build_transcript(
                BeaverTriple("TA", TA.a, TA.b, TA.c, TA.a_shares, TA.b_shares, TA.c_shares),
                BeaverTriple("TB", TB.a, TB.b, TB.c, TB.a_shares, TB.b_shares, TB.c_shares),
            )
            sa, sb = t.session(ALPHA), t.session(BETA)
            stream = [alpha_messages()[0], beta_messages()[1], alpha_messages()[2],
                      beta_messages()[0], alpha_messages()[1], beta_messages()[2]]
            for m in stream:
                (sa if m.session_id == ALPHA else sb).add_message(m)
            return sa.open("mul"), sb.open("mul")

        ra, rb = run()
        ra2, rb2 = run()
        self.assertEqual(ra.output_shares(), ALPHA_Z)
        self.assertEqual(rb.output_shares(), BETA_Z)
        self.assertEqual(ra2.output_shares(), ra.output_shares())
        self.assertEqual(rb2.output_shares(), rb.output_shares())


# ---- 路由与缺方等失败边界 -----------------------------------------------

class RoutingAndIncompleteTest(unittest.TestCase):
    def test_wrong_session_rejected_without_state_change(self):
        t, bank = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma = alpha_messages()
        # alpha 的消息误投到 beta。
        with self.assertRaises(SessionMismatchError) as cm:
            sb.add_message(ma[0])
        self.assertEqual(cm.exception.expected_session, BETA)
        self.assertEqual(cm.exception.message_session, ALPHA)
        # 无任何状态变更：两个会话都没有该操作，三元组仍空闲。
        self.assertEqual(sb.operations_count(), 0)
        self.assertEqual(sa.operations_count(), 0)
        self.assertEqual(t.messages_count(), 0)
        self.assertEqual(bank.state("TA"), "free")
        # 改投正确会话即可成功。
        self.assertEqual(sa.add_message(ma[0]), "accepted")

    def test_batch_wrong_session_rejected_as_a_unit(self):
        t, bank = build_transcript(TA, TB)
        sa = t.session(ALPHA)
        good = alpha_messages()
        smuggled = Message("mul", 1, "TB", BETA_D[1], BETA_E[1], BETA)
        with self.assertRaises(SessionMismatchError):
            sa.add_messages([good[0], smuggled, good[1]])
        # 整批未落盘。
        self.assertEqual(t.messages_count(), 0)
        self.assertEqual(bank.state("TA"), "free")
        self.assertEqual(sa.missing_parties("mul"), (0, 1, 2))

    def test_missing_party_open_leaves_no_change_then_completable(self):
        t, bank = build_transcript(TA)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_messages([ma[2], ma[0]])
        self.assertEqual(sa.missing_parties("mul"), (1,))
        with self.assertRaises(IncompleteTranscriptError) as cm:
            sa.open("mul")
        self.assertEqual(cm.exception.missing, (1,))
        self.assertFalse(sa.is_open("mul"))
        self.assertEqual(bank.state("TA"), "reserved")  # 未消费
        sa.add_message(ma[1])
        assert_hand_alpha(self, sa.open("mul"))

    def test_open_unknown_operation_in_session(self):
        t, _ = build_transcript(TA)
        sa = t.session(ALPHA)
        with self.assertRaises(UnknownOperationError):
            sa.open("ghost")

    def test_unknown_triple_in_session(self):
        t, _ = build_transcript(TA)
        sa = t.session(ALPHA)
        with self.assertRaises(UnknownTripleError):
            sa.add_message(Message("mul", 0, "nope", 1, 1, ALPHA))

    def test_session_id_validation(self):
        t, _ = build_transcript(TA)
        with self.assertRaises(IdentifierError):
            t.session("")
        with self.assertRaises(FieldTypeError):
            t.session(7)
        with self.assertRaises(TypeError):
            t.session(ALPHA).__class__("ok", object())


# ---- 冲突重复：中止操作、烧毁三元组、隔离 -------------------------------

class ConflictAbortTest(unittest.TestCase):
    def test_conflicting_duplicate_aborts_operation_and_burns_triple(self):
        t, bank = build_transcript(TA)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_messages([ma[0], ma[1]])
        # P0 的冲突重复：d 被篡改。
        bad = Message("mul", 0, "TA", (ALPHA_D[0] + 1) % P, ALPHA_E[0], ALPHA)
        with self.assertRaises(OperationAbortedError) as cm:
            sa.add_message(bad)
        self.assertEqual(cm.exception.session_id, ALPHA)
        self.assertEqual(cm.exception.op_id, "mul")
        self.assertEqual(cm.exception.party, 0)
        # 终态观察。
        self.assertTrue(sa.is_aborted("mul"))
        self.assertEqual(sa.aborted_operations(), ("mul",))
        self.assertIn("d=", sa.abort_reason("mul"))
        self.assertEqual(bank.state("TA"), "aborted")
        # 冲突消息本身未落盘，缓冲停在冲突前的 2 条。
        self.assertEqual(sa.messages_count(), 2)

    def test_aborted_operation_is_permanently_closed(self):
        t, bank = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma = alpha_messages()
        sa.add_message(ma[0])
        with self.assertRaises(OperationAbortedError):
            sa.add_message(Message("mul", 0, "TA", 0, 0, ALPHA))
        # 即使是完全相同的重复、其他方的补票、打开——一律再拒。
        with self.assertRaises(OperationAbortedError):
            sa.add_message(ma[0])
        with self.assertRaises(OperationAbortedError):
            sa.add_message(ma[1])
        with self.assertRaises(OperationAbortedError):
            sa.open("mul")
        with self.assertRaises(OperationAbortedError):
            sa.result("mul")
        self.assertEqual(sa.missing_parties("mul"), (0, 1, 2))

    def test_burned_triple_cannot_be_claimed_again(self):
        t, bank = build_transcript(TA)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_message(ma[0])
        with self.assertRaises(OperationAbortedError):
            sa.add_message(Message("mul", 0, "TA", 5, 5, ALPHA))
        # 同会话的另一个操作不能领取烧毁的三元组。
        other = alpha_messages("other")
        with self.assertRaises(TripleReusedError) as cm:
            sa.add_messages(other)
        self.assertTrue(cm.exception.consumed)
        self.assertEqual(cm.exception.terminal_state, "aborted")
        # 批次失败未半更新：other 未创建。
        self.assertEqual(sa.operations_count(), 1)
        # 直接向银行领取同样被拒。
        with self.assertRaises(TripleReusedError):
            bank.reserve("TA", "x", ALPHA)

    def test_abort_is_isolated_from_other_ops_and_sessions(self):
        t, bank = build_transcript(TA, TB)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma, mb = alpha_messages(), beta_messages()
        # 交错：beta 先投两条，alpha 发生冲突中止。
        sb.add_message(mb[0])
        sa.add_message(ma[0])
        sb.add_message(mb[1])
        with self.assertRaises(OperationAbortedError):
            sa.add_message(Message("mul", 0, "TA", 11, ALPHA_E[0], ALPHA))
        # beta 不受影响，补齐后正常打开。
        sb.add_message(mb[2])
        assert_hand_beta(self, sb.open("mul"))
        self.assertFalse(sb.is_aborted("mul"))
        self.assertEqual(bank.state("TB"), "consumed")
        # alpha 同会话内用新三元组的新操作仍然正常。
        tc = BeaverTriple(
            "TC", 1, 1, 1, (0, 1, 0), (0, 0, 1), (1, 0, 0)
        )
        bank.register(tc)
        # a=b=1,c=1; x=y=2 分享成 (0,1,1),(0,1,1) → 积 4
        cm_msgs = [
            Message("clean", i, "TC",
                    ((0, 1, 1)[i] - (0, 1, 0)[i]) % P,
                    ((0, 1, 1)[i] - (0, 0, 1)[i]) % P,
                    ALPHA)
            for i in range(3)
        ]
        sa.add_messages(cm_msgs)
        res = sa.open("clean")
        self.assertEqual(res.product, 4)

    def test_triple_id_mismatch_aborts_operation(self):
        t, bank = build_transcript(TA, TB)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_message(ma[0])
        # P1 的消息引用了另一个三元组 → 冲突，中止。
        impostor = Message("mul", 1, "TB", ALPHA_D[1], ALPHA_E[1], ALPHA)
        with self.assertRaises(OperationAbortedError):
            sa.add_message(impostor)
        self.assertTrue(sa.is_aborted("mul"))
        self.assertEqual(bank.state("TA"), "aborted")
        # TB 没有被这个操作碰过（仍空闲，可用于正常操作）。
        self.assertEqual(bank.state("TB"), "free")

    def test_conflict_after_open_aborts_and_burns(self):
        # 已打开的操作收到冲突重复：同样中止（终态），三元组烧毁，
        # 之前缓存的结果不再可读。
        t, bank = build_transcript(TA, TB)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_messages(ma)
        res = sa.open("mul")
        self.assertEqual(res.product, ALPHA_PRODUCT)
        bad = Message("mul", 2, "TA", ALPHA_D[2], (ALPHA_E[2] + 1) % P, ALPHA)
        with self.assertRaises(OperationAbortedError):
            sa.add_message(bad)
        self.assertTrue(sa.is_aborted("mul"))
        self.assertFalse(sa.is_open("mul"))
        self.assertEqual(bank.state("TA"), "aborted")
        with self.assertRaises(OperationAbortedError):
            sa.result("mul")
        with self.assertRaises(OperationAbortedError):
            sa.open("mul")

    def test_batch_abort_keeps_terminal_but_rolls_back_other_changes(self):
        t, bank = build_transcript(TA, TB)
        sa = t.session(ALPHA)
        ma = alpha_messages("victim")
        # 批次：新操作 victim 的 P0、新操作 other 的 P0，然后 victim 的冲突 P0。
        other0 = Message("other", 0, "TB", BETA_D[0], BETA_E[0], ALPHA)
        with self.assertRaises(OperationAbortedError):
            sa.add_messages([ma[0], other0,
                             Message("victim", 0, "TA", 40, 40, ALPHA)])
        # victim 保留中止终态（含已落盘的 P0），TA 烧毁。
        self.assertTrue(sa.is_aborted("victim"))
        self.assertEqual(bank.state("TA"), "aborted")
        # other 的本批次变更被整体回滚：操作不存在、TB 仍空闲。
        self.assertEqual(sa.missing_parties("other"), (0, 1, 2))
        self.assertEqual(bank.state("TB"), "free")
        self.assertEqual(sa.operations_count(), 1)


# ---- 三元组一次性：跨操作 / 跨会话 --------------------------------------

class TripleOneShotAcrossSessionsTest(unittest.TestCase):
    def test_reserved_triple_blocks_other_session(self):
        t, bank = build_transcript(TA)
        sa, sb = t.session(ALPHA), t.session(BETA)
        ma = alpha_messages()
        sa.add_message(ma[0])  # alpha 已领取 TA 但未齐方、未打开
        self.assertEqual(bank.state("TA"), "reserved")
        # beta 试图领取同一三元组。
        m = Message("mul", 0, "TA", 1, 1, BETA)
        with self.assertRaises(TripleReusedError) as cm:
            sb.add_message(m)
        self.assertFalse(cm.exception.consumed)
        self.assertIn(ALPHA, cm.exception.holder)
        # beta 操作未创建，alpha 补齐后仍可正常打开。
        self.assertEqual(sb.operations_count(), 0)
        sa.add_messages([ma[1], ma[2]])
        assert_hand_alpha(self, sa.open("mul"))

    def test_consumed_triple_blocks_new_operation(self):
        t, bank = build_transcript(TA)
        sa = t.session(ALPHA)
        ma = alpha_messages()
        sa.add_messages(ma)
        sa.open("mul")
        again = alpha_messages("second")
        with self.assertRaises(TripleReusedError) as cm:
            sa.add_messages(again)
        self.assertTrue(cm.exception.consumed)
        self.assertEqual(cm.exception.terminal_state, "consumed")
        self.assertEqual(sa.operations_count(), 1)

    def test_serial_operations_consume_distinct_triples_interleaved_sessions(self):
        # 两个会话各自两个串操作，交错投递，各自只领取自己的三元组一次。
        bank = TripleBank()
        rng = random.Random(4242)
        for k in range(4):
            bank.register(make_triple(f"R{k}", 10 + k, 20 + k, rng))
        tc = Transcript(bank)
        sa, sb = tc.session(ALPHA), tc.session(BETA)
        prepared = []
        for k in range(4):
            sess = ALPHA if k % 2 == 0 else BETA
            x, y = k + 2, k + 5
            _, _, msgs = honest_prepare(
                f"op{k}", bank.get(f"R{k}"), x, y, rng, session_id=sess
            )
            prepared.append((sess, f"op{k}", f"R{k}", msgs, (x * y) % P))
        # 交错：每个操作先投 P0（全部处于预留态，互不串用），再回头补齐。
        for sess, _, _, msgs, _ in prepared:
            (sa if sess == ALPHA else sb).add_message(msgs[0])
        for sess, _, _, msgs, _ in prepared:
            s = sa if sess == ALPHA else sb
            self.assertEqual(s.add_message(msgs[1]), "accepted")
            self.assertEqual(s.add_message(msgs[2]), "accepted")
        for sess, oid, tid, _, expect in prepared:
            s = sa if sess == ALPHA else sb
            self.assertEqual(s.open(oid).product, expect)
            self.assertEqual(bank.state(tid), "consumed")
        self.assertEqual(tc.operations_count(), 4)


# ---- 上限与旧转录机语义不回归 -------------------------------------------

class LimitsAndLegacySemanticsTest(unittest.TestCase):
    def test_global_operation_limit_spans_sessions(self):
        bank = TripleBank()
        rng = random.Random(7)
        for k in range(3):
            bank.register(make_triple(f"g{k}", 1, 1, rng))
        t = Transcript(bank, max_operations=2, max_messages=100)
        sa, sb = t.session(ALPHA), t.session(BETA)
        _, _, m0 = honest_prepare("a0", bank.get("g0"), 1, 1, rng, session_id=ALPHA)
        _, _, m1 = honest_prepare("b0", bank.get("g1"), 1, 1, rng, session_id=BETA)
        sa.add_message(m0[0])
        sb.add_message(m1[0])
        _, _, m2 = honest_prepare("a1", bank.get("g2"), 1, 1, rng, session_id=ALPHA)
        with self.assertRaises(LimitError):
            sa.add_message(m2[0])
        self.assertEqual(t.operations_count(), 2)
        self.assertEqual(bank.state("g2"), "free")  # 未半更新

    def test_legacy_transcript_still_rejects_conflict_without_aborting(self):
        # 旧契约：直接使用 Transcript 时冲突不落盘、不中止，操作还能补齐。
        bank = TripleBank()
        rng = random.Random(8)
        bank.register(make_triple("L", 3, 4, rng))
        t = Transcript(bank)
        _, _, msgs = honest_prepare("op", bank.get("L"), 5, 6, rng)
        t.add_message(msgs[0])
        bad = Message("op", 0, "L", (msgs[0].d + 1) % P, msgs[0].e)
        with self.assertRaises(ConflictError):
            t.add_message(bad)
        self.assertFalse(t.is_aborted("op"))
        self.assertEqual(bank.state("L"), "reserved")
        t.add_messages([msgs[1], msgs[2]])
        self.assertEqual(t.open("op").product, 30)

    def test_legacy_triple_mismatch_error_type_unchanged(self):
        from sss_mult import TripleMismatchError
        bank = TripleBank()
        rng = random.Random(9)
        bank.register(make_triple("A", 1, 2, rng))
        bank.register(make_triple("B", 3, 4, rng))
        t = Transcript(bank)
        _, _, ma = honest_prepare("op", bank.get("A"), 2, 3, rng)
        _, _, mb = honest_prepare("op", bank.get("B"), 2, 3, rng)
        t.add_message(ma[0])
        with self.assertRaises(TripleMismatchError):
            t.add_message(mb[1])


if __name__ == "__main__":
    unittest.main()
