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
    """会话 ID / 操作 ID / 三元组 ID 非法（空串、超长等）。"""


class TranscriptError(SSSMultError):
    """转录（消息收集与打开阶段）相关错误的基类。"""


class UnknownTripleError(TranscriptError, ValueError):
    """引用了未在 :class:`TripleBank` 注册的 ``triple_id``。"""

    def __init__(self, triple_id: str):
        super().__init__(f"unknown triple_id {triple_id!r}: register a Beaver triple first")
        self.triple_id = triple_id


class ConflictError(TranscriptError, ValueError):
    """同一键上出现了内容不一致的消息（冲突拒绝）。

    冲突会**中止该操作**（见 :class:`OperationAbortedError`）：冲突消息
    本身不落盘，但操作进入 ``aborted`` 终态、其领取的三元组作废。
    """


class TripleMismatchError(ConflictError):
    """同一操作内不同消息引用了不同的 ``triple_id``。

    与普通冲突一样中止该操作。
    """


class TripleReusedError(TranscriptError, ValueError):
    """``triple_id`` 被第二次领取（被其他操作预留、已消费或已作废）。

    :ivar consumed: 三元组已被成功打开的操作消费。
    :ivar voided: 三元组因所属操作被冲突中止而作废，不可再被任何操作领取。
    """

    def __init__(
        self,
        triple_id: str,
        op_id: str,
        holder: str,
        consumed: bool,
        voided: bool = False,
        holder_session: str = "default",
    ):
        if voided:
            state = (
                f"voided after op {holder!r} in session {holder_session!r} aborted"
            )
        elif consumed:
            state = (
                f"already consumed by op {holder!r} in session {holder_session!r}"
            )
        else:
            state = (
                f"reserved by other op {holder!r} in session {holder_session!r}"
            )
        msg = f"triple_id {triple_id!r} cannot be used by op {op_id!r}: {state}"
        super().__init__(msg)
        self.triple_id = triple_id
        self.op_id = op_id
        self.holder = holder
        self.holder_session = holder_session
        self.consumed = consumed
        self.voided = voided


class OperationAbortedError(TranscriptError):
    """向已中止的操作投递消息或尝试打开。

    操作在收到冲突重复（同键不同内容，或同操作引用不同 ``triple_id``）
    后进入 ``aborted`` 终态：缓冲保留在冲突发生前的快照，领取的三元组
    作废，任何重放（哪怕内容与原消息完全一致）都不能使其复活；
    必须换用新的 ``op_id``（及新的三元组）。中止不影响其他会话/操作。
    """

    def __init__(self, session_id: str, op_id: str, reason: str):
        super().__init__(
            f"op {op_id!r} in session {session_id!r} has been aborted: {reason}"
        )
        self.session_id = session_id
        self.op_id = op_id
        self.reason = reason


class IncompleteTranscriptError(TranscriptError):
    """参与方消息未收齐时尝试打开。

    该错误不会改变任何转录状态：不消费三元组、不缓存结果，
    补齐消息后可以重新打开。
    """

    def __init__(self, op_id: str, missing: tuple[int, ...], session_id: str = "default"):
        super().__init__(
            f"op {op_id!r} in session {session_id!r} cannot be opened: "
            f"missing messages from parties {list(missing)}"
        )
        self.op_id = op_id
        self.session_id = session_id
        self.missing = tuple(missing)


class LimitError(TranscriptError, ValueError):
    """超过本库声明的接口上限（见 :mod:`sss_mult.field`）。"""
