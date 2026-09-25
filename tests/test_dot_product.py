"""点积（dot_product）测试：手算对照、消息交错、重复三元组、中途冲突。

期望值预言只有朴素域运算 sum(x_k*y_k) % 101；协议核心不参与期望生成。
"""

import random
import unittest

from sss_mult import (
    ConflictError,
    DotMessage,
    IncompleteTranscriptError,
    LimitError,
    Transcript,
    TripleBank,
    TripleMismatchError,
    TripleReusedError,
    UnknownOperationError,
    UnknownTripleError,
    dot_product_prepare,
    honest_prepare,
    make_triple,
    random_share,
    reconstruct,
)

P = 101
SEED = 20260925


def build_bank(n, prefix="dp"):
    bank = TripleBank()
    rng = random.Random(SEED)
    for k in range(n):
        bank.register(make_triple(f"{prefix}-{k}", rng.randrange(P), rng.randrange(P), rng))
    return bank, rng


def plain_dot(xs, ys):
    return sum((x * y) for x, y in zip(xs, ys)) % P


class DotCorrectnessTest(unittest.TestCase):
    def test_two_dim_hand_computed(self):
        # 二维手算：x=(3,4), y=(5,6) -> 15+24 = 39。
        bank, rng = build_bank(2)
        t = Transcript(bank)
        xs = [random_share(3, rng), random_share(4, rng)]
        ys = [random_share(5, rng), random_share(6, rng)]
        res = t.dot_product("dot-2d", ["dp-0", "dp-1"], xs, ys)
        self.assertEqual(res.length, 2)
        self.assertEqual(res.positions[0].product, 15)
        self.assertEqual(res.positions[1].product, 24)
        self.assertEqual(res.value, 39)
        # 各方总份额之和等于点积。
        self.assertEqual(reconstruct(res.output_shares()), 39)
        self.assertEqual(bank.state("dp-0"), "consumed")
        self.assertEqual(bank.state("dp-1"), "consumed")

    def test_wraparound_hand_computed(self):
        # x=y=(100,100)：100*100 = 1 (mod 101)，1+1 = 2。
        bank, rng = build_bank(2)
        t = Transcript(bank)
        xs = [random_share(100, rng), random_share(100, rng)]
        ys = [random_share(100, rng), random_share(100, rng)]
        res = t.dot_product("dot-wrap", ["dp-0", "dp-1"], xs, ys)
        self.assertEqual(res.positions[0].product, 1)
        self.assertEqual(res.value, 2)

    def test_random_vectors_match_plain_field(self):
        n = 20
        bank, rng = build_bank(n)
        t = Transcript(bank)
        xvals = [rng.randrange(P) for _ in range(n)]
        yvals = [rng.randrange(P) for _ in range(n)]
        xs = [random_share(v, rng) for v in xvals]
        ys = [random_share(v, rng) for v in yvals]
        res = t.dot_product("dot-rand", [f"dp-{k}" for k in range(n)], xs, ys)
        self.assertEqual(res.value, plain_dot(xvals, yvals))
        for k, pos in enumerate(res.positions):
            self.assertEqual(pos.product, (xvals[k] * yvals[k]) % P)

    def test_empty_dot_product_returns_zero_and_consumes_nothing(self):
        bank, rng = build_bank(1)
        t = Transcript(bank)
        self.assertEqual(t.begin_dot_product("dot-empty", []), 0)
        res = t.open_dot("dot-empty")
        self.assertEqual(res.value, 0)
        self.assertEqual(res.length, 0)
        self.assertEqual(res.output_shares(), (0, 0, 0))
        # 不消耗任何三元组：无关三元组仍空闲。
        self.assertEqual(bank.state("dp-0"), "free")
        # 重复打开幂等。
        self.assertIs(t.open_dot("dot-empty"), res)


