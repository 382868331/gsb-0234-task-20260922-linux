"""比较电路（less_than）测试。

独立参考：期望值只来自 Python 普通整数比较 a < b，不调用被测核心。
"""

import random
import unittest

from sss_mult import (
    MAX_WIDTH,
    ComparisonError,
    NonBinaryBitError,
    Transcript,
    TripleBank,
    TripleReusedError,
    UnknownTripleError,
    less_than,
    make_triple,
    random_share,
    reconstruct,
    required_triples,
)


def bits_of(value: int, width: int) -> list[int]:
    """普通整数 -> LSB 在前的位列表（独立参考，不经过被测核心）。"""
    return [(value >> i) & 1 for i in range(width)]


def share_bits(bits, rng):
    return [random_share(bit, rng) for bit in bits]


def fresh_env(width: int, seed: int = 20260926, prefix: str = "t"):
    rng = random.Random(seed)
    bank = TripleBank()
    n = required_triples(width)
    ids = [f"{prefix}-{i}" for i in range(n)]
    for tid in ids:
        bank.register(make_triple(tid, rng.randrange(101), rng.randrange(101), rng))
    transcript = Transcript(bank, max_operations=10_000, max_messages=100_000)
    return rng, bank, transcript, ids


def run_compare(a: int, b: int, width: int, seed: int = 20260926):
    rng, bank, transcript, ids = fresh_env(width, seed)
    a_bits = share_bits(bits_of(a, width), rng)
    b_bits = share_bits(bits_of(b, width), rng)
    report = less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                       triple_ids=ids)
    return report, bank, transcript


class TestRequiredTriples(unittest.TestCase):
    def test_counts(self):
        for w in range(1, MAX_WIDTH + 1):
            self.assertEqual(required_triples(w), 6 * w)

    def test_width_bounds(self):
        for bad in (0, 9, -1):
            with self.assertRaises(ComparisonError):
                required_triples(bad)
        with self.assertRaises(TypeError):
            required_triples(True)


class TestLessThanExhaustive(unittest.TestCase):
    def test_exhaustive_small_widths(self):
        # 小位宽穷举全部输入对，与普通整数比较独立对照。
        for width in (1, 2, 3, 4):
            for a in range(1 << width):
                for b in range(1 << width):
                    report, _, _ = run_compare(a, b, width, seed=1000 + width)
                    self.assertEqual(
                        report.result, int(a < b),
                        f"width={width} a={a} b={b}",
                    )
                    self.assertEqual(report.width, width)
                    self.assertEqual(len(report.gates), 6 * width)
                    self.assertTrue(all(report.valid_bits_a))
                    self.assertTrue(all(report.valid_bits_b))

    def test_equality_is_not_less(self):
        for width in (1, 3, 5, 8):
            for a in (0, 1, (1 << width) - 1, (1 << (width - 1))):
                report, _, _ = run_compare(a, a, width, seed=7)
                self.assertEqual(report.result, 0)

    def test_highest_bit_decides(self):
        # 仅最高位不同：a 的 MSB=0、b 的 MSB=1 -> a < b；反之不成立。
        width = 8
        msb = 1 << (width - 1)
        for low_a, low_b in ((0, msb - 1), (msb - 1, 0), (37, 200 % msb)):
            a, b = low_a, msb | low_b
            report, _, _ = run_compare(a, b, width, seed=11)
            self.assertEqual(report.result, 1, f"a={a} b={b}")
            report2, _, _ = run_compare(b, a, width, seed=11)
            self.assertEqual(report2.result, 0, f"a={b} b={a}")

    def test_max_width_exhaustive_pairs_sampled(self):
        # 8 位全宽：边界对 + 固定种子抽样对。
        width = 8
        pairs = [(0, 0), (0, 255), (255, 0), (255, 255), (128, 127), (127, 128)]
        rng = random.Random(99)
        pairs += [(rng.randrange(256), rng.randrange(256)) for _ in range(30)]
        for a, b in pairs:
            report, _, _ = run_compare(a, b, width, seed=5)
            self.assertEqual(report.result, int(a < b), f"a={a} b={b}")


