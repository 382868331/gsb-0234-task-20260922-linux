"""加法分享与 Beaver 三元组测试。

独立参考：重构期望值用朴素的 ``sum(...) % 101``，三元组关系用朴素
``(a*b) % 101``，均不调用被测协议核心。
"""

import random
import unittest

from sss_mult import (
    FieldTypeError,
    FieldValueError,
    IdentifierError,
    Message,
    derive_messages,
    make_triple,
    random_share,
    reconstruct,
    split_share,
)

P = 101


class SharingTest(unittest.TestCase):
    def test_split_share_reconstructs_for_all_secrets(self):
        # 穷举全部秘密值与若干固定随机量。
        rng = random.Random(9917)
        for secret in range(P):
            shares = split_share(secret, rng.randrange(P), rng.randrange(P))
            # 每个分量都是合法域元素且为严格 int。
            for s in shares:
                self.assertIs(type(s), int)
                self.assertTrue(0 <= s < P)
            self.assertEqual(sum(shares) % P, secret)

    def test_random_share_deterministic_under_seed(self):
        s1 = random_share(42, random.Random(7))
        s2 = random_share(42, random.Random(7))
        self.assertEqual(s1, s2)
        self.assertEqual(reconstruct(s1), 42)

    def test_random_share_rejects_bad_rng(self):
        with self.assertRaises(FieldTypeError):
            random_share(1, object())

    def test_split_share_rejects_bool_and_float(self):
        with self.assertRaises(FieldTypeError):
            split_share(True, 1, 2)
        with self.assertRaises(FieldTypeError):
            split_share(1, 1.0, 2)
        with self.assertRaises(FieldValueError):
            split_share(101, 0, 0)

    def test_reconstruct_plain_reference(self):
        self.assertEqual(reconstruct((34, 60, 8)), (34 + 60 + 8) % P)
        self.assertEqual(reconstruct([]), 0)


class TripleTest(unittest.TestCase):
    def test_triple_relation_and_shares(self):
        rng = random.Random(31337)
        for a in range(P):
            for b in (0, 1, 50, 100):
                t = make_triple(f"t-{a}-{b}", a, b, rng)
                # 独立参考：c == a*b mod p。
                self.assertEqual(t.c, (a * b) % P)
                self.assertEqual(reconstruct(t.a_shares), a)
                self.assertEqual(reconstruct(t.b_shares), b)
                self.assertEqual(reconstruct(t.c_shares), (a * b) % P)
                for i in range(3):
                    self.assertEqual(
                        t.party_view(i),
                        (t.a_shares[i], t.b_shares[i], t.c_shares[i]),
                    )

    def test_make_triple_input_validation(self):
        rng = random.Random(1)
        with self.assertRaises(IdentifierError):
            make_triple("", 1, 2, rng)
        with self.assertRaises(FieldValueError):
            make_triple("x", 101, 2, rng)
        with self.assertRaises(FieldTypeError):
            make_triple("x", True, 2, rng)
        with self.assertRaises(FieldTypeError):
            make_triple("x", 1, 2, None)

    def test_derive_messages_plain_reference(self):
        rng = random.Random(2024)
        t = make_triple("tt", 11, 13, rng)
        xs = random_share(30, rng)
        ys = random_share(40, rng)
        msgs = derive_messages("op", t, xs, ys)
        for i, m in enumerate(msgs):
            self.assertEqual(m.op_id, "op")
            self.assertEqual(m.party, i)
            self.assertEqual(m.triple_id, "tt")
            # 独立参考：分量差朴素取模。
            self.assertEqual(m.d, (xs[i] - t.a_shares[i]) % P)
            self.assertEqual(m.e, (ys[i] - t.b_shares[i]) % P)
        # 公开 d、e 重构关系：x = a + d，y = b + e。
        d = sum(m.d for m in msgs) % P
        e = sum(m.e for m in msgs) % P
        self.assertEqual((11 + d) % P, 30)
        self.assertEqual((13 + e) % P, 40)

    def test_message_is_frozen_and_validated(self):
        rng = random.Random(5)
        t = make_triple("f", 1, 2, rng)
        xs = random_share(3, rng)
        ys = random_share(4, rng)
        m = derive_messages("op", t, xs, ys)[0]
        with self.assertRaises(Exception):
            m.party = 2  # type: ignore[misc]
        with self.assertRaises(FieldTypeError):
            Message("op", True, "f", 0, 0)
        with self.assertRaises(FieldValueError):
            Message("op", 0, "f", 101, 0)
        with self.assertRaises(IdentifierError):
            Message("", 0, "f", 0, 0)


if __name__ == "__main__":
    unittest.main()