class DotInterleavingTest(unittest.TestCase):
    def test_out_of_order_and_duplicate_delivery(self):
        # 两个点积位置 + 一个普通乘法，消息全局乱序、含重复一致消息。
        bank, rng = build_bank(3)
        t = Transcript(bank)
        triples = [bank.get("dp-0"), bank.get("dp-1")]
        xvals, yvals = [7, 9], [11, 13]
        _, _, dmsgs = dot_product_prepare("dot-x", triples, xvals, yvals, rng)
        _, _, mmsgs = honest_prepare("mul-x", bank.get("dp-2"), 3, 5, rng)

        t.begin_dot_product("dot-x", ["dp-0", "dp-1"])
        # 全局乱序交织：点积两条、乘法一条、重复一条……
        order = [dmsgs[5], mmsgs[0], dmsgs[0], dmsgs[5], mmsgs[2], dmsgs[3]]
        for m in order:
            if isinstance(m, DotMessage):
                t.add_dot_message(m)
            else:
                t.add_message(m)
        self.assertFalse(t.is_dot_complete("dot-x"))
        # 缺方打开：IncompleteTranscriptError，且不消费任何三元组。
        with self.assertRaises(IncompleteTranscriptError) as cm:
            t.open_dot("dot-x")
        self.assertIn((0, 1), cm.exception.missing)
        self.assertEqual(bank.state("dp-0"), "reserved")
        self.assertEqual(bank.state("dp-1"), "reserved")
        # 补齐。
        t.add_dot_messages([dmsgs[1], dmsgs[2], dmsgs[4]])
        t.add_message(mmsgs[1])
        self.assertTrue(t.is_dot_complete("dot-x"))
        res = t.open_dot("dot-x")
        self.assertEqual(res.value, plain_dot(xvals, yvals))
        self.assertEqual(t.open("mul-x").product, 15)
        # 整个点积只发布一次：重复打开返回同一缓存结果。
        self.assertIs(t.open_dot("dot-x"), res)

    def test_dot_message_requires_declared_op(self):
        bank, rng = build_bank(1)
        t = Transcript(bank)
        _, _, dmsgs = dot_product_prepare("ghost", [bank.get("dp-0")], [1], [2], rng)
        with self.assertRaises(UnknownOperationError):
            t.add_dot_message(dmsgs[0])

    def test_wrong_position_and_wrong_triple_rejected(self):
        bank, rng = build_bank(2)
        t = Transcript(bank)
        t.begin_dot_product("dot", ["dp-0", "dp-1"])
        # 位置越界。
        with self.assertRaises(ValueError):
            t.add_dot_message(DotMessage("dot", 2, 0, "dp-0", 1, 1))
        # 位置 0 引用位置 1 的三元组。
        with self.assertRaises(TripleMismatchError):
            t.add_dot_message(DotMessage("dot", 0, 0, "dp-1", 1, 1))
        self.assertEqual(t.messages_count(), 0)


class DotBatchValidationTest(unittest.TestCase):
    def test_duplicate_triple_ids_rejected_before_any_reservation(self):
        bank, rng = build_bank(3)
        t = Transcript(bank)
        with self.assertRaises(ConflictError):
            t.begin_dot_product("dot-dup", ["dp-0", "dp-1", "dp-0"])
        # 不能消费一半才发现重复：所有三元组仍空闲，操作未创建。
        for k in range(3):
            self.assertEqual(bank.state(f"dp-{k}"), "free")
        self.assertEqual(t.operations_count(), 0)

    def test_unknown_triple_rejected_before_any_reservation(self):
        bank, rng = build_bank(2)
        t = Transcript(bank)
        with self.assertRaises(UnknownTripleError):
            t.begin_dot_product("dot-ghost", ["dp-0", "ghost"])
        self.assertEqual(bank.state("dp-0"), "free")
        self.assertEqual(t.operations_count(), 0)

    def test_unavailable_triple_rejected_before_any_reservation(self):
        bank, rng = build_bank(3)
        t = Transcript(bank)
        # 先用普通乘法消费 dp-2。
        _, _, msgs = honest_prepare("mul-first", bank.get("dp-2"), 2, 3, rng)
        t.add_messages(msgs)
        t.open("mul-first")
        # 批次里 dp-0 可用、dp-2 已消费：整批拒绝，dp-0 不被预留。
        with self.assertRaises(TripleReusedError) as cm:
            t.begin_dot_product("dot-bad", ["dp-0", "dp-2"])
        self.assertTrue(cm.exception.consumed)
        self.assertEqual(bank.state("dp-0"), "free")
        self.assertEqual(bank.state("dp-1"), "free")
        self.assertEqual(t.operations_count(), 1)

    def test_length_limit(self):
        bank, rng = build_bank(3)
        t = Transcript(bank, max_dot_product_len=2)
        with self.assertRaises(LimitError):
            t.begin_dot_product("dot-long", ["dp-0", "dp-1", "dp-2"])
        for k in range(3):
            self.assertEqual(bank.state(f"dp-{k}"), "free")
        # 恰好在上限内可以。
        self.assertEqual(t.begin_dot_product("dot-ok", ["dp-0", "dp-1"]), 2)

    def test_op_id_collision_with_mul_rejected(self):
        bank, rng = build_bank(2)
        t = Transcript(bank)
        _, _, msgs = honest_prepare("taken", bank.get("dp-0"), 1, 1, rng)
        t.add_message(msgs[0])
        with self.assertRaises(ConflictError):
            t.begin_dot_product("taken", ["dp-1"])
        self.assertEqual(bank.state("dp-1"), "free")


