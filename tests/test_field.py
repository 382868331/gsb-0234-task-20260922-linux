"""F_101 元素校验与分享/重构的输入语义。"""

import random
import unittest

from beaver_transcript import P, check_field_element, reconstruct, share_secret
from beaver_transcript.errors import FieldValueError
from tests.reference import ref_share


class FieldValidationTest(unittest.TestCase):
    def test_accepts_int_in_range(self):
        for v in (0, 1, 50, 100):
            self.assertEqual(check_field_element(v), v)

    def test_rejects_bool(self):
        with self.assertRaises(FieldValueError):
            check_field_element(True)
        with self.assertRaises(FieldValueError):
            check_field_element(False, "x")

    def test_rejects_nan_and_infinity(self):
        with self.assertRaises(FieldValueError):
            check_field_element(float("nan"))
        with self.assertRaises(FieldValueError):
            check_field_element(float("inf"))
        with self.assertRaises(FieldValueError):
            check_field_element(float("-inf"))

    def test_rejects_plain_float_and_string(self):
        with self.assertRaises(FieldValueError):
            check_field_element(3.0)
        with self.assertRaises(FieldValueError):
            check_field_element("7")
        with self.assertRaises(FieldValueError):
            check_field_element(None)

    def test_rejects_out_of_range(self):
        for v in (-1, 101, 1000, -101):
            with self.assertRaises(FieldValueError):
                check_field_element(v)

    def test_error_is_localizable(self):
        try:
            check_field_element(101, "qty")
        except FieldValueError as exc:
            self.assertIn("qty", str(exc))
        else:
            self.fail("应抛 FieldValueError")


class SharingTest(unittest.TestCase):
    def test_shares_match_independent_reference(self):
        # 期望值由 tests.reference 独立给出，不使用被测实现。
        for value in (0, 1, 37, 100):
            got = share_secret(value, random.Random(1000 + value))
            want = ref_share(value, 1000 + value)
            self.assertEqual(got, want)
            for piece in got:
                self.assertTrue(0 <= piece < P)

    def test_shares_individually_leak_nothing_trivial(self):
        # 同一种子、同一值的每次分享应不同（随机性存在），仅作结构性补充检查。
        a = share_secret(50, random.Random(1))
        b = share_secret(50, random.Random(2))
        self.assertNotEqual(a, b)

    def test_reconstruct_validation(self):
        with self.assertRaises(FieldValueError):
            reconstruct([])
        with self.assertRaises(FieldValueError):
            reconstruct([1, 2, True])
        with self.assertRaises(FieldValueError):
            reconstruct([0, 0, 200])


if __name__ == "__main__":
    unittest.main()
