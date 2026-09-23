"""秘密分享乘法的离线转录模拟（Beaver 三元组，p=101，三方加法分享）。

仅用于教学：模拟半诚实场景下的离线转录，不承诺恶意安全。
"""

from .errors import (
    BeaverError,
    FieldValueError,
    IncompleteTranscriptError,
    MessageConflictError,
    ProtocolStateError,
    TripleReuseError,
    UnknownPartyError,
)
from .field import P, check_field_element
from .protocol import MAX_ID_LEN, Message, OpResult, Party, PartyView, Transcript
from .sharing import NUM_PARTIES, reconstruct, share_secret
from .triples import TripleShare, TripleSource

__all__ = [
    "P",
    "NUM_PARTIES",
    "MAX_ID_LEN",
    "BeaverError",
    "FieldValueError",
    "IncompleteTranscriptError",
    "Message",
    "MessageConflictError",
    "OpResult",
    "Party",
    "PartyView",
    "ProtocolStateError",
    "Transcript",
    "TripleReuseError",
    "TripleShare",
    "TripleSource",
    "UnknownPartyError",
    "check_field_element",
    "reconstruct",
    "share_secret",
]