class DotConflictTest(unittest.TestCase):
    def test_midway_conflict_rejected_and_evidence_kept(self):
        bank, rng = build_bank(2)
        t = Transcript(bank)
        triples = [bank.get("dp-0"), bank.get("dp-1")]
        _, _, dmsgs = dot_product_prepare("dot-cf", triples, [6, 7], [8, 9], rng)
        t.begin_dot_product("dot-cf", ["dp-0", "dp-1"])
        t.add_dot_message(dmsgs[0])  # pos 0, party 0
        # 中途冲突：同键不同内容。
        bad = DotMessage("dot-cf", 0, 0, "dp-0", (dmsgs[0].d + 1) % P, dmsgs[0].e)
        with self.assertRaises(ConflictError):
            t.add_dot_message(bad)
        # 失败证据保留。
        evidence = t.dot_conflicts("dot-cf")
        self.assertEqual(evidence, (bad,))
        # 状态未被污染：原消息仍在，重复一致仍幂等。
        self.assertEqual(t.add_dot_message(dmsgs[0]), "duplicate")
        # 失败终止后三元组也不能重新用于另一秘密运算。
        with self.assertRaises(TripleReusedError) as cm:
            t.begin_dot_product("dot-other", ["dp-0"])
        self.assertFalse(cm.exception.consumed)
        self.assertEqual(cm.exception.holder, "dot-cf")
        # 补齐后原操作仍可正常完成并只发布一次。
        t.add_dot_messages(list(dmsgs[1:]))
        res = t.open_dot("dot-cf")
        self.assertEqual(res.value, plain_dot([6, 7], [8, 9]))
        self.assertEqual(bank.state("dp-0"), "consumed")
        self.assertEqual(bank.state("dp-1"), "consumed")

    def test_batch_delivery_rolls_back_inserted_messages(self):
        bank, rng = build_bank(2)
        t = Transcript(bank)
        triples = [bank.get("dp-0"), bank.get("dp-1")]
        _, _, dmsgs = dot_product_prepare("dot-rb", triples, [2, 3], [4, 5], rng)
        t.begin_dot_product("dot-rb", ["dp-0", "dp-1"])
        t.add_dot_message(dmsgs[0])
        bad = DotMessage("dot-rb", 0, 0, "dp-0", (dmsgs[0].d + 1) % P, dmsgs[0].e)
        with self.assertRaises(ConflictError):
            t.add_dot_messages([dmsgs[1], dmsgs[2], bad])
        # 本批次插入的两条被回滚；预留不释放。
        self.assertFalse(t.is_dot_complete("dot-rb"))
        self.assertEqual(t.messages_count(), 1)
        self.assertEqual(bank.state("dp-0"), "reserved")
        t.add_dot_messages(list(dmsgs[1:]))
        self.assertEqual(t.open_dot("dot-rb").value, plain_dot([2, 3], [4, 5]))


if __name__ == "__main__":
    unittest.main()
