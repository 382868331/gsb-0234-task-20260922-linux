"""份额刷新（refresh）测试：秘密不变性、世代绑定、原子切换与刷新后乘法。

秘密不变性的期望值只来自独立的朴素模加法 sum(shares) % 101，
不调用被测核心生成期望；刷新后乘法的期望只来自朴素 (x*y) % 101。
"""

import random
import unittest

from sss_mult import (
    FieldValueError,
    IncompleteRefreshError,
    RefreshConfirmation,
    RefreshConflictError,
    RefreshCoordinator,
    RefreshError,
    RefreshReusedError,
    StaleGenerationError,
    Transcript,
    TripleBank,
    check_masks,
    derive_messages,
    honest_prepare,
    make_masks,
    make_triple,
    mask_view,
    random_share,
    refresh_share,
    refresh_shares,
    split_share,
)

P = 101
PARTIES = (0, 1, 2)
PAIRS = ((0, 1), (0, 2), (1, 2))
SEED = 20260926


def plain_sum(shares):
    """独立参考：朴素模加法重构，不经过被测核心。"""
    total = 0
    for s in shares:
        total = (total + s) % P
    return total


def run_refresh(coordinator, refresh_id, shares, masks, order=(0, 1, 2)):
    """按给定确认顺序执行一轮完整刷新，返回新份额元组。"""
    old_gen = coordinator.current_generation
    coordinator.begin(refresh_id, old_gen, old_gen + 1)
    new_shares = tuple(
        refresh_share(shares[p], p, mask_view(masks, p)) for p in PARTIES
    )
    for p in order:
        coordinator.confirm(
            RefreshConfirmation(refresh_id, p, old_gen, old_gen + 1, PARTIES)
        )
    coordinator.commit(refresh_id)
    return new_shares


class SecretInvariantTests(unittest.TestCase):
    """刷新前后秘密不变；测试端用独立模加法核对。"""

    def test_random_masks_preserve_secret(self):
        rng = random.Random(SEED)
        for secret in (0, 1, 50, 100):
            shares = random_share(secret, rng)
            self.assertEqual(plain_sum(shares), secret)
            masks = make_masks(PAIRS, rng)
            new_shares = refresh_shares(shares, masks)
            self.assertEqual(plain_sum(new_shares), secret)
            self.assertEqual(plain_sum(new_shares), plain_sum(shares))

    def test_exhaustive_small_masks_preserve_secret(self):
        # 小掩码值穷举：3 对掩码各取 {0,1,2}，秘密取若干代表值。
        for secret in (0, 7, 100):
            shares = split_share(secret, 11, 22)
            for r01 in range(3):
                for r02 in range(3):
                    for r12 in range(3):
                        masks = {(0, 1): r01, (0, 2): r02, (1, 2): r12}
                        self.assertEqual(plain_sum(refresh_shares(shares, masks)), secret)

    def test_zero_masks_leave_shares_unchanged(self):
        shares = split_share(42, 3, 77)
        masks = {(0, 1): 0, (0, 2): 0, (1, 2): 0}
        self.assertEqual(refresh_shares(shares, masks), shares)
        self.assertEqual(plain_sum(refresh_shares(shares, masks)), 42)

    def test_shares_coincidentally_unchanged(self):
        # 非零掩码但某方净效果为零：P0 在两对中都是加方，
        # r01 + r02 = 5 + 96 = 101 ≡ 0，份额恰巧不变。
        shares = split_share(9, 10, 20)
        masks = {(0, 1): 5, (0, 2): 96, (1, 2): 3}
        new_shares = refresh_shares(shares, masks)
        self.assertEqual(new_shares[0], shares[0])  # P0 恰巧不变
        self.assertNotEqual(new_shares[1], shares[1])
        self.assertEqual(plain_sum(new_shares), 9)

    def test_chained_refreshes_preserve_secret(self):
        rng = random.Random(SEED + 1)
        coordinator = RefreshCoordinator()
        shares = random_share(88, rng)
        for round_no in range(5):
            masks = make_masks(PAIRS, rng)
            shares = run_refresh(coordinator, f"chain-{round_no}", shares, masks)
            self.assertEqual(plain_sum(shares), 88)
            self.assertEqual(coordinator.current_generation, round_no + 1)


