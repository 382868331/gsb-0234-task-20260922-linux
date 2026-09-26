"""加法分享的世代刷新（refresh）：换份额、不换秘密。

模型：GF(101) 三方加法分享（非 Shamir）。对每一对参与方 i<j，测试
调用方注入一个掩码 r_ij；参与方 i 把自己的份额加上 r_ij，参与方 j 把
自己的份额减去 r_ij（均模 101）。每对掩码在总和中一加一减恰好抵消，
因此刷新前后重构出的秘密不变，且任何一方都不需要知道秘密本身——
本模块**绝不先重建秘密再分享**。

隐私边界：各方视图只持有与自己相关的掩码（:func:``mask_view``），
公开确认 :class:``RefreshConfirmation`` 只携带刷新 ID、世代号与参与方
集合，**不包含任何掩码或私有份额**。

原子切换：:class:``RefreshCoordinator`` 把每轮刷新绑定到
(refresh_id, old_generation, new_generation, parties)。只有收齐参与方
集合中全部确认后才允许 :meth:``RefreshCoordinator.commit`` 原子地把
当前世代切到 new_generation；缺确认抛 :class:``IncompleteRefreshError``
且不改变任何状态。重复且一致的确认幂等；冲突确认使本轮失败并保留
完整旧世代；同一 refresh_id 不能换参数再次生效；世代号必须严格
+1，旧世代重放被拒绝。

安全声明：与协议核心一致，这只是半诚实模型下的本地教学模拟，
不宣称能抵抗恶意参与方。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    FieldTypeError,
    FieldValueError,
    IncompleteRefreshError,
    RefreshConflictError,
    RefreshError,
    RefreshReusedError,
    StaleGenerationError,
)
from .field import N_PARTIES, P, require_element
from .shares import check_id, check_party

__all__ = [
    "RefreshConfirmation",
    "RefreshCoordinator",
    "RefreshResult",
    "check_masks",
    "make_masks",
    "mask_view",
    "refresh_share",
    "refresh_shares",
]


def _check_generation(value, what: str) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise FieldTypeError(
            f"{what} must be a non-negative int (bool not accepted), "
            f"got {type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise FieldValueError(f"{what}={value} must be >= 0")
    return value


def _check_parties(parties) -> tuple:
    try:
        t = tuple(parties)
    except TypeError:
        raise FieldTypeError("parties must be an iterable of party ids") from None
    if len(t) == 0:
        raise FieldValueError("parties must be non-empty")
    for p in t:
        check_party(p)
    if len(set(t)) != len(t):
        raise FieldValueError(f"parties must be distinct, got {t!r}")
    return tuple(sorted(t))


def check_masks(masks, parties=None) -> dict:
    """校验并规范化掩码映射 {(i, j): r_ij}（i<j，r_ij 为域元素）。

    :param masks: 键为参与方对 (i, j)、值为域元素的映射。
    :param parties: 若给定，要求掩码恰好覆盖该集合内所有 i<j 对。
    :returns: 规范化后的 dict，键统一为 (min, max) 升序对。
    """
    if not hasattr(masks, "items"):
        raise FieldTypeError(
            f"masks must be a mapping of (i, j) -> mask, got {type(masks).__name__}"
        )
    norm: dict = {}
    for key, value in masks.items():
        try:
            i, j = key
        except (TypeError, ValueError):
            raise FieldTypeError(
                f"mask key must be a (i, j) party pair, got {key!r}"
            ) from None
        check_party(i)
        check_party(j)
        if i == j:
            raise FieldValueError(f"mask pair must have two distinct parties, got {key!r}")
        pair = (min(i, j), max(i, j))
        if pair in norm:
            raise RefreshConflictError(f"duplicate mask for pair {pair}")
        norm[pair] = require_element(value, f"masks[{pair}]")
    if parties is not None:
        want = {
            (a, b)
            for idx, a in enumerate(parties)
            for b in parties[idx + 1 :]
        }
        missing = want - set(norm)
        extra = set(norm) - want
        if missing:
            raise FieldValueError(f"masks missing pairs: {sorted(missing)}")
        if extra:
            raise FieldValueError(f"masks contain pairs outside the party set: {sorted(extra)}")
    return norm


def make_masks(pairs, rng) -> dict:
    """用调用方注入的随机源为给定参与方对生成掩码映射。

    pairs 为 (i, j) 对的可迭代对象；随机源由调用方提供（固定种子即可
    复现），本函数不自行获取任何随机性。
    """
    import random as _random

    if not isinstance(rng, _random.Random):
        raise FieldTypeError(
            f"rng must be a random.Random instance, got {type(rng).__name__}"
        )
    return check_masks({tuple(pair): rng.randrange(P) for pair in pairs})


def mask_view(masks, party: int) -> dict:
    """抽取参与方 party 的私有掩码视图：只含与自己相关的掩码对。"""
    check_party(party)
    norm = check_masks(masks)
    return {pair: r for pair, r in norm.items() if party in pair}


def refresh_share(share, party: int, view) -> int:
    """参与方本地刷新自己的份额：对每对 (i, j)，i 加 r_ij、j 减 r_ij。

    view 必须只包含与 party 相关的掩码对（见 :func:``mask_view``）；
    传入与己无关的对会被拒绝，以强制“视图只持有自己的掩码”这一边界。
    """
    s = require_element(share, "share")
    check_party(party)
    norm = check_masks(view)
    for (i, j), r in norm.items():
        if party == i:
            s = (s + r) % P
        elif party == j:
            s = (s - r) % P
        else:
            raise RefreshError(
                f"party {party} received a mask for unrelated pair {(i, j)}: "
                "views must only contain masks involving the holder"
            )
    return s


def refresh_shares(shares, masks) -> tuple:
    """上帝视角便捷函数：对整组份额应用完整掩码映射。

    等价于对每个参与方分别调用 :func:``refresh_share``；只用于测试与
    演示的本地模拟，协议本身不要求任何一方持有全部份额。
    """
    from .shares import _check_share_tuple

    t = _check_share_tuple(shares, "shares")
    parties = tuple(range(N_PARTIES))
    norm = check_masks(masks, parties)
    return tuple(
        refresh_share(t[p], p, mask_view(norm, p)) for p in parties
    )


@dataclass(frozen=True)
class RefreshConfirmation:
    """参与方对一轮刷新的公开确认。

    只携带刷新 ID、确认方、旧/新世代号与参与方集合——**不含掩码，
    也不含任何私有份额**。(refresh_id, party) 唯一标识一条确认；
    重复且完全一致是幂等的，任一字段不同即构成冲突。
    """

    refresh_id: str
    party: int
    old_generation: int
    new_generation: int
    parties: tuple

    def __post_init__(self):
        check_id(self.refresh_id, "refresh_id")
        check_party(self.party)
        _check_generation(self.old_generation, "old_generation")
        _check_generation(self.new_generation, "new_generation")
        object.__setattr__(self, "parties", _check_parties(self.parties))
        if self.party not in self.parties:
            raise FieldValueError(
                f"confirming party {self.party} is not in the party set {self.parties!r}"
            )


@dataclass(frozen=True)
class RefreshResult:
    """一次已原子提交的刷新（公开转录视角，不含掩码/份额）。"""

    refresh_id: str
    old_generation: int
    new_generation: int
    parties: tuple


class _Round:
    __slots__ = ("refresh_id", "old_generation", "new_generation", "parties",
                 "confirmations", "failed")

    def __init__(self, refresh_id, old_generation, new_generation, parties):
        self.refresh_id = refresh_id
        self.old_generation = old_generation
        self.new_generation = new_generation
        self.parties = parties
        self.confirmations = {}
        self.failed = False

    def fingerprint(self):
        return (self.old_generation, self.new_generation, self.parties)


class RefreshCoordinator:
    """刷新协调器：绑定世代与参与方集合，收齐确认后原子切换世代。

    协调器只跟踪公开状态（世代号、确认、轮次），**从不接触掩码或
    私有份额**；份额的实际更新由各方用 :func:``refresh_share`` 在本地
    完成。世代号从 0 开始，每次成功提交严格 +1。
    """

    def __init__(self, parties=(0, 1, 2)):
        self._parties = _check_parties(parties)
        self._generation = 0
        self._rounds: dict = {}
        self._completed: dict = {}

    # ---- 只读观察接口 -------------------------------------------------

    @property
    def parties(self) -> tuple:
        return self._parties

    @property
    def current_generation(self) -> int:
        return self._generation

    def is_complete(self, refresh_id: str) -> bool:
        """该轮确认是否已收齐（未知/已失败轮返回 False）。"""
        check_id(refresh_id, "refresh_id")
        rnd = self._rounds.get(refresh_id)
        return (
            rnd is not None
            and not rnd.failed
            and len(rnd.confirmations) == len(rnd.parties)
        )

    def missing_parties(self, refresh_id: str) -> tuple:
        """尚未确认的参与方（升序）；未知轮返回参与方集合全体。"""
        check_id(refresh_id, "refresh_id")
        rnd = self._rounds.get(refresh_id)
        have = set() if rnd is None else set(rnd.confirmations)
        return tuple(p for p in self._parties if p not in have)

    def check_generation(self, generation: int) -> int:
        """校验给定的份额世代就是当前世代，防止新旧份额混入同次乘法。"""
        _check_generation(generation, "generation")
        if generation != self._generation:
            raise StaleGenerationError(self._generation, generation)
        return generation

    # ---- 轮次管理 -----------------------------------------------------

    def begin(self, refresh_id: str, old_generation: int, new_generation: int,
              parties=None) -> None:
        """开启一轮刷新并绑定参数；同 ID 同参数幂等，同 ID 换参数拒绝。"""
        check_id(refresh_id, "refresh_id")
        _check_generation(old_generation, "old_generation")
        _check_generation(new_generation, "new_generation")
        parties = self._parties if parties is None else _check_parties(parties)
        if parties != self._parties:
            raise RefreshError(
                f"party set {parties!r} does not match the coordinator set "
                f"{self._parties!r}"
            )
        if refresh_id in self._completed:
            raise RefreshReusedError(refresh_id, "this refresh has already been committed")
        if old_generation != self._generation:
            raise StaleGenerationError(self._generation, old_generation)
        if new_generation != old_generation + 1:
            raise RefreshError(
                f"new_generation must equal old_generation + 1, got "
                f"{old_generation} -> {new_generation}"
            )
        existing = self._rounds.get(refresh_id)
        if existing is not None:
            if existing.failed:
                raise RefreshReusedError(refresh_id, "this refresh round has failed")
            if existing.fingerprint() != (old_generation, new_generation, parties):
                raise RefreshReusedError(
                    refresh_id, "refresh_id is already bound to different parameters"
                )
            return  # 同参数重复 begin：幂等。
        self._rounds[refresh_id] = _Round(
            refresh_id, old_generation, new_generation, parties
        )

    def confirm(self, confirmation: RefreshConfirmation) -> str:
        """投递一条公开确认；重复一致幂等，冲突使本轮失败。"""
        if not isinstance(confirmation, RefreshConfirmation):
            raise FieldTypeError(
                f"confirmation must be a RefreshConfirmation, "
                f"got {type(confirmation).__name__}"
            )
        cid = confirmation.refresh_id
        if cid in self._completed:
            raise RefreshReusedError(cid, "this refresh has already been committed")
        rnd = self._rounds.get(cid)
        if rnd is None:
            raise RefreshError(f"unknown refresh_id {cid!r}: call begin() first")
        if rnd.failed:
            raise RefreshConflictError(
                f"refresh {cid!r} has already failed; the old generation "
                f"{self._generation} remains active"
            )
        bound = (confirmation.old_generation, confirmation.new_generation,
                 confirmation.parties)
        if bound != rnd.fingerprint():
            rnd.failed = True
            raise RefreshConflictError(
                f"confirmation from party {confirmation.party} binds "
                f"{bound}, but round {cid!r} is bound to {rnd.fingerprint()}; "
                "round failed, old generation kept intact"
            )
        existing = rnd.confirmations.get(confirmation.party)
        if existing is not None:
            if existing == confirmation:
                return "duplicate"
            rnd.failed = True
            raise RefreshConflictError(
                f"conflicting confirmations for (refresh={cid!r}, "
                f"party={confirmation.party}); round failed, old generation kept intact"
            )
        rnd.confirmations[confirmation.party] = confirmation
        return "accepted"

    def commit(self, refresh_id: str) -> RefreshResult:
        """收齐全部确认后原子切换到新世代；缺确认不改变任何状态。"""
        check_id(refresh_id, "refresh_id")
        if refresh_id in self._completed:
            raise RefreshReusedError(refresh_id, "this refresh has already been committed")
        rnd = self._rounds.get(refresh_id)
        if rnd is None:
            raise RefreshError(f"unknown refresh_id {refresh_id!r}: call begin() first")
        if rnd.failed:
            raise RefreshConflictError(
                f"refresh {refresh_id!r} has failed and cannot commit; "
                f"generation {self._generation} remains active"
            )
        missing = tuple(p for p in rnd.parties if p not in rnd.confirmations)
        if missing:
            raise IncompleteRefreshError(refresh_id, missing)
        # 全部检查通过后才落盘：原子切换。
        result = RefreshResult(
            refresh_id=refresh_id,
            old_generation=rnd.old_generation,
            new_generation=rnd.new_generation,
            parties=rnd.parties,
        )
        self._generation = rnd.new_generation
        self._completed[refresh_id] = rnd.fingerprint()
        del self._rounds[refresh_id]
        return result

