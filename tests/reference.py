"""独立参考实现：不调用被测核心，只用最朴素的 int 取模算术。

测试中的期望值（份额、d/e、各方输出、重构积）全部由本文件独立推导，
被测核心只负责按协议跑出结果，二者逐字段比对。
"""

import random

P = 101
N = 3


def ref_share(value, seed):
    """与实现无关的最简单分享：前 n-1 份由自带 RNG 给出，末份配平。"""
    rng = random.Random(seed)
    shares = [rng.randrange(P) for _ in range(N - 1)]
    shares.append((value - sum(shares)) % P)
    assert sum(shares) % P == value
    return shares


def ref_beaver_outputs(x, y, a, b, x_shares, y_shares, a_shares, b_shares, c_shares):
    """独立计算 d、e 与各方应得输出份额，返回 (d, e, [z_0, z_1, z_2])。"""
    d_shares = [(x_shares[i] - a_shares[i]) % P for i in range(N)]
    e_shares = [(y_shares[i] - b_shares[i]) % P for i in range(N)]
    d = sum(d_shares) % P
    e = sum(e_shares) % P
    assert d == (x - a) % P
    assert e == (y - b) % P
    z = []
    for i in range(N):
        zi = (c_shares[i] + d * b_shares[i] + e * a_shares[i]) % P
        if i == 0:
            zi = (zi + d * e) % P
        z.append(zi)
    assert sum(z) % P == (x * y) % P
    return d, e, z
