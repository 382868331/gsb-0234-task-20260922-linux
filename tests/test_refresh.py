"""刷新（refresh）测试：秘密不变、份额换新、确认/冲突/重放语义。

独立预言：秘密不变性只用朴素模加法 (sum(shares) % 101) 检查，
不调用被测核心生成期望值。
"""

import random
import unittest

from sss_mult import (
    FieldValueError,
    MaskBook,
    RefreshConflictError,
    RefreshManager,
    RefreshPlan,
    RefreshStateError,
    Transcript,
    TripleBank,
    derive_messages,
    honest_prepare,
    make_triple,
    random_share,
    reconstruct,
    refresh_share,
)

P = 101
SEED = 20260926


def plain_sum(shares):
    return sum(shares) % P


def make_plan(rid="r1", old=0):
    return RefreshPlan(refresh_id=rid, old_generation=old, new_generation=old + 1)


def run_round(manager, plan, masks, order=(0, 1, 2)):
    manager.begin(plan)
    for i in order:
        manager.confirm(i, plan)
    return manager.finalize(masks)


class MaskBookTest(unittest.TestCase):
    def test_party_view_holds_only_own_pairs(self):
        masks = MaskBook({(0, 1): 5, (0, 2): 7, (1, 2): 9})
        v0 = masks.party_view(0)
        self.assertEqual(sorted(t.other for t in v0.terms), [1, 2])
        self.assertTrue(all(t.sign == 1 for t in v0.terms))
        v2 = masks.party_view(2)
        self.assertEqual(sorted(t.other for t in v2.terms), [0, 1])
        self.assertTrue(all(t.sign == -1 for t in v2.terms))
        # 0 号视图中不存在配对 (1, 2) 的掩码值 9。
        self.assertNotIn(9, [t.value for t in v0.terms])

    def test_pair_coverage_enforced(self):
        with self.assertRaises(FieldValueError):
            MaskBook({(0, 1): 1, (0, 2): 2})  # 缺 (1,2)
        with self.assertRaises(FieldValueError):
            MaskBook({(1, 0): 1, (0, 2): 2, (1, 2): 3})  # i < j 要求
        with self.assertRaises(FieldValueError):
            MaskBook({(0, 1): 1, (0, 2): 2, (1, 2): 300})  # 非域元素

    def test_zero_masks_leave_shares_unchanged(self):
        masks = MaskBook({(0, 1): 0, (0, 2): 0, (1, 2): 0})
        for share in (0, 1, 50, 100):
            for party in (0, 1, 2):
                self.assertEqual(refresh_share(share, masks.party_view(party)), share)

    def test_signed_deltas_cancel_in_sum(self):
        rng = random.Random(SEED)
        masks = MaskBook.random(rng)
        old = (10, 20, 30)
        new = tuple(refresh_share(old[i], masks.party_view(i)) for i in range(3))
        self.assertEqual(plain_sum(new), plain_sum(old))


