"""sss_mult —— p=101 三方加法分享 Beaver 乘法的离线转录模拟（教学库）。

最小用法::

    import random
    from sss_mult import TripleBank, Transcript, make_triple, honest_prepare

    bank = TripleBank()
    rng = random.Random(20260923)
    bank.register(make_triple("t1", a=17, b=9, rng=rng))
    triple = bank.get("t1")

    _, _, msgs = honest_prepare("mul-1", triple, x=12, y=8, rng=rng)
    t = Transcript(bank)
    t.add_messages(msgs)
    result = t.open("mul-1")
    assert result.product == (12 * 8) % 101

安全边界：仅半诚实算术模拟与转录一致性检查，不提供恶意安全。
"""

from .errors import (
    ConflictError,
    FieldTypeError,
    FieldValueError,
    IdentifierError,
    IncompleteTranscriptError,
    LimitError,
    SSSMultError,
    TranscriptError,
    TripleMismatchError,
    TripleReusedError,
    UnknownTripleError,
)
from .field import (
    MAX_ID_LENGTH,
    MAX_MESSAGES,
    MAX_OPERATIONS,
    N_PARTIES,
    P,
    add,
    is_field_element,
    mul,
    neg,
    require_element,
    sub,
)
from .protocol import (
    OperationResult,
    PartyView,
    Transcript,
    TripleBank,
    UnknownOperationError,
    honest_prepare,
)
from .refresh import (
    MaskBook,
    MaskTerm,
    PartyMaskView,
    RefreshConflictError,
    RefreshError,
    RefreshManager,
    RefreshPlan,
    RefreshRecord,
    RefreshStateError,
    refresh_share,
)
from .shares import (
    BeaverTriple,
    Message,
    derive_messages,
    make_triple,
    random_share,
    reconstruct,
    split_share,
)

__all__ = [
    # 常量
    "P",
    "N_PARTIES",
    "MAX_OPERATIONS",
    "MAX_MESSAGES",
    "MAX_ID_LENGTH",
    # 域
    "add",
    "sub",
    "neg",
    "mul",
    "is_field_element",
    "require_element",
    # 分享 / 三元组 / 消息
    "BeaverTriple",
    "Message",
    "make_triple",
    "split_share",
    "random_share",
    "reconstruct",
    "derive_messages",
    # 协议
    "TripleBank",
    "Transcript",
    "OperationResult",
    "PartyView",
    "honest_prepare",
    # 刷新
    "MaskBook",
    "MaskTerm",
    "PartyMaskView",
    "RefreshManager",
    "RefreshPlan",
    "RefreshRecord",
    "refresh_share",
    # 错误
    "SSSMultError",
    "FieldTypeError",
    "FieldValueError",
    "IdentifierError",
    "TranscriptError",
    "UnknownTripleError",
    "ConflictError",
    "TripleMismatchError",
    "TripleReusedError",
    "IncompleteTranscriptError",
    "UnknownOperationError",
    "LimitError",
    "RefreshError",
    "RefreshConflictError",
    "RefreshStateError",
]

__version__ = "1.0.0"
