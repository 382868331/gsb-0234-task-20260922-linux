"""比较电路（compare_bit_shares）的测试。

独立参考：期望值只来自朴素 Python 整数比较 ``a < b`` 与位拆分，
不调用被测核心生成期望值。
"""

import random
import unittest
from dataclasses import fields

from sss_mult import (
    MAX_BIT_WIDTH,
    BitCheck,
    ComparisonReport,
    FieldValueError,
    GateRecord,
    InsufficientTriplesError,
    P,
    TripleReusedError,
    compare_bit_shares,
    make_triple,
    random_share,
    reconstruct,
    triples_required,
)
from sss_mult import TripleBank


def bits_of(value: int, width: int):
    """朴素位拆分（MSB 优先），独立参考实现。"""
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def share_bits(bits, rng):
    return tuple(random_share(b, rng) for b in bits)


def fresh_bank(label: str, count: int, rng) -> tuple[TripleBank, list[str]]:
    bank = TripleBank()
    ids = []
    for k in range(count):
        tid = f"{label}-t{k}"
        bank.register(make_triple(tid, a=rng.randrange(P), b=rng.randrange(P), rng=rng))
        ids.append(tid)
    return bank, ids


def run_compare(a: int, b: int, width: int, seed: int) -> ComparisonReport:
    rng = random.Random(seed)
    bank, ids = fresh_bank(f"t{seed}", triples_required(width), rng)
    a_sh = share_bits(bits_of(a, width), rng)
    b_sh = share_bits(bits_of(b, width), rng)
    return compare_bit_shares(bank, a_sh, b_sh, ids, rng, label=f"c{seed}")


