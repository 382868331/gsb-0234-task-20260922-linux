"""三元组发放：c=a*b、一次性消费、确定性显式合成入口。"""

import random
import unittest

from beaver_transcript import TripleSource, reconstruct
from beaver_transcript.errors import FieldValueError, TripleReuseError


class TripleSourceTest(unittest.TestCase):
    def test_c_equals_a_times_b_and_shares_sum_back(self):
        src = TripleSource(random.Random(42))
        shares = src.issue_values("T1", 7, 13)
        self.assertEqual(len(shares), 3)
        a = reconstruct([s.a for s in shares])
        b = reconstruct([s.b for s in shares])
        c = reconstruct([s.c for s in shares])
        self.assertEqual((a, b, c), (7, 13, 7 * 13 % 101))

    def test_random_triples_satisfy_relation(self):
        src = TripleSource(random.Random(7))
        for tid in (f"R{i}" for i in range(20)):
            shares = src.issue(tid)
            a = reconstruct([s.a for s in shares])
            b = reconstruct([s.b for s in shares])
            c = reconstruct([s.c for s in shares])
            self.assertEqual(c, a * b % 101)

    def test_triple_id_one_shot(self):
        src = TripleSource(random.Random(1))
        src.issue_values("once", 2, 3)
        with self.assertRaises(TripleReuseError):
            src.issue_values("once", 4, 5)
        with self.assertRaises(TripleReuseError):
            src.issue("once")

    def test_bad_triple_id_and_values(self):
        src = TripleSource(random.Random(1))
        for bad in ("", 123, True):
            with self.assertRaises(FieldValueError):
                src.issue(bad)
        with self.assertRaises(FieldValueError):
            src.issue_values("x", True, 1)
        with self.assertRaises(FieldValueError):
            src.issue_values("x", 1, 101)

    def test_failure_leaves_no_partial_consumption(self):
        src = TripleSource(random.Random(1))
        with self.assertRaises(FieldValueError):
            src.issue_values("bad", 999, 1)
        # 失败的 id 未被登记，随后合法使用应当成功。
        self.assertNotIn("bad", src.consumed_ids)
        src.issue_values("bad", 1, 1)
        self.assertIn("bad", src.consumed_ids)


if __name__ == "__main__":
    unittest.main()
