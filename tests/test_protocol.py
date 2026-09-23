"""转录协议测试：穷举正确性、乱序、缺方、复用、冲突、幂等、串操作。"""

import random
import unittest

from beaver_transcript import (
    Message,
    Party,
    Transcript,
    TripleSource,
    share_secret,
)
from beaver_transcript.errors import (
    FieldValueError,
    IncompleteTranscriptError,
    MessageConflictError,
    ProtocolStateError,
    TripleReuseError,
    UnknownPartyError,
)
from tests.reference import P, ref_beaver_outputs, ref_share


def build_scenario(seed, x, y, a, b, triple_id="T", op_id="M"):
    """搭一套三方转录：返回 (transcript, parties, all_messages, 真值包)。

    真值（d、e、各方 z_i）由 tests.reference 独立计算，不取自被测核心。
    """
    source = TripleSource(random.Random(seed))
    triple_shares = source.issue_values(triple_id, a, b)
    parties = [Party(i) for i in range(3)]
    x_shares = share_secret(x, random.Random(seed * 7 + 1))
    y_shares = share_secret(y, random.Random(seed * 7 + 2))
    # 独立参考对同一分享过程的期望（种子相同）。
    ref_x = ref_share(x, seed * 7 + 1)
    ref_y = ref_share(y, seed * 7 + 2)
    assert ref_x == x_shares and ref_y == y_shares

    transcript = Transcript(parties)
    transcript.begin(op_id, triple_id)
    messages = []
    for i in range(3):
        parties[i].receive_triple(triple_shares[i])
        messages.extend(parties[i].contribute(op_id, triple_id, x_shares[i], y_shares[i]))

    a_shares = [s.a for s in triple_shares]
    b_shares = [s.b for s in triple_shares]
    c_shares = [s.c for s in triple_shares]
    d, e, z_ref = ref_beaver_outputs(
        x, y, a, b, x_shares, y_shares, a_shares, b_shares, c_shares
    )
    truth = {"d": d, "e": e, "z": z_ref, "product": x * y % P}
    return transcript, parties, messages, truth


class ExhaustiveMultiplicationTest(unittest.TestCase):
    def test_all_x_y_pairs(self):
        """穷举 x,y ∈ F_101（a,b 固定），逐字段对照独立参考与域乘法 x*y。"""
        checked = 0
        for x in range(P):
            for y in range(P):
                transcript, _, messages, truth = build_scenario(
                    seed=1000003, x=x, y=y, a=30, b=60,
                    triple_id=f"T-{x}-{y}", op_id=f"M-{x}-{y}",
                )
                transcript.post_bulk(messages)
                result = transcript.open(f"M-{x}-{y}")
                self.assertEqual(result.d, truth["d"])
                self.assertEqual(result.e, truth["e"])
                self.assertEqual(result.d, (x - 30) % P)
                self.assertEqual(result.e, (y - 60) % P)
                self.assertEqual([v.z_i for v in result.views], truth["z"])
                self.assertEqual(result.z, truth["product"])
                checked += 1
        self.assertEqual(checked, P * P)

    def test_sweep_a_b_on_corner_inputs(self):
        """对边角输入再扫一遍全部 a,b，覆盖三元组与输入的各种相对关系。"""
        corners = (0, 1, 50, 100)
        for a in range(P):
            for b in range(P):
                x, y = corners[(a + b) % 4], corners[(a * b) % 4]
                transcript, _, messages, truth = build_scenario(
                    seed=a * P + b + 1, x=x, y=y, a=a, b=b,
                    triple_id=f"TT-{a}-{b}", op_id=f"MM-{a}-{b}",
                )
                transcript.post_bulk(messages)
                result = transcript.open(f"MM-{a}-{b}")
                self.assertEqual(result.z, truth["product"])


