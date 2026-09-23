"""Beaver 三元组 (a, b, c = a*b mod P) 的确定性合成与一次性发放。"""

from dataclasses import dataclass

from .errors import FieldValueError, TripleReuseError
from .field import P, check_field_element
from .sharing import NUM_PARTIES, share_secret

#: op_id / triple_id 的长度上限（接口边界，超出直接拒绝，不静默截断）。
MAX_ID_LEN = 256


@dataclass(frozen=True)
class TripleShare:
    """发给单个参与方的一份三元组份额。"""

    triple_id: str
    a: int
    b: int
    c: int


class TripleSource:
    """离线的可信三元组发放方（教学模拟）。

    每个 triple_id 只能被 issue 一次；重复消费抛 TripleReuseError。
    发放是原子的：参数非法不登记消费；生成成功后才标记已消费。
    """

    def __init__(self, rng):
        self._rng = rng
        self._consumed = set()

    @property
    def consumed_ids(self):
        """已消费的 triple_id 集合（只读副本）。"""
        return frozenset(self._consumed)

    def _check_fresh(self, triple_id):
        if isinstance(triple_id, bool) or not isinstance(triple_id, str) or not triple_id:
            raise FieldValueError(f"triple_id: 必须是非空 str，得到 {triple_id!r}")
        if len(triple_id) > MAX_ID_LEN:
            raise FieldValueError(f"triple_id: 长度 {len(triple_id)} 超过上限 {MAX_ID_LEN}")
        if triple_id in self._consumed:
            raise TripleReuseError(f"triple_id {triple_id!r} 已被消费，拒绝复用")

    def issue(self, triple_id):
        """随机合成 a、b，令 c = a*b mod P，分享后按方发放。"""
        self._check_fresh(triple_id)
        a = self._rng.randrange(P)
        b = self._rng.randrange(P)
        return self._issue_values(triple_id, a, b)

    def issue_values(self, triple_id, a, b):
        """用显式给定的 a、b 合成三元组（测试/演示用的确定性入口）。"""
        self._check_fresh(triple_id)
        check_field_element(a, "a")
        check_field_element(b, "b")
        return self._issue_values(triple_id, a, b)

    def _issue_values(self, triple_id, a, b):
        c = (a * b) % P
        a_shares = share_secret(a, self._rng)
        b_shares = share_secret(b, self._rng)
        c_shares = share_secret(c, self._rng)
        shares = [
            TripleShare(triple_id, a_shares[i], b_shares[i], c_shares[i])
            for i in range(NUM_PARTIES)
        ]
        # 全部成功后才登记消费，保证失败不留部分变更。
        self._consumed.add(triple_id)
        return shares
