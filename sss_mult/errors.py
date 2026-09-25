"""公开错误类型。

所有异常都继承 :class:`SSSMultError`；同时按 Python 惯例混入内置的
``TypeError`` / ``ValueError``，使调用方既可以按本包错误统一捕获，
也可以按内置类别捕获。
"""


class SSSMultError(Exception):
    """``sss_mult`` 包所有异常的基类。"""


class FieldTypeError(SSSMultError, TypeError):
    """参数类型非法（例如 ``bool`` 冒充整数、传入字符串/浮点）。"""


class FieldValueError(SSSMultError, ValueError):
    """参数类型正确但取值非法（越界、``NaN``、``Infinity`` 等）。"""


class IdentifierError(SSSMultError, ValueError):
    """操作 ID / 三元组 ID 非法（空串、超长等）。"""


class TranscriptError(SSSMultError):
    """转录（消息收集与打开阶段）相关错误的基类。"""


class UnknownTripleError(TranscriptError, ValueError):
    """引用了未在 :class:`TripleBank` 注册的 ``triple_id``。"""

    def __init__(self, triple_id: str):
        super().__init__(f"unknown triple_id {triple_id!r}: register a Beaver triple first")
        self.triple_id = triple_id


class ConflictError(TranscriptError, ValueError):
    """同一键上出现了内容不一致的消息（冲突拒绝）。"""


class TripleMismatchError(ConflictError):
    """同一操作内不同消息引用了不同的 ``triple_id``。"""


class TripleReusedError(TranscriptError, ValueError):
    """``triple_id`` 被第二次消费（被其他操作预留或已打开消费）。"""

    def __init__(self, triple_id: str, op_id: str, holder: str, consumed: bool):
        state = "already consumed" if consumed else f"reserved by other op {holder!r}"
        msg = f"triple_id {triple_id!r} cannot be used by op {op_id!r}: {state}"
        super().__init__(msg)
        self.triple_id = triple_id
        self.op_id = op_id
        self.holder = holder
        self.consumed = consumed


class IncompleteTranscriptError(TranscriptError):
    """参与方消息未收齐时尝试打开。

    该错误不会改变任何转录状态：不消费三元组、不缓存结果，
    补齐消息后可以重新打开。
    """

    def __init__(self, op_id: str, missing: tuple[int, ...]):
        super().__init__(
            f"op {op_id!r} cannot be opened: missing messages from parties {list(missing)}"
        )
        self.op_id = op_id
        self.missing = tuple(missing)


class DotProductError(TranscriptError):
    """点积操作相关错误的基类。"""


class DotLengthError(DotProductError, ValueError):
    """点积向量长度非法：长度不一致、为空之外的负值或超过长度上限。

    注意：**空点积（长度 0）合法**，返回零且不消耗任何三元组；
    该异常只针对真正的非法长度。
    """


class DuplicatePositionError(DotProductError, ValueError):
    """同一次点积预留了重复的 ``triple_id``（整批拒绝，不预留任何三元组）。"""


class TripleBurnedError(TripleReusedError):
    """引用的三元组属于已终止（冲突）的点积，已被永久作废。

    继承 :class:`TripleReusedError`（``consumed=True``）：作废的三元组
    与已消费三元组一样，永远不能再用于另一秘密运算，即使导致终止的
    操作本身从未打开。
    """

    def __init__(self, triple_id: str, op_id: str, holder: str):
        super().__init__(triple_id, op_id, holder, consumed=True)


class DotProductConflictError(DotProductError, ConflictError):
    """点积中某位置收到冲突消息，整个点积立即终止。

    终止后：操作标记为 ``failed``，其全部已预留三元组永久作废
    （``burned``），结果永不发布；异常对象保留失败证据
    （``op_id``、``position``、``stored``、``incoming``）。
    """

    def __init__(self, op_id: str, position: int, stored, incoming):
        super().__init__(
            f"dot product {op_id!r} terminated by conflicting message at "
            f"position {position}: stored={stored!r}, incoming={incoming!r}"
        )
        self.op_id = op_id
        self.position = position
        self.stored = stored
        self.incoming = incoming


class DotProductFailedError(DotProductError):
    """向已终止的点积继续投递消息或尝试打开（终止证据保留在转录中）。"""

    def __init__(self, op_id: str, reason: str, evidence: DotProductConflictError | None = None):
        super().__init__(f"dot product {op_id!r} has failed: {reason}")
        self.op_id = op_id
        self.reason = reason
        self.evidence = evidence


class LimitError(TranscriptError, ValueError):
    """超过本库声明的接口上限（见 :mod:`sss_mult.field`）。"""