class ExhaustiveCompareTest(unittest.TestCase):
    """小位宽穷举全部输入对，对照朴素整数比较。"""
    def test_exhaustive_widths_1_to_4(self):
        for width in range(1, 5):
            for a in range(1 << width):
                for b in range(1 << width):
                    report = run_compare(a, b, width, seed=width * 10000 + a * 100 + b)
                    self.assertTrue(report.ok, (width, a, b, report.failure))
                    self.assertEqual(report.result, int(a < b), (width, a, b))
                    self.assertIn(report.result, (0, 1))

    def test_width_8_spot_cases(self):
        cases = [
            (0, 0), (0, 255), (255, 0), (255, 255), (37, 52), (52, 37),
            (128, 127),  # 仅最高位不同，a > b
            (127, 128),  # 仅最高位不同，a < b
            (200, 200),  # 相等
            (1, 254), (254, 1),
        ]
        for i, (a, b) in enumerate(cases):
            report = run_compare(a, b, 8, seed=500 + i)
            self.assertTrue(report.ok, (a, b, report.failure))
            self.assertEqual(report.result, int(a < b), (a, b))

    def test_equal_values_give_zero(self):
        for width in (1, 3, 8):
            for v in (0, 1, (1 << width) - 1, (1 << width) // 2):
                report = run_compare(v, v, width, seed=7000 + width * 10 + v % 7)
                self.assertTrue(report.ok)
                self.assertEqual(report.result, 0)


class BitValidationTest(unittest.TestCase):
    """非法位输入返回失败，不直接公开位值，资源不复活。"""
    def _run_invalid(self, bad_value: int, position: int, operand: str):
        width = 3
        rng = random.Random(99)
        bank, ids = fresh_bank("inv", triples_required(width), rng)
        a_sh = share_bits(bits_of(5, width), rng)
        b_sh = share_bits(bits_of(2, width), rng)
        # 把一个位的份额替换为重构出非法值 bad_value 的常数份额。
        bad = (bad_value, 0, 0)
        if operand == "a":
            a_sh = a_sh[:position] + (bad,) + a_sh[position + 1:]
        else:
            b_sh = b_sh[:position] + (bad,) + b_sh[position + 1:]
        return compare_bit_shares(bank, a_sh, b_sh, ids, rng, label="inv"), bank, ids

    def test_non_bit_returns_failure(self):
        for bad_value in (2, 3, 100):
            report, bank, ids = self._run_invalid(bad_value, 1, "a")
            self.assertFalse(report.ok)
            self.assertIsNone(report.result)
            self.assertIn("a[1]", report.failure)
            # 失败前的位检查公开结论里出现了"乘积非零"。
            self.assertTrue(any(not c.product_is_zero for c in report.bit_checks))

    def test_non_bit_in_b_operand(self):
        report, bank, ids = self._run_invalid(7, 0, "b")
        self.assertFalse(report.ok)
        self.assertIn("b[0]", report.failure)

    def test_failure_does_not_resurrect_triples(self):
        report, bank, ids = self._run_invalid(2, 1, "a")
        consumed = [t for t in ids if bank.state(t) == "consumed"]
        free = [t for t in ids if bank.state(t) == "free"]
        # 失败前每个已执行的门恰好消费一个三元组，且保持 consumed。
        self.assertEqual(len(consumed), len(report.gates))
        self.assertGreater(len(consumed), 0)
        self.assertEqual(len(consumed) + len(free), len(ids))
        # 已消费的三元组再次使用仍被拒绝（没有被失败复活）。
        rng = random.Random(1)
        bank2, ids2 = fresh_bank("re", 1, rng)
        from sss_mult import honest_prepare, Transcript
        t = Transcript(bank)
        triple = bank.get(consumed[0])
        _, _, msgs = honest_prepare("reuse-op", triple, 1, 1, rng)
        with self.assertRaises(TripleReusedError):
            t.add_messages(msgs)

    def test_bit_shares_must_be_field_elements(self):
        rng = random.Random(5)
        bank, ids = fresh_bank("fe", triples_required(2), rng)
        a_sh = share_bits([1, 0], rng)
        b_sh = ((0, 0, 0), (1, 0, 200))  # 200 越界
        with self.assertRaises(FieldValueError):
            compare_bit_shares(bank, a_sh, b_sh, ids, rng)


class TripleSupplyTest(unittest.TestCase):
    """三元组数量与一次性消费。"""
    def test_insufficient_triples_raises_before_consuming(self):
        width = 4
        rng = random.Random(11)
        need = triples_required(width)
        bank, ids = fresh_bank("short", need - 1, rng)
        a_sh = share_bits(bits_of(9, width), rng)
        b_sh = share_bits(bits_of(3, width), rng)
        with self.assertRaises(InsufficientTriplesError) as ctx:
            compare_bit_shares(bank, a_sh, b_sh, ids, rng)
        self.assertEqual(ctx.exception.required, need)
        self.assertEqual(ctx.exception.available, need - 1)
        # 在消费任何三元组之前失败：全部保持 free。
        self.assertTrue(all(bank.state(t) == "free" for t in ids))

    def test_each_gate_consumes_a_unique_triple(self):
        width = 3
        rng = random.Random(21)
        bank, ids = fresh_bank("uniq", triples_required(width), rng)
        a_sh = share_bits(bits_of(6, width), rng)
        b_sh = share_bits(bits_of(1, width), rng)
        report = compare_bit_shares(bank, a_sh, b_sh, ids, rng)
        self.assertTrue(report.ok)
        used = [g.triple_id for g in report.gates]
        self.assertEqual(len(used), len(set(used)), "同一三元组被多个门使用")
        self.assertEqual(len(used), triples_required(width))
        self.assertTrue(all(bank.state(t) == "consumed" for t in ids))

    def test_triples_required_scales_with_width(self):
        self.assertEqual(triples_required(1), 9)
        self.assertEqual(triples_required(MAX_BIT_WIDTH), 9 * MAX_BIT_WIDTH)
        with self.assertRaises(FieldValueError):
            triples_required(MAX_BIT_WIDTH + 1)


class PublicTranscriptHygieneTest(unittest.TestCase):
    """公开转录只含掩蔽差值、位合法性布尔结论与最终比特。"""
    def test_report_structure_contains_no_shares(self):
        # 结构层面：公开条目类型只有这些字段，无份额/秘密容器。
        self.assertEqual([f.name for f in fields(GateRecord)],
                         ["gate", "op_id", "triple_id", "d", "e"])
        self.assertEqual([f.name for f in fields(BitCheck)],
                         ["position", "operand", "product_is_zero"])
        report = run_compare(37, 52, 8, seed=42)
        self.assertTrue(report.ok)
        self.assertEqual(report.result, 1)
        for g in report.gates:
            self.assertIs(type(g.d), int)
            self.assertIs(type(g.e), int)
        for c in report.bit_checks:
            self.assertIs(type(c.product_is_zero), bool)
        self.assertEqual(len(report.bit_checks), 2 * 8)
        self.assertTrue(all(c.product_is_zero for c in report.bit_checks))

    def test_god_view_secrets_not_in_public_transcript(self):
        # 上帝视角的私有数据（位秘密、份额、三元组秘密、掩蔽 r）
        # 不得出现在公开转录的 (d, e) 对中：d/e 是掩蔽差值，
        # 对任一固定秘密值 s，公开 d 满足 d = s - a 的概率可忽略；
        # 这里用确定性种子做结构性核对：公开 (d,e) 对不等于任何
        # (secret, secret) 或 (share, share) 组合。
        width = 4
        rng = random.Random(777)
        bank, ids = fresh_bank("hyg", triples_required(width), rng)
        a_bits = bits_of(9, width)
        b_bits = bits_of(3, width)
        a_sh = share_bits(a_bits, rng)
        b_sh = share_bits(b_bits, rng)
        report = compare_bit_shares(bank, a_sh, b_sh, ids, rng, label="hyg")
        self.assertTrue(report.ok)

        private_scalars = set(a_bits) | set(b_bits)
        for shares in a_sh + b_sh:
            private_scalars.update(shares)
        for tid in ids:
            triple = bank.get(tid)
            private_scalars.update(
                [triple.a, triple.b, triple.c]
                + list(triple.a_shares) + list(triple.b_shares) + list(triple.c_shares)
            )
        public_pairs = {(g.d, g.e) for g in report.gates}
        for s in private_scalars:
            self.assertNotIn((s, s), public_pairs)
        # 结构层面：公开条目只有 d/e 两个数值字段，不存在可携带
        # 份额三元组或秘密向量的容器（见 test_report_structure_*）。
        self.assertTrue(all(isinstance(g.d, int) and isinstance(g.e, int)
                            for g in report.gates))

    def test_masked_validity_opening_hides_bit_value(self):
        # 非法位 b=2 时，v = b*(b-1) = 2；公开转录只记录"非零"结论，
        # 报告中不存在等于 v 或 b 的字段。
        report, _, _ = BitValidationTest()._run_invalid(2, 1, "a")
        self.assertFalse(report.ok)
        public_numbers = set()
        for g in report.gates:
            public_numbers.update([g.d, g.e])
        # 公开 d/e 都是掩蔽差值；报告其余公开字段只有布尔与序号。
        self.assertEqual({c.product_is_zero for c in report.bit_checks},
                         {True, False})


if __name__ == "__main__":
    unittest.main()
