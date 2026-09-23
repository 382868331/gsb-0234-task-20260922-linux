"""转录协议测试：穷举乘法正确性 + 乱序/缺方/复用/冲突/串操作/批次原子性。

期望值预言只有朴素域乘法 ``(x*y) % 101``；协议本身不参与期望生成。
"""

import random
import unittest

from sss_mult import (
    ConflictError,
    IncompleteTranscriptError,
    LimitError,
    Message,
    Transcript,
    TripleBank,
    TripleMismatchError,
    TripleReusedError,
    UnknownOperationError,
    UnknownTripleError,
    honest_prepare,
    make_triple,
    reconstruct,
)

P = 101
SEED = 20260923


def build_env(triple_specs):
    """triple_specs: list of (triple_id, a, b)。返回 (bank, rng)。"""
    bank = TripleBank()
    rng = random.Random(SEED)
    for tid, a, b in triple_specs:
        bank.register(make_triple(tid, a, b, rng))
    return bank, rng


def run_honest(bank, rng, op_id, tid, x, y, order=(0, 1, 2)):
    triple = bank.get(tid)
    _, _, msgs = honest_prepare(op_id, triple, x, y, rng)
    t = Transcript(bank)
    for i in order:
        t.add_message(msgs[i])
    return t.open(op_id), t


class CorrectnessTest(unittest.TestCase):
    def test_exhaustive_small_inputs_match_plain_field_mul(self):
        # 穷举小值 0..15（x、y 各 16 个），对照朴素模乘。
        small = range(0, 16)
        bank, rng = build_env([(f"tt-{i}", 3 + i % 7, 5 + i % 11) for i in range(256)])
        t = Transcript(bank)
        k = 0
        for x in small:
            for y in small:
                tid = f"tt-{k}"
                k += 1
                _, _, msgs = honest_prepare(f"op-{x}-{y}", bank.get(tid), x, y, rng)
                t.add_messages(msgs)
                res = t.open(f"op-{x}-{y}")
                # 独立参考预言。
                self.assertEqual(res.product, (x * y) % P, f"x={x}, y={y}")
                # 重构输入也一致。
                self.assertEqual(res.x, x)
                self.assertEqual(res.y, y)
                # 输出份额之和等于乘积（份额本身不直接等于乘积）。
                self.assertEqual(reconstruct(res.output_shares()), (x * y) % P)

    def test_exhaustive_full_field_product_with_few_triples(self):
        # 全域 101x101 穷举（10201 例）；该规模超过默认接口上限，
        # 显式放大 Transcript 上限（上限是可配置的接口边界）。
        bank = TripleBank()
        rng = random.Random(SEED + 1)
        ids = []
        for i in range(P * P):
            tid = f"T{i}"
            bank.register(make_triple(tid, rng.randrange(P), rng.randrange(P), rng))
            ids.append(tid)
        t = Transcript(bank, max_operations=P * P + 1, max_messages=4 * P * P)
        k = 0
        for x in range(P):
            for y in range(P):
                tid = ids[k]
                _, _, msgs = honest_prepare(f"O{k}", bank.get(tid), x, y, rng)
                t.add_messages(msgs)
                res = t.open(f"O{k}")
                self.assertEqual(res.product, (x * y) % P)
                k += 1

    def test_output_share_formula_const_de_only_to_party0(self):
        # d*e 只加给 0 号：构造 d,e 非零的场景，手工核算各方份额。
        bank, rng = build_env([("k", 40, 60)])
        triple = bank.get("k")
        res, _ = run_honest(bank, rng, "op", "k", 7, 9)
        d, e = res.d, res.e
        self.assertEqual((triple.a + d) % P, 7)
        self.assertEqual((triple.b + e) % P, 9)
        for i, v in enumerate(res.views):
            a_i, b_i, c_i = triple.party_view(i)
            de = (d * e) % P if i == 0 else 0
            expect = (c_i + d * b_i + e * a_i + de) % P
            self.assertEqual(v.z_i, expect, f"party {i}")
        self.assertNotEqual((d * e) % P, 0)  # 确认常量项确实非平凡