class DeliveryOrderTest(unittest.TestCase):
    def test_out_of_order_delivery(self):
        transcript, _, messages, truth = build_scenario(5, 40, 60, 12, 8, "T1", "M1")
        rng = random.Random(99)
        rng.shuffle(messages)
        # 边投递边检查：未齐时不能打开。
        for message in messages[:-1]:
            transcript.post(message)
            self.assertFalse(transcript.is_complete("M1"))
            with self.assertRaises(IncompleteTranscriptError):
                transcript.open("M1")
        transcript.post(messages[-1])
        self.assertTrue(transcript.is_complete("M1"))
        self.assertEqual(transcript.open("M1").z, truth["product"])

    def test_missing_party_blocks_open_then_resumes(self):
        transcript, _, messages, truth = build_scenario(6, 10, 20, 3, 4, "T2", "M2")
        partial = [m for m in messages if m.sender != 2]
        transcript.post_bulk(partial)
        with self.assertRaises(IncompleteTranscriptError) as ctx:
            transcript.open("M2")
        # 错误要能定位：明确指出缺 2 号方的 d、e。
        missing = transcript.missing("M2")
        self.assertEqual(missing, [(2, "d"), (2, "e")])
        self.assertIn("(2, 'd')", str(ctx.exception))
        # 打开失败不改变状态；补齐后正常打开。
        transcript.post_bulk([m for m in messages if m.sender == 2])
        self.assertEqual(transcript.open("M2").z, truth["product"])


class ConflictAndIdempotenceTest(unittest.TestCase):
    def test_identical_duplicate_is_idempotent(self):
        transcript, _, messages, truth = build_scenario(7, 9, 11, 2, 5, "T3", "M3")
        self.assertTrue(transcript.post(messages[0]))
        self.assertFalse(transcript.post(messages[0]))  # 一致重复：幂等
        transcript.post_bulk(messages)
        self.assertEqual(transcript.open("M3").z, truth["product"])

    def test_conflicting_message_rejected_without_state_change(self):
        transcript, _, messages, _ = build_scenario(8, 9, 11, 2, 5, "T4", "M4")
        first = messages[0]
        transcript.post(first)
        bogus = Message(first.op_id, first.sender, first.kind, (first.value + 1) % P)
        with self.assertRaises(MessageConflictError):
            transcript.post(bogus)
        # 冲突消息没有覆盖原值：重新投递一致消息仍然幂等。
        self.assertFalse(transcript.post(first))
        transcript.post_bulk(messages[1:])
        self.assertEqual(transcript.open("M4").z, 9 * 11 % P)

    def test_bulk_is_atomic_on_conflict(self):
        transcript, _, messages, _ = build_scenario(9, 9, 11, 2, 5, "T5", "M5")
        good = messages[:4]
        target = messages[4]
        bad = Message(target.op_id, target.sender, target.kind, 77)
        # 同一批次内既有坏值又有真值，批次内部即冲突。
        with self.assertRaises(MessageConflictError):
            transcript.post_bulk(good + [bad, target] + messages[5:])
        # 整批回滚：没有任何一条被收录。
        self.assertEqual(len(transcript.missing("M5")), 6)
        # 同样要拒绝批内冲突并回滚批内先行条目（不依赖前一次的状态）。
        with self.assertRaises(MessageConflictError):
            transcript.post_bulk(messages[:2] + [bad, target])
        self.assertEqual(len(transcript.missing("M5")), 6)
        # 与已落盘状态冲突时，批内先行条目同样回滚。
        transcript.post_bulk(messages)
        with self.assertRaises(MessageConflictError):
            transcript.post_bulk([Message(messages[0].op_id, 1, "e", 50), bad])
        self.assertEqual(transcript.missing("M5"), [])

    def test_unknown_party_rejected(self):
        transcript = Transcript([Party(i) for i in range(3)])
        transcript.begin("MX", "TX")
        with self.assertRaises(UnknownPartyError):
            transcript.post(Message("MX", 3, "d", 0))
        # 线缆层对 sender 的基本校验仍在构造期生效。
        for bad_sender in (101, -1, True, "0"):
            with self.assertRaises(FieldValueError):
                Message("MX", bad_sender, "d", 0)

    def test_transcript_requires_exactly_three_parties(self):
        with self.assertRaises(FieldValueError):
            Transcript([Party(0), Party(1)])
        with self.assertRaises(FieldValueError):
            Transcript([Party(0), Party(1), Party(1)])
        with self.assertRaises(FieldValueError):
            Transcript([Party(0), Party(1), Party(3)])