class MaskViewTests(unittest.TestCase):
    """各方视图只持有与自己相关的掩码。"""

    def test_view_contains_only_own_pairs(self):
        masks = {(0, 1): 11, (0, 2): 22, (1, 2): 33}
        self.assertEqual(mask_view(masks, 0), {(0, 1): 11, (0, 2): 22})
        self.assertEqual(mask_view(masks, 1), {(0, 1): 11, (1, 2): 33})
        self.assertEqual(mask_view(masks, 2), {(0, 2): 22, (1, 2): 33})

    def test_foreign_pair_rejected_in_local_refresh(self):
        with self.assertRaises(RefreshError):
            refresh_share(10, 0, {(1, 2): 7})

    def test_masks_validation(self):
        with self.assertRaises(FieldValueError):
            check_masks({(0, 0): 1})
        with self.assertRaises(FieldValueError):
            check_masks({(0, 1): 1}, PARTIES)  # 缺对
        with self.assertRaises(FieldValueError):
            check_masks({(0, 1): 200})
        # 键顺序无关，统一规范为升序对。
        self.assertEqual(check_masks({(1, 0): 4}), {(0, 1): 4})


class CoordinatorTests(unittest.TestCase):
    """世代绑定、确认幂等、冲突失败、缺确认与重放。"""

    def setUp(self):
        self.coordinator = RefreshCoordinator()

    def confirm(self, refresh_id, party, old, new, parties=PARTIES):
        return self.coordinator.confirm(
            RefreshConfirmation(refresh_id, party, old, new, parties)
        )

    def test_out_of_order_confirmations(self):
        self.coordinator.begin("r1", 0, 1)
        for p in (2, 0, 1):
            self.assertEqual(self.confirm("r1", p, 0, 1), "accepted")
        result = self.coordinator.commit("r1")
        self.assertEqual((result.old_generation, result.new_generation), (0, 1))
        self.assertEqual(self.coordinator.current_generation, 1)

    def test_duplicate_confirmation_idempotent(self):
        self.coordinator.begin("r1", 0, 1)
        self.assertEqual(self.confirm("r1", 0, 0, 1), "accepted")
        self.assertEqual(self.confirm("r1", 0, 0, 1), "duplicate")
        self.assertFalse(self.coordinator.is_complete("r1"))
        self.assertEqual(self.coordinator.missing_parties("r1"), (1, 2))

    def test_missing_confirmation_blocks_commit_without_state_change(self):
        self.coordinator.begin("r1", 0, 1)
        self.confirm("r1", 0, 0, 1)
        self.confirm("r1", 1, 0, 1)
        with self.assertRaises(IncompleteRefreshError) as ctx:
            self.coordinator.commit("r1")
        self.assertEqual(ctx.exception.missing, (2,))
        # 状态未变：世代仍为 0，补齐后可重试并成功。
        self.assertEqual(self.coordinator.current_generation, 0)
        self.confirm("r1", 2, 0, 1)
        self.coordinator.commit("r1")
        self.assertEqual(self.coordinator.current_generation, 1)

    def test_conflicting_confirmation_fails_round_keeps_old_generation(self):
        self.coordinator.begin("r1", 0, 1)
        self.confirm("r1", 0, 0, 1)
        # P1 绑定了不同的新世代：冲突，本轮失败。
        with self.assertRaises(RefreshConflictError):
            self.confirm("r1", 1, 0, 2)
        self.assertEqual(self.coordinator.current_generation, 0)
        self.assertFalse(self.coordinator.is_complete("r1"))
        # 失败轮不能提交，旧世代完整保留。
        with self.assertRaises(RefreshConflictError):
            self.coordinator.commit("r1")
        self.assertEqual(self.coordinator.current_generation, 0)
        # 失败轮上后续确认同样被拒绝。
        with self.assertRaises(RefreshConflictError):
            self.confirm("r1", 2, 0, 1)
        # 同一 refresh_id 在失败后不能再次生效。
        with self.assertRaises(RefreshReusedError):
            self.coordinator.begin("r1", 0, 1)
        # 换一个 refresh_id 可以正常完成一轮。
        shares = run_refresh(self.coordinator, "r2", split_share(3, 1, 1),
                             {pair: 0 for pair in PAIRS})
        self.assertEqual(plain_sum(shares), 3)
        self.assertEqual(self.coordinator.current_generation, 1)

    def test_same_id_reuse_rejected(self):
        self.coordinator.begin("r1", 0, 1)
        self.coordinator.begin("r1", 0, 1)  # 同参数幂等，不抛错
        for p in PARTIES:
            self.confirm("r1", p, 0, 1)
        self.coordinator.commit("r1")
        # 已完成的 refresh_id 无论参数是否相同都不能再次生效。
        with self.assertRaises(RefreshReusedError):
            self.coordinator.begin("r1", 1, 2)
        with self.assertRaises(RefreshReusedError):
            self.coordinator.begin("r1", 0, 1)
        self.assertEqual(self.coordinator.current_generation, 1)

    def test_generation_replay_rejected(self):
        shares = split_share(50, 4, 6)
        run_refresh(self.coordinator, "r1", shares, {pair: 1 for pair in PAIRS})
        self.assertEqual(self.coordinator.current_generation, 1)
        # 重放旧世代 0 -> 1：拒绝。
        with self.assertRaises(StaleGenerationError):
            self.coordinator.begin("r2", 0, 1)
        # 跳过世代 1 -> 3：拒绝。
        with self.assertRaises(RefreshError):
            self.coordinator.begin("r2", 1, 3)
        # 份额世代标签校验：旧份额不能混入新世代。
        self.coordinator.check_generation(1)
        with self.assertRaises(StaleGenerationError):
            self.coordinator.check_generation(0)

    def test_party_set_mismatch_rejected(self):
        with self.assertRaises(RefreshError):
            self.coordinator.begin("r1", 0, 1, parties=(0, 1))

    def test_confirmation_fields_are_public_only(self):
        # 公开转录只含 ID / 世代 / 参与方集合，不含掩码或份额字段。
        conf = RefreshConfirmation("r1", 0, 0, 1, PARTIES)
        self.assertEqual(
            set(vars(conf)), {"refresh_id", "party", "old_generation",
                              "new_generation", "parties"}
        )