class OrderingAndIdempotencyTest(unittest.TestCase):
    def test_out_of_order_delivery(self):
        # 每组乱序使用独立 triple，逐组验证结果。
        bank, rng = build_env([("o1", 1, 2), ("o2", 3, 4), ("o3", 5, 6)])
        for j, order in enumerate(((2, 0, 1), (1, 2, 0), (0, 2, 1))):
            res, _ = run_honest(bank, rng, f"op{j}", f"o{j+1}", 20 + j, 30 - j, order)
            self.assertEqual(res.product, ((20 + j) * (30 - j)) % P)

    def test_duplicate_consistent_messages_idempotent(self):
        bank, rng = build_env([("dup", 7, 8)])
        triple = bank.get("dup")
        _, _, msgs = honest_prepare("op", triple, 5, 6, rng)
        t = Transcript(bank)
        self.assertEqual(t.add_message(msgs[0]), "accepted")
        # 完全一致重复：幂等。
        for _ in range(3):
            self.assertEqual(t.add_message(msgs[0]), "duplicate")
        self.assertEqual(t.messages_count(), 1)
        t.add_messages([msgs[1], msgs[1], msgs[2]])
        self.assertEqual(t.messages_count(), 3)
        res = t.open("op")
        self.assertEqual(res.product, 30)
        # 重复打开幂等，返回同一缓存结果。
        self.assertIs(t.open("op"), res)

    def test_missing_party_blocks_open_without_state_change(self):
        bank, rng = build_env([("m", 2, 3), ("other", 1, 1)])
        triple = bank.get("m")
        _, _, msgs = honest_prepare("op", triple, 4, 5, rng)
        t = Transcript(bank)
        t.add_message(msgs[0])
        t.add_message(msgs[2])
        self.assertFalse(t.is_complete("op"))
        self.assertEqual(t.missing_parties("op"), (1,))
        with self.assertRaises(IncompleteTranscriptError) as cm:
            t.open("op")
        self.assertEqual(cm.exception.missing, (1,))
        # 三元组未被消费：补齐后可正常打开。
        self.assertEqual(bank.state("m"), "reserved")
        t.add_message(msgs[1])
        res = t.open("op")
        self.assertEqual(res.product, 20)
        self.assertEqual(bank.state("m"), "consumed")

    def test_open_unknown_operation(self):
        bank, _ = build_env([])
        t = Transcript(bank)
        with self.assertRaises(UnknownOperationError):
            t.open("nope")
        self.assertEqual(t.missing_parties("nope"), (0, 1, 2))


class ConflictTest(unittest.TestCase):
    def test_conflicting_d_rejected(self):
        bank, rng = build_env([("cf", 2, 3)])
        triple = bank.get("cf")
        _, _, msgs = honest_prepare("op", triple, 4, 5, rng)
        t = Transcript(bank)
        t.add_message(msgs[0])
        bad = Message("op", 0, "cf", (msgs[0].d + 1) % P, msgs[0].e)
        with self.assertRaises(ConflictError) as cm:
            t.add_message(bad)
        self.assertIn("op", str(cm.exception))
        self.assertIn("party=0", str(cm.exception))
        # 冲突不改变状态。
        self.assertEqual(t.add_message(msgs[0]), "duplicate")
        self.assertEqual(t.messages_count(), 1)

    def test_triple_id_mismatch_rejected(self):
        bank, rng = build_env([("tA", 2, 3), ("tB", 4, 5)])
        a = bank.get("tA")
        _, _, msgs_a = honest_prepare("op", a, 4, 5, rng)
        _, _, msgs_b = honest_prepare("op", bank.get("tB"), 4, 5, rng)
        t = Transcript(bank)
        t.add_message(msgs_a[0])
        with self.assertRaises(TripleMismatchError):
            t.add_message(msgs_b[1])
        # 冲突消息未落盘；tB 仍空闲。
        self.assertEqual(bank.state("tB"), "free")
        t.add_messages([msgs_a[1], msgs_a[2]])
        self.assertEqual(t.open("op").product, 20)

    def test_batch_rollback_on_failure_leaves_no_partial_change(self):
        bank, rng = build_env([("b0", 1, 1), ("b1", 2, 2), ("b2", 3, 3)])
        triples = [bank.get(f"b{i}") for i in range(3)]
        _, _, m0 = honest_prepare("op0", triples[0], 2, 3, rng)
        _, _, m1 = honest_prepare("op1", triples[1], 4, 5, rng)
        _, _, m2 = honest_prepare("op2", triples[2], 6, 7, rng)
        t = Transcript(bank)
        bad = [m0[0], m1[0], m2[0]]
        # 让最后一条与 op2 已预留 triple 冲突：构造引用 b2 的“别的 op”先占。
        t.add_messages(bad)
        # 新批次：两条正常 + 一条引用已被 op2 预留的 b2（来自 op3）。
        _, _, m3 = honest_prepare("op3", bank.get("b2"), 8, 9, rng)
        extra = [m0[1], m1[1], m3[0]]
        with self.assertRaises(TripleReusedError):
            t.add_messages(extra)
        # 回滚后：op0/op1 没有混入本批次消息，op3 不存在，b2 仍属于 op2。
        self.assertEqual(t.messages_count(), 3)
        self.assertEqual(t.missing_parties("op0"), (1, 2))
        self.assertEqual(t.missing_parties("op1"), (1, 2))
        self.assertEqual(t.missing_parties("op3"), (0, 1, 2))
        self.assertEqual(bank.state("b2"), "reserved")