class StateAndReuseTest(unittest.TestCase):
    def test_triple_reuse_at_party_level(self):
        source = TripleSource(random.Random(11))
        shares = source.issue_values("TU", 4, 9)
        parties = [Party(i) for i in range(3)]
        for i, party in enumerate(parties):
            party.receive_triple(shares[i])
        parties[0].contribute("OP1", "TU", 5, 6)
        with self.assertRaises(TripleReuseError):
            parties[0].contribute("OP2", "TU", 7, 8)
        # 复用被拒后 OP2 没有留下任何登记：重试仍按“三元组已用”拒绝，
        # 而不是变成“OP2 已存在”的状态错误。
        with self.assertRaises(TripleReuseError):
            parties[0].contribute("OP2", "TU", 7, 8)

    def test_source_and_party_reuse_are_distinct_guards(self):
        source = TripleSource(random.Random(12))
        source.issue_values("TV", 1, 1)
        with self.assertRaises(TripleReuseError):
            source.issue_values("TV", 2, 2)

    def test_begin_rules(self):
        transcript = Transcript([Party(i) for i in range(3)])
        with self.assertRaises(ProtocolStateError):
            transcript.post(Message("NOPE", 0, "d", 0))  # 未 begin
        transcript.begin("OP", "T")
        with self.assertRaises(ProtocolStateError):
            transcript.begin("OP", "OTHER")  # 同 op 改绑 triple
        transcript.begin("OP", "T")  # 同参重复 begin 幂等

    def test_serial_operations_interleaved(self):
        """两个乘法操作交错投递，各自独立打开，互不串台。"""
        t1, _, m1, truth1 = build_scenario(20, 15, 16, 2, 3, "K1", "OP1")
        source = TripleSource(random.Random(21))
        s2 = source.issue_values("K2", 70, 80)
        parties = [Party(i) for i in range(3)]
        x2 = share_secret(44, random.Random(201))
        y2 = share_secret(55, random.Random(202))
        t2 = Transcript(parties)
        t2.begin("OP2", "K2")
        m2 = []
        for i, party in enumerate(parties):
            party.receive_triple(s2[i])
            m2.extend(party.contribute("OP2", "K2", x2[i], y2[i]))

        # 交错：两个操作各投一部分。
        t1.post(m1[0])
        t2.post_bulk(m2[:3])
        self.assertFalse(t1.is_complete("OP1"))
        self.assertFalse(t2.is_complete("OP2"))
        t1.post_bulk(m1[1:])
        t2.post_bulk(m2[3:])
        self.assertEqual(t1.open("OP1").z, truth1["product"])
        self.assertEqual(t2.open("OP2").z, 44 * 55 % P)

    def test_views_carry_full_local_information(self):
        transcript, _, messages, truth = build_scenario(22, 83, 17, 5, 6, "K3", "OP3")
        transcript.post_bulk(messages)
        result = transcript.open("OP3")
        for i, view in enumerate(result.views):
            self.assertEqual(view.party, i)
            self.assertEqual(view.triple_id, "K3")
            self.assertEqual(view.d, result.d)
            self.assertEqual(view.e, result.e)
            self.assertEqual(
                (view.d_i, view.e_i),
                (messages[2 * i].value, messages[2 * i + 1].value),
            )
        self.assertEqual(sum(v.z_i for v in result.views) % P, result.z)


if __name__ == "__main__":
    unittest.main()
