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
    """``triple_id`` 被第二次领取（被其他操作预留、已消费或已随中止操作烧毁）。"""

    def __init__(
        self,
        triple_id: str,
        op_id: str,
        holder: str,
        consumed: bool,
        *,
        terminal_state: str = "consumed",
    ):
        if consumed:
            state = f"{terminal_state} by op {holder!r}"
        else:
            state = f"reserved by other op {holder!r}"
        msg = f"triple_id {triple_id!r} cannot be used by op {op_id!r}: {state}"
        super().__init__(msg)
        self.triple_id = triple_id
        self.op_id = op_id
        self.holder = holder
        self.consumed = consumed
        #: 终态种类：``"consumed"``（正常打开消费）或 ``"aborted"``（随冲突操作烧毁）。
        self.terminal_state = terminal_state if consumed else "reserved"


class SessionMismatchError(TranscriptError, ValueError):
    """消息绑定的 ``session_id`` 与会话不符（路由错误）。

    该错误不改变任何状态：消息既没有进入缓冲，也没有预留任何三元组。
    """

    def __init__(self, expected_session: str, message_session: str):
        super().__init__(
            f"message is bound to session {message_session!r}, "
            f"cannot be delivered to session {expected_session!r}"
        )
        self.expected_session = expected_session
        self.message_session = message_session


class OperationAbortedError(ConflictError):
    """冲突重复导致操作被永久中止。

    触发条件：向已缓冲的 ``(session_id, op_id, party)`` 投递内容不一致的
    消息，或向同一操作投递引用不同 ``triple_id`` 的消息。操作进入
    ``aborted`` 终态：缓冲关闭（后续任何消息与打开都再抛本错误），其已
    领取的三元组被永久烧毁（``bank.state(...) == "aborted"``），其他操作
    与其他会话不受影响。
    """

    def __init__(self, session_id: str, op_id: str, reason: str, party: int | None = None):
        where = f"session={session_id!r}, op={op_id!r}"
        if party is not None:
            where += f", party={party}"
        super().__init__(f"operation aborted after a conflicting duplicate ({where}): {reason}")
        self.session_id = session_id
        self.op_id = op_id
        self.reason = reason
        self.party = party


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


class LimitError(TranscriptError, ValueError):
    """超过本库声明的接口上限（见 :mod:`sss_mult.field`）。"""
