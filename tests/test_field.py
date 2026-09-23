"""GF(101) 原语与输入校验测试。

期望值全部用独立朴素算术（Python 内置 ``%``）直接给出，不调用被测核心。
"""

import math
import unittest

from sss_mult import field as F
from sss_mult.errors import FieldTypeError, FieldValueError


class FieldArithmeticTest(unittest.TestCase):
    def test_add_sub_mul_against_plain_arithmetic(self):
        # 独立参考：直接用 Python 整数运算模 101。
        for x in range(F.P):
            for y in range(F.P):
                self.assertEqual(F.add(x, y), (x + y) % 101)
                self.assertEqual(F.sub(x, y), (x - y) % 101)
                self.assertEqual(F.mul(x, y), (x * y) % 101)

    def test_neg_against_plain_arithmetic(self):
        for x in range(F.P):
            self.assertEqual(F.neg(x), (-x) % 101)

    def test_sum_mod(self):
        self.assertEqual(F.sum_mod([]), 0)
        self.assertEqual(F.sum_mod([100, 100, 3]), (100 + 100 + 3) % 101)
        terms = list(range(101))
        self.assertEqual(F.sum_mod(terms), sum(terms) % 101)

    def test_is_field_element(self):
        self.assertTrue(F.is_field_element(0))
        self.assertTrue(F.is_field_element(100))
        self.assertFalse(F.is_field_element(-1))
        self.assertFalse(F.is_field_element(101))
        self.assertFalse(F.is_field_element(True))
        self.assertFalse(F.is_field_element(1.0))
        self.assertFalse(F.is_field_element("3"))
        self.assertFalse(F.is_field_element(None))


class FieldValidationTest(unittest.TestCase):
    def test_bool_rejected_as_int_parameter(self):
        # bool 是 int 子类，但契约要求显式拒绝。
        with self.assertRaises(FieldTypeError):
            F.require_element(True, "flag")
        with self.assertRaises(FieldTypeError):
            F.require_element(False, "flag")
        with self.assertRaises(FieldTypeError):
            F.add(1, True)

    def test_float_rejected(self):
        for bad in (1.0, 0.5, -0.0):
            with self.subTest(bad=bad):
                with self.assertRaises(FieldTypeError):
                    F.require_element(bad, "f")

    def test_nan_and_infinity_rejected_with_locatable_error(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises((FieldTypeError, FieldValueError)) as cm:
                    F.require_element(bad, "scalar")
                # 错误信息必须可定位：包含参数名。
                self.assertIn("scalar", str(cm.exception))
        # 明确 NaN/Infinity 走 FieldValueError 分支。
        with self.assertRaises(FieldValueError):
            F.require_element(float("nan"), "scalar")
        with self.assertRaises(FieldValueError):
            F.require_element(float("inf"), "scalar")

    def test_non_numeric_rejected(self):
        for bad in ("7", None, [7], {1: 2}, 1j):
            with self.subTest(bad=bad):
                with self.assertRaises(FieldTypeError):
                    F.require_element(bad, "scalar")

    def test_out_of_range_rejected(self):
        for bad in (-1, 101, 102, 10**9, -101):
            with self.subTest(bad=bad):
                with self.assertRaises(FieldValueError) as cm:
                    F.require_element(bad, "scalar")
                self.assertIn("scalar", str(cm.exception))
                self.assertIn("[0, 100]", str(cm.exception))

    def test_boundary_values_accepted(self):
        self.assertEqual(F.require_element(0), 0)
        self.assertEqual(F.require_element(100), 100)

    def test_no_float_used(self):
        # 所有算术接口返回严格 int 类型。
        self.assertIsInstance(F.mul(50, 4), int)
        self.assertIs(type(F.mul(50, 4)), int)
        self.assertFalse(isinstance(F.add(1, 2), bool))
        self.assertFalse(math.isnan(F.neg(0)))  # 结果可被数值化检查且不异常


if __name__ == "__main__":
    unittest.main()