class RefreshFlowTest(unittest.TestCase):
    def setUp(self):
        rng = random.Random(SEED)
        self.secret = 42
        self.shares = random_share(self.secret, rng)
        self.masks = MaskBook({(0, 1): 11, (0, 2): 22, (1, 2): 33})

    def test_happy_path_secret_preserved_shares_changed(self):
        m = RefreshManager(self.shares)
        new = run_round(m, make_plan(), self.masks)
        self.assertEqual(m.generation, 1)
        self.assertEqual(plain_sum(new), self.secret)  # 独立模加法检查
        self.assertNotEqual(new, self.shares)  # 份额确实换新
        # 逐方公式核对：i 加、j 减。
        s0, s1, s2 = self.shares
        self.assertEqual(new[0], (s0 + 11 + 22) % P)
        self.assertEqual(new[1], (s1 - 11 + 33) % P)
        self.assertEqual(new[2], (s2 - 22 - 33) % P)

    def test_shares_happen_to_stay_same(self):
        # 掩码使每方修正量恰为 0（非零掩码但 delta 全为 0）。
        masks = MaskBook({(0, 1): 5, (0, 2): P - 5, (1, 2): 5})
        m = RefreshManager(self.shares)
        new = run_round(m, make_plan(), masks)
        self.assertEqual(new, self.shares)
        self.assertEqual(m.generation, 1)  # 世代仍然前进

    def test_out_of_order_confirmations(self):
        m = RefreshManager(self.shares)
        plan = make_plan()
        m.begin(plan)
        for i in (2, 0, 1):
            self.assertEqual(m.confirm(i, plan), "accepted")
        self.assertTrue(m.is_complete())
        new = m.finalize(self.masks)
        self.assertEqual(plain_sum(new), self.secret)

    def test_duplicate_confirmation_idempotent(self):
        m = RefreshManager(self.shares)
        plan = make_plan()
        m.begin(plan)
        self.assertEqual(m.begin(plan), "duplicate")
        m.confirm(0, plan)
        self.assertEqual(m.confirm(0, plan), "duplicate")
        m.confirm(1, plan)
        m.confirm(2, plan)
        m.finalize(self.masks)
        self.assertEqual(m.generation, 1)

    def test_missing_confirmation_blocks_finalize_and_keeps_state(self):
        m = RefreshManager(self.shares)
        plan = make_plan()
        m.begin(plan)
        m.confirm(0, plan)
        m.confirm(1, plan)
        with self.assertRaises(RefreshStateError):
            m.finalize(self.masks)
        self.assertEqual(m.shares, self.shares)  # 旧份额原样
        self.assertEqual(m.generation, 0)
        self.assertEqual(m.missing_parties(), (2,))
        # 补齐后仍可完成。
        m.confirm(2, plan)
        m.finalize(self.masks)
        self.assertEqual(m.generation, 1)

    def test_conflicting_confirmation_fails_round_keeps_old_generation(self):
        m = RefreshManager(self.shares)
        plan = make_plan()
        m.begin(plan)
        m.confirm(0, plan)
        other = RefreshPlan(refresh_id="r1", old_generation=0, new_generation=1)
        other = RefreshPlan(refresh_id="rX", old_generation=0, new_generation=1)
        with self.assertRaises(RefreshConflictError):
            m.confirm(1, other)
        self.assertTrue(m.failed)
        self.assertEqual(m.shares, self.shares)
        self.assertEqual(m.generation, 0)
        with self.assertRaises(RefreshStateError):
            m.finalize(self.masks)
        # 失败记录进入公开转录，且不含掩码/份额字段。
        rec = m.public_record()[-1]
        self.assertEqual(rec.status, "failed")
        self.assertFalse(hasattr(rec, "masks"))
        self.assertFalse(hasattr(rec, "shares"))

    def test_same_refresh_id_cannot_reuse_with_new_params(self):
        m = RefreshManager(self.shares)
        run_round(m, make_plan("r1", old=0), self.masks)
        # 同一 ID 换参数再次生效：拒绝。
        with self.assertRaises(RefreshConflictError):
            m.begin(RefreshPlan(refresh_id="r1", old_generation=1, new_generation=2))
        self.assertEqual(m.generation, 1)
        self.assertEqual(plain_sum(m.shares), self.secret)

    def test_generation_replay_rejected(self):
        m = RefreshManager(self.shares)
        run_round(m, make_plan("r1", old=0), self.masks)
        # 重放旧世代迁移（新 ID 但 old_generation 已过期）：拒绝。
        with self.assertRaises(RefreshConflictError):
            m.begin(make_plan("r2", old=0))
        self.assertEqual(m.generation, 1)
        # 同 ID 同参数幂等重放已提交的迁移：同样拒绝（世代已前进）。
        with self.assertRaises(RefreshConflictError):
            m.begin(make_plan("r1", old=0))
        self.assertEqual(m.generation, 1)
        # 正常的新一代可以推进。
        run_round(m, make_plan("r3", old=1), self.masks)
        self.assertEqual(m.generation, 2)
        self.assertEqual(plain_sum(m.shares), self.secret)

    def test_failed_round_can_be_retried_with_new_id(self):
        m = RefreshManager(self.shares)
        plan = make_plan("r1", old=0)
        m.begin(plan)
        with self.assertRaises(RefreshConflictError):
            m.confirm(0, make_plan("r9", old=0))
        self.assertTrue(m.failed)
        # 新 ID、同世代迁移：允许重开并成功。
        new = run_round(m, make_plan("r2", old=0), self.masks)
        self.assertEqual(m.generation, 1)
        self.assertEqual(plain_sum(new), self.secret)


class RefreshThenMultiplyTest(unittest.TestCase):
    def test_multiplication_uses_only_new_generation_shares(self):
        rng = random.Random(SEED)
        x, y = 12, 8
        x_shares = random_share(x, rng)
        y_shares = random_share(y, rng)
        # 对 x 的分享做刷新（y 不变），刷新后再做 Beaver 乘法。
        mx = RefreshManager(x_shares)
        masks = MaskBook.random(random.Random(SEED + 1))
        run_round(mx, RefreshPlan("rx", 0, 1), masks)
        new_x = mx.shares
        self.assertEqual(plain_sum(new_x), x)

        bank = TripleBank()
        bank.register(make_triple("t1", a=17, b=9, rng=rng))
        triple = bank.get("t1")
        msgs = derive_messages("mul-after-refresh", triple, new_x, y_shares)
        t = Transcript(bank)
        t.add_messages(msgs)
        res = t.open("mul-after-refresh")
        self.assertEqual(res.x, x)
        self.assertEqual(res.product, (x * y) % P)

    def test_old_shares_not_mixed_into_multiplication(self):
        # 原子切换前，旧份额仍是唯一有效份额；切换后旧份额不再出现。
        rng = random.Random(SEED)
        shares = random_share(77, rng)
        m = RefreshManager(shares)
        plan = RefreshPlan("r1", 0, 1)
        m.begin(plan)
        m.confirm(0, plan)
        self.assertEqual(m.shares, shares)  # 未收齐：仍是旧世代
        m.confirm(1, plan)
        m.confirm(2, plan)
        masks = MaskBook({(0, 1): 3, (0, 2): 4, (1, 2): 5})
        new = m.finalize(masks)
        self.assertEqual(m.shares, new)
        self.assertNotEqual(m.shares, shares)
        self.assertEqual(plain_sum(m.shares), 77)


if __name__ == "__main__":
    unittest.main()
