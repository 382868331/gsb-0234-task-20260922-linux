"""三方加法分享与重构（本题契约固定三方）。"""

from .errors import FieldValueError
from .field import P, check_field_element

#: 本题契约的参与方数量，固定为 3。
NUM_PARTIES = 3


def share_secret(value, rng):
    """把域元素 value 拆成 3 份加法分享，份额之和 ≡ value (mod P)。

    rng 必须是 random.Random 实例（由调用方持有种子，保证确定性可复现）。
    返回长度为 3 的 int 列表。
    """
    check_field_element(value, "value")
    shares = [rng.randrange(P) for _ in range(NUM_PARTIES - 1)]
    last = (value - sum(shares)) % P
    shares.append(last)
    return shares


def reconstruct(shares):
    """把加法份额求和重构出秘密：sum(shares) mod P。"""
    shares = list(shares)
    if not shares:
        raise FieldValueError("reconstruct: 份额列表为空")
    for i, s in enumerate(shares):
        check_field_element(s, f"shares[{i}]")
    return sum(shares) % P
