"""有限域 GF(101) 原语。

本模块只实现题目契约所需的极小子集：p = 101 素数域上的加法、乘法、
取负以及“严格整数元素”校验。不实现域逆元/除法/扩域，也不宣称与任何
行业标准（如 MPC 框架）兼容。

误差口径：本库不使用浮点。所有域元素都是区间 ``[0, P-1]`` 内的 Python
精确整数，运算结果同样是精确整数（模 ``P``），不存在浮点舍入误差。
"""

from .errors import FieldTypeError, FieldValueError

#: 域特征（题目固定为 101）。
P = 101

#: 参与方数量（固定三方）。
N_PARTIES = 3

#: 接口上限（教学/演示规模）。
MAX_OPERATIONS = 1000
MAX_MESSAGES = 10_000
MAX_ID_LENGTH = 128


def is_field_element(value) -> bool:
    """判断 ``value`` 是否为合法域元素（``[0, 100]`` 内的非 ``bool`` 整数）。

    不抛异常，仅返回 ``True``/``False``。浮点（含 ``NaN``、``Infinity``）
    一律视为非法。
    """
    return type(value) is int and 0 <= value < P


def require_element(value, what: str = "value") -> int:
    """校验并返回规范化后的域元素。

    :param what: 参数名，用于“可定位”的错误信息。
    :raises FieldTypeError: 不是整数（``bool``、浮点、字符串等）。
        ``bool`` 被显式拒绝，即使它在 Python 中是 ``int`` 的子类。
    :raises FieldValueError: 整数但不在 ``[0, 100]`` 内；
        或为浮点 ``NaN`` / ``Infinity``（类型本身也不合法）。
    """
    # bool 是 int 的子类，按契约必须显式拒绝。
    if isinstance(value, bool) or type(value) is not int:
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise FieldValueError(
                    f"{what} must be a finite integer in [0, {P - 1}], got non-finite float {value!r}"
                )
        raise FieldTypeError(
            f"{what} must be an int in [0, {P - 1}] (bool is not accepted), got {type(value).__name__}: {value!r}"
        )
    if not (0 <= value < P):
        raise FieldValueError(f"{what}={value!r} is out of the field range [0, {P - 1}]")
    return value


def add(x: int, y: int) -> int:
    """模 101 加法（输入须为合法域元素）。"""
    return (require_element(x, "x") + require_element(y, "y")) % P


def sub(x: int, y: int) -> int:
    """模 101 减法。"""
    return (require_element(x, "x") - require_element(y, "y")) % P


def neg(x: int) -> int:
    """模 101 取负。"""
    return (-require_element(x, "x")) % P


def mul(x: int, y: int) -> int:
    """模 101 乘法。"""
    return (require_element(x, "x") * require_element(y, "y")) % P


def sum_mod(terms) -> int:
    """多个域元素求和后取模（空序列为 0）。"""
    total = 0
    for i, t in enumerate(terms):
        total = (total + require_element(t, f"terms[{i}]")) % P
    return total
