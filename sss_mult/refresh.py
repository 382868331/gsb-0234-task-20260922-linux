"""加法分享的世代刷新（refresh）：换份额、不换秘密。

模型：三方加法分享 s = s0 + s1 + s2 (mod p)。刷新时，每一对参与方
i < j 共享一个新掩码 r_ij：i 把自己的份额加上 r_ij，j 把自己的
份额减去 r_ij。对每对而言 +r 与 -r 在总和中抵消，因此刷新前后
重构出的秘密完全相同，而各方手里的份额全部换新。

职责划分：

* 掩码由**调用方注入**（测试/演示显式给定或用种子确定的 rng 生成），
  经 MaskBook 拆成**逐方视图** PartyMaskView：每方只持有与自己相关
  的掩码项，看不到其他对的掩码。
* RefreshManager 是公开转录侧：只记录刷新计划 RefreshPlan（刷新 ID、
  旧世代、新世代、参与方集合）与各方确认，**从不接触掩码与私有份额**。
  所有确认收齐后，finalize 一次性原子切换到新世代；在此之前旧世代
  完整可用。
* 重复且一致的确认幂等；同一方对同一轮提交不同计划、或任何参数冲突，
  本轮立即失败且**旧世代保持不变**。同一 refresh_id 不允许换参数
  再次生效（世代重放拒绝）。

本模块只做半诚实本地模拟：不重建秘密、不验证参与方知识一致性，
不宣称抵抗恶意参与方。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .errors import FieldTypeError, FieldValueError, TranscriptError
from .field import N_PARTIES, P, require_element
from .shares import check_id, check_party


class RefreshError(TranscriptError):
    """刷新流程相关错误的基类。"""


class RefreshConflictError(RefreshError, ValueError):
    """刷新参数冲突：同一刷新 ID 换参数、或同一方确认内容不一致。"""


class RefreshStateError(RefreshError):
    """刷新轮次状态非法（未收齐确认就收尾、轮次已失败等）。"""


def check_generation(value, what: str = "generation") -> int:
    """校验世代号：非负严格整数（bool 拒绝）。"""
    if isinstance(value, bool) or type(value) is not int:
        raise FieldTypeError(
            f"{what} must be a non-negative int (bool not accepted), "
            f"got {type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise FieldValueError(f"{what}={value} must be >= 0")
    return value


@dataclass(frozen=True)
class RefreshPlan:
    """一轮刷新的公开绑定参数（不含任何掩码或份额）。

    old_generation / new_generation 把本轮刷新绑定到确定的世代迁移；
    parties 固定为升序三方全集。构造时完成全部校验。
    """

    refresh_id: str
    old_generation: int
    new_generation: int
    parties: tuple[int, int, int] = (0, 1, 2)

    def __post_init__(self):
        check_id(self.refresh_id, "refresh_id")
        check_generation(self.old_generation, "old_generation")
        check_generation(self.new_generation, "new_generation")
        if self.new_generation != self.old_generation + 1:
            raise FieldValueError(
                f"new_generation must be old_generation + 1, got "
                f"{self.old_generation} -> {self.new_generation}"
            )
        parties = tuple(self.parties)
        if parties != tuple(range(N_PARTIES)):
            raise FieldValueError(
                f"parties must be exactly {tuple(range(N_PARTIES))}, got {parties}"
            )
        object.__setattr__(self, "parties", parties)


@dataclass(frozen=True)
class MaskTerm:
    """逐方视图中的一条掩码项：与 other 共享的掩码及本方符号。

    sign == +1 表示本方是 (i, j) 中的 i（加掩码）；
    sign == -1 表示本方是 j（减掩码）。
    """

    other: int
    sign: int
    value: int


@dataclass(frozen=True)
class PartyMaskView:
    """参与方 party 的私有掩码视图：只含与自己相关的掩码项。"""

    party: int
    terms: tuple[MaskTerm, ...]

    def delta(self) -> int:
        """本方份额的总修正量 sum(sign * value) mod p。"""
        return sum(t.sign * t.value for t in self.terms) % P


class MaskBook:
    """调用方侧（上帝视角）的成对掩码集合。

    只存在于测试/演示调用方手中；协议对象（RefreshManager）从不接收
    它。通过 party_view 拆出逐方私有视图。
    """

    def __init__(self, pairs):
        """pairs: 可迭代的 (i, j, value) 或 {(i, j): value}。

        必须恰好覆盖所有 i < j 配对各一次。
        """
        if isinstance(pairs, dict):
            items = [(k[0], k[1], v) for k, v in pairs.items()]
        else:
            items = [tuple(t) for t in pairs]
        table: dict[tuple[int, int], int] = {}
        for idx, item in enumerate(items):
            if len(item) != 3:
                raise FieldValueError(
                    f"pairs[{idx}] must be a (i, j, value) triple, got {item!r}"
                )
            i, j, value = item
            check_party(i)
            check_party(j)
            if not i < j:
                raise FieldValueError(
                    f"pairs[{idx}]: require i < j, got ({i}, {j})"
                )
            require_element(value, f"pairs[{idx}].value")
            if (i, j) in table:
                raise RefreshConflictError(f"duplicate mask for pair ({i}, {j})")
            table[(i, j)] = value
        expected = {(i, j) for i in range(N_PARTIES) for j in range(i + 1, N_PARTIES)}
        if set(table) != expected:
            raise FieldValueError(
                f"masks must cover exactly the pairs {sorted(expected)}, "
                f"got {sorted(table)}"
            )
        self._table = table

    @classmethod
    def random(cls, rng: random.Random) -> "MaskBook":
        """用种子确定的 rng 生成全部配对的随机掩码（含 0 的可能）。"""
        if not isinstance(rng, random.Random):
            raise FieldTypeError(
                f"rng must be a random.Random instance, got {type(rng).__name__}"
            )
        return cls(
            {(i, j): rng.randrange(P) for i in range(N_PARTIES) for j in range(i + 1, N_PARTIES)}
        )

    def pair_value(self, i: int, j: int) -> int:
        """返回配对 (i, j)（i < j）的掩码值。"""
        check_party(i)
        check_party(j)
        if not i < j:
            raise FieldValueError(f"require i < j, got ({i}, {j})")
        return self._table[(i, j)]

    def party_view(self, party: int) -> PartyMaskView:
        """拆出参与方 party 的私有掩码视图（只含与自己相关的项）。"""
        check_party(party)
        terms = []
        for (i, j), value in sorted(self._table.items()):
            if party == i:
                terms.append(MaskTerm(other=j, sign=1, value=value))
            elif party == j:
                terms.append(MaskTerm(other=i, sign=-1, value=value))
        return PartyMaskView(party=party, terms=tuple(terms))


def refresh_share(share: int, view: PartyMaskView) -> int:
    """参与方本地更新自己的份额：share + delta(view) (mod p)。

    纯函数，只依赖该方自己的旧份额与私有掩码视图；不接触其他方的
    份额或掩码。
    """
    if not isinstance(view, PartyMaskView):
        raise FieldTypeError(
            f"view must be a PartyMaskView, got {type(view).__name__}"
        )
    return (require_element(share, "share") + view.delta()) % P


@dataclass(frozen=True)
class RefreshRecord:
    """一轮已收尾刷新的公开记录（无掩码、无份额）。"""

    plan: RefreshPlan
    confirmed_by: tuple[int, ...]
    status: str  # "committed" | "failed"


class RefreshManager:
    """刷新协调器：绑定世代与参与方集合，收齐确认后原子切换。

    公开状态只有：当前世代号、当前份额元组（模拟器上帝视角持有，
    公开转录记录中不出现）、刷新计划与确认集合。掩码只在 finalize
    调用瞬间经逐方视图传入，不被保存。
    """

    def __init__(self, shares, *, generation: int = 0):
        from .shares import _check_share_tuple

        self._shares = _check_share_tuple(shares, "shares")
        self._generation = check_generation(generation)
        self._plan: RefreshPlan | None = None
        self._confirmations: dict[int, RefreshPlan] = {}
        self._failed = False
        self._history: list[RefreshRecord] = []
        self._used_ids: dict[str, RefreshPlan] = {}

    # ---- 只读观察 -----------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def shares(self) -> tuple[int, int, int]:
        """当前世代的份额元组（模拟器视角；公开转录记录中不出现）。"""
        return self._shares

    @property
    def plan(self) -> RefreshPlan | None:
        return self._plan

    @property
    def failed(self) -> bool:
        return self._failed

    def is_complete(self) -> bool:
        """本轮确认是否已收齐全部参与方。"""
        return self._plan is not None and len(self._confirmations) == N_PARTIES

    def missing_parties(self) -> tuple[int, ...]:
        if self._plan is None:
            return tuple(range(N_PARTIES))
        return tuple(i for i in range(N_PARTIES) if i not in self._confirmations)

    def public_record(self) -> tuple[RefreshRecord, ...]:
        """公开转录：已收尾轮次的计划与确认方，不含掩码与份额。"""
        return tuple(self._history)

    # ---- 刷新流程 -----------------------------------------------------

    def begin(self, plan: RefreshPlan) -> str:
        """开启一轮刷新；返回 "accepted" 或 "duplicate"。

        同一 refresh_id 携带**相同**计划重复调用是幂等的；携带不同
        计划（换参数）抛 RefreshConflictError，本轮失败且旧世代完整
        保留。
        """
        if not isinstance(plan, RefreshPlan):
            raise FieldTypeError(
                f"plan must be a RefreshPlan, got {type(plan).__name__}"
            )
        if self._failed:
            # 上一轮已失败（旧世代完整保留）；允许用新计划开启新一轮。
            self._plan = None
            self._confirmations = {}
            self._failed = False
        seen = self._used_ids.get(plan.refresh_id)
        if seen is not None and seen != plan:
            self._fail_round()
            raise RefreshConflictError(
                f"refresh_id {plan.refresh_id!r} was already used with plan {seen}; "
                f"refusing different plan {plan}"
            )
        if self._plan is not None and self._plan != plan:
            self._fail_round()
            raise RefreshConflictError(
                f"round already bound to plan {self._plan}; refusing {plan}"
            )
        if plan.old_generation != self._generation:
            self._fail_round()
            raise RefreshConflictError(
                f"plan binds old_generation={plan.old_generation} but current "
                f"generation is {self._generation}"
            )
        if seen is not None:
            if self._plan is None:
                # 上一轮以失败告终且参数完全一致：允许按同计划重开一轮。
                self._plan = plan
                return "accepted"
            return "duplicate"
        self._plan = plan
        self._used_ids[plan.refresh_id] = plan
        return "accepted"

    def confirm(self, party: int, plan: RefreshPlan) -> str:
        """参与方确认本轮刷新计划；重复一致确认幂等。

        确认的计划与本轮绑定计划不一致即冲突：本轮失败、旧世代保留。
        """
        check_party(party)
        if self._plan is None:
            raise RefreshStateError("no refresh round in progress: call begin() first")
        if self._failed:
            raise RefreshStateError("refresh round has failed; old generation kept")
        if plan != self._plan:
            self._fail_round()
            raise RefreshConflictError(
                f"party {party} confirmed {plan}, but round is bound to {self._plan}"
            )
        if party in self._confirmations:
            return "duplicate"
        self._confirmations[party] = plan
        return "accepted"

    def finalize(self, mask_book: MaskBook) -> tuple[int, int, int]:
        """确认收齐后原子切换到新世代，返回新份额元组。

        逐方用私有掩码视图计算新份额，全部算好后一次性替换：任何一步
        失败都不会留下新旧混合的份额。掩码不被保存。
        """
        if not isinstance(mask_book, MaskBook):
            raise FieldTypeError(
                f"mask_book must be a MaskBook, got {type(mask_book).__name__}"
            )
        if self._plan is None:
            raise RefreshStateError("no refresh round in progress")
        if self._failed:
            raise RefreshStateError("refresh round has failed; old generation kept")
        missing = self.missing_parties()
        if missing:
            raise RefreshStateError(
                f"cannot finalize: missing confirmations from parties {list(missing)}"
            )
        new_shares = tuple(
            refresh_share(self._shares[i], mask_book.party_view(i))
            for i in range(N_PARTIES)
        )
        # 原子切换：全部计算完成后才落盘。
        self._shares = new_shares
        self._generation = self._plan.new_generation
        self._history.append(
            RefreshRecord(self._plan, tuple(sorted(self._confirmations)), "committed")
        )
        self._plan = None
        self._confirmations = {}
        return self._shares

    def _fail_round(self) -> None:
        if self._plan is not None and not self._failed:
            self._history.append(
                RefreshRecord(self._plan, tuple(sorted(self._confirmations)), "failed")
            )
        self._failed = True


__all__ = [
    "MaskBook",
    "MaskTerm",
    "PartyMaskView",
    "RefreshConflictError",
    "RefreshError",
    "RefreshManager",
    "RefreshPlan",
    "RefreshRecord",
    "RefreshStateError",
    "check_generation",
    "refresh_share",
]
