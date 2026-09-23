"""模块错误语义：所有可定位错误均继承 BeaverError。"""


class BeaverError(Exception):
    """本模块所有协议/输入错误的基类。"""


class FieldValueError(BeaverError, ValueError):
    """域元素非法：bool、float（含 NaN/Infinity）、非 int、或超出 [0, P)。"""


class TripleReuseError(BeaverError):
    """同一 triple_id 被重复消费。"""


class MessageConflictError(BeaverError):
    """同一 (op_id, sender, kind) 收到取值不同的冲突消息。"""


class IncompleteTranscriptError(BeaverError):
    """打开时尚未收齐全部参与方的 d/e 消息。"""


class UnknownPartyError(BeaverError):
    """消息发送方不在参与方集合内。"""


class ProtocolStateError(BeaverError):
    """状态机违规，例如同一参与方重复发起同一 op_id。"""
