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


class LimitError(TranscriptError, ValueError):
    """超过本库声明的接口上限（见 :mod:`sss_mult.field`）。"""


class RefreshError(TranscriptError):
    """份额刷新（refresh）相关错误的基类。"""


class RefreshConflictError(ConflictError):
    """同一刷新轮内出现内容不一致的确认；本轮失败，旧世代保持完整。"""


class RefreshReusedError(RefreshError, ValueError):
    """refresh_id 已被使用（已完成或已绑定其他参数），不能再次生效。"""

    def __init__(self, refresh_id: str, detail: str):
        super().__init__(f"refresh_id {refresh_id!r} cannot take effect again: {detail}")
        self.refresh_id = refresh_id


class StaleGenerationError(RefreshError, ValueError):
    """刷新绑定的旧世代与当前世代不一致（世代重放/跳跃）。"""

    def __init__(self, expected: int, got: int):
        super().__init__(
            f"stale generation: current generation is {expected}, "
            f"but refresh references old_generation={got}"
        )
        self.expected = expected
        self.got = got


class IncompleteRefreshError(RefreshError):
    """确认未收齐时尝试原子切换；不改变任何状态，补齐后可重试。"""

    def __init__(self, refresh_id: str, missing: tuple):
        super().__init__(
            f"refresh {refresh_id!r} cannot commit: missing confirmations "
            f"from parties {list(missing)}"
        )
        self.refresh_id = refresh_id
        self.missing = tuple(missing)