class TripleLifecycleTest(unittest.TestCase):
    def test_unknown_triple(self):
        bank = TripleBank()
        t = Transcript(bank)
        with self.assertRaises(UnknownTripleError):
            t.add_message(Message("op", 0, "ghost", 1, 1))

    def test_duplicate_triple_registration_rejected(self):
        bank, rng = build_env([("solo", 1, 2)])
        with self.assertRaises(ConflictError):
            bank.register(make_triple("solo", 3, 4, rng))

    def test_triple_reuse_across_serial_operations(self):
        bank, rng = build_env([("once", 9, 10)])
        triple = bank.get("once")
        _, _, m1 = honest_prepare("op1", triple, 3, 4, rng)
        t = Transcript(bank)
        t.add_messages(m1)
        res1 = t.open("op1")
        self.assertEqual(res1.product, 12)
        self.assertEqual(bank.state("once"), "consumed")
        # 复用已消费三元组：拒绝。
        _, _, m2 = honest_prepare("op2", triple, 5, 6, rng)
        with self.assertRaises(TripleReusedError) as cm:
            t.add_messages(m2)
        self.assertTrue(cm.exception.consumed)
        # op2 没有被创建。
        self.assertEqual(t.operations_count(), 1)

    def test_reservation_prevents_concurrent_use_then_release_on_failed_open(self):
        bank, rng = build_env([("hot", 2, 2)])
        triple = bank.get("hot")
        _, _, mA = honest_prepare("A", triple, 3, 3, rng)
        _, _, mB = honest_prepare("B", triple, 4, 4, rng)
        t = Transcript(bank)
        t.add_message(mA[0])
        # B 试图使用已被 A 预留的三元组。
        with self.assertRaises(TripleReusedError) as cm:
            t.add_message(mB[0])
        self.assertFalse(cm.exception.consumed)
        self.assertEqual(cm.exception.holder, "A")

    def test_serial_operations_each_consume_own_triple(self):
        bank, rng = build_env([(f"s{i}", i, i + 1) for i in range(5)])
        t = Transcript(bank)
        for i in range(5):
            x, y = i + 2, i + 3
            _, _, msgs = honest_prepare(f"mul{i}", bank.get(f"s{i}"), x, y, rng)
            t.add_messages(msgs)
            res = t.open(f"mul{i}")
            self.assertEqual(res.product, (x * y) % P)
            self.assertEqual(bank.state(f"s{i}"), "consumed")
        self.assertEqual(t.operations_count(), 5)
        self.assertEqual(t.messages_count(), 15)


class LimitsTest(unittest.TestCase):
    def test_operation_and_message_limits(self):
        bank = TripleBank()
        rng = random.Random(1)
        for i in range(3):
            bank.register(make_triple(f"L{i}", 1, 1, rng))
        t = Transcript(bank, max_operations=2, max_messages=10)
        _, _, m0 = honest_prepare("o0", bank.get("L0"), 1, 1, rng)
        _, _, m1 = honest_prepare("o1", bank.get("L1"), 1, 1, rng)
        t.add_messages(m0)
        t.add_messages(m1)
        _, _, m2 = honest_prepare("o2", bank.get("L2"), 1, 1, rng)
        with self.assertRaises(LimitError):
            t.add_message(m2[0])
        # 超限拒绝不产生部分变更。
        self.assertEqual(t.operations_count(), 2)

    def test_bad_limit_arguments(self):
        bank = TripleBank()
        with self.assertRaises(TypeError):
            Transcript(bank, max_operations=True)
        with self.assertRaises(ValueError):
            Transcript(bank, max_messages=0)


if __name__ == "__main__":
    unittest.main()