class RefreshThenMultiplyTests(unittest.TestCase):
    """刷新后乘法：新份额进入 Beaver 乘法，重构乘积不变。"""

    def test_multiply_with_refreshed_input_shares(self):
        rng = random.Random(SEED + 2)
        x, y = 12, 8
        bank = TripleBank()
        bank.register(make_triple("t-mul", a=17, b=9, rng=rng))
        triple = bank.get("t-mul")

        x_shares = random_share(x, rng)
        y_shares = random_share(y, rng)
        coordinator = RefreshCoordinator()

        # 刷新 x 的份额（世代 0 -> 1），掩码由测试注入。
        masks = make_masks(PAIRS, rng)
        x_shares_new = run_refresh(coordinator, "refresh-x", x_shares, masks)
        self.assertEqual(plain_sum(x_shares_new), x)
        self.assertNotEqual(x_shares_new, x_shares)

        # 世代标签：旧份额世代已过期，不能混入本次乘法。
        with self.assertRaises(StaleGenerationError):
            coordinator.check_generation(0)
        coordinator.check_generation(1)

        # 用刷新后的份额做乘法，期望只来自朴素 (x*y) % 101。
        messages = derive_messages("mul-after-refresh", triple, x_shares_new, y_shares)
        transcript = Transcript(bank)
        transcript.add_messages(messages)
        result = transcript.open("mul-after-refresh")
        self.assertEqual(result.product, (x * y) % P)
        self.assertEqual(result.x, x)
        self.assertEqual(result.y, y)

    def test_multiply_before_and_after_refresh_same_product(self):
        rng = random.Random(SEED + 3)
        x, y = 30, 40
        bank = TripleBank()
        bank.register(make_triple("t-a", a=3, b=4, rng=rng))
        bank.register(make_triple("t-b", a=5, b=6, rng=rng))
        transcript = Transcript(bank)
        coordinator = RefreshCoordinator()

        x_shares = random_share(x, rng)
        y_shares = random_share(y, rng)
        _, _, msgs1 = honest_prepare("mul-1", bank.get("t-a"), x, y, rng)
        transcript.add_messages(msgs1)
        r1 = transcript.open("mul-1")

        masks = make_masks(PAIRS, rng)
        x_shares = run_refresh(coordinator, "rx", x_shares, masks)
        y_shares = run_refresh(coordinator, "ry", y_shares, masks)
        msgs2 = derive_messages("mul-2", bank.get("t-b"), x_shares, y_shares)
        transcript.add_messages(msgs2)
        r2 = transcript.open("mul-2")

        self.assertEqual(r1.product, (x * y) % P)
        self.assertEqual(r2.product, (x * y) % P)


if __name__ == "__main__":
    unittest.main()