class TestNonBinaryBits(unittest.TestCase):
    def test_invalid_bit_fails_without_revealing(self):
        width = 3
        rng, bank, transcript, ids = fresh_env(width)
        a_bits = share_bits(bits_of(2, width), rng)
        bad = random_share(2, rng)  # 重构值为 2，不是比特
        b_bits = share_bits(bits_of(1, width), rng)
        b_bits[1] = bad
        with self.assertRaises(NonBinaryBitError) as ctx:
            less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                      triple_ids=ids)
        exc = ctx.exception
        self.assertEqual((exc.operand, exc.index), ("b", 1))
        # 错误信息不携带秘密值或份额。
        self.assertNotIn(repr(bad), str(exc))
        for share_value in bad:
            self.assertNotIn(f"share {share_value}", str(exc))

    def test_failure_does_not_resurrect_triples(self):
        width = 2
        rng, bank, transcript, ids = fresh_env(width)
        a_bits = share_bits([1, 0], rng)
        b_bits = [random_share(5, rng), random_share(0, rng)]  # bit0 非法
        with self.assertRaises(NonBinaryBitError):
            less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                      triple_ids=ids)
        consumed = [t for t in ids if bank.state(t) == "consumed"]
        self.assertTrue(consumed, "失败前应已消费部分三元组")
        # 已消费的不可复活、不可复用。
        for tid in consumed:
            with self.assertRaises(TripleReusedError):
                bank.reserve(tid, "another-op")
        # 未触及的三元组仍然 free（失败不制造未说明的变更）。
        untouched = [t for t in ids if bank.state(t) == "free"]
        self.assertEqual(len(consumed) + len(untouched), len(ids))


class TestTripleSupply(unittest.TestCase):
    def test_wrong_triple_count_rejected_before_consumption(self):
        width = 2
        rng, bank, transcript, ids = fresh_env(width)
        a_bits = share_bits([1, 0], rng)
        b_bits = share_bits([0, 1], rng)
        for bad_ids in (ids[:-1], ids + ["extra"], ids[:-1] + [ids[0]]):
            with self.assertRaises(ComparisonError):
                less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                          triple_ids=bad_ids)
        # 数量校验在任何消费之前：全部三元组仍 free。
        self.assertTrue(all(bank.state(t) == "free" for t in ids))

    def test_unregistered_triple_rejected(self):
        width = 1
        rng, bank, transcript, ids = fresh_env(width)
        a_bits = share_bits([1], rng)
        b_bits = share_bits([0], rng)
        ids[2] = "not-registered"
        with self.assertRaises(UnknownTripleError):
            less_than(a_bits, b_bits, bank=bank, transcript=transcript,
                      triple_ids=ids)

    def test_each_triple_consumed_exactly_once(self):
        report, bank, _ = run_compare(3, 5, 3, seed=3)
        used = [g.triple_id for g in report.gates]
        self.assertEqual(len(used), len(set(used)), "门之间不得共用三元组")
        self.assertTrue(all(bank.state(t) == "consumed" for t in used))


class TestPublicTranscriptHygiene(unittest.TestCase):
    def test_report_contains_no_shares_or_secrets(self):
        a, b, width = 13, 9, 4
        report, bank, transcript = run_compare(a, b, width, seed=42)
        # 报告字段：只有位宽、门记录、合法性布尔、最终位。
        self.assertIsInstance(report.result, int)
        self.assertIn(report.result, (0, 1))
        for g in report.gates:
            self.assertIsInstance(g.d, int)
            self.assertIsInstance(g.e, int)
            self.assertTrue(0 <= g.d < 101 and 0 <= g.e < 101)
        # 转录机里每条消息只携带掩蔽差值 (d_i, e_i)，无其他字段。
        for op_id in (g.op_id for g in report.gates):
            res = transcript.open(op_id)  # 打开缓存视图做上帝视角核查
            for v in res.views:
                self.assertTrue(0 <= v.d_i < 101 and 0 <= v.e_i < 101)
        # 门记录只有 op_id/triple_id/purpose/d/e，没有秘密或份额字段。
        import dataclasses
        gate_fields = {f.name for f in dataclasses.fields(report.gates[0])}
        self.assertEqual(gate_fields,
                         {"op_id", "triple_id", "purpose", "d", "e"})
        report_fields = {f.name for f in dataclasses.fields(report)}
        self.assertEqual(report_fields,
                         {"width", "gates", "valid_bits_a",
                          "valid_bits_b", "result"})
        # 报告没有任何三分量份额字段。
        for field_value in (report.gates, report.valid_bits_a,
                            report.valid_bits_b):
            for item in field_value:
                self.assertNotIsInstance(item, tuple)


if __name__ == "__main__":
    unittest.main()
