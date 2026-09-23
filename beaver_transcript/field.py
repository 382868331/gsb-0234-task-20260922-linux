"""素数域 F_101 的元素校验。运算本身只是 int 的取模算术。"""

import math

from .errors import FieldValueError

#: 域特征，本题固定为 101。
P = 101


def check_field_element(value, name="value"):
    """校验 value 是 F_101 的合法元素，返回规范化后的 int。

    拒绝：bool、任何 float（含 NaN/Infinity）、非 int 类型、超出 [0, P) 的整数。
    不做静默纠正（不取模、不截断）。
    """
    if isinstance(value, bool):
        raise FieldValueError(f"{name}: bool 不是合法域元素: {value!r}")
    if isinstance(value, float):
        if math.isnan(value):
            raise FieldValueError(f"{name}: 拒绝 NaN")
        if math.isinf(value):
            raise FieldValueError(f"{name}: 拒绝 Infinity")
        raise FieldValueError(f"{name}: 拒绝 float 域元素: {value!r}，请使用 [0, {P}) 内的 int")
    if not isinstance(value, int):
        raise FieldValueError(f"{name}: 域元素必须是 int，得到 {type(value).__name__}: {value!r}")
    if not 0 <= value < P:
        raise FieldValueError(f"{name}: 域元素越界 {value}，须在 [0, {P}) 内")
    return value
