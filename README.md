# sss_mult —— 秘密分享乘法的离线转录模拟（教学模块）

在素数域 **GF(101)** 上模拟**三方加法秘密分享**，并用 **Beaver 三元组**
完成乘法的**离线转录**：消息绑定**会话（session）/操作/参与方**，
转录层负责乱序缓冲、重复幂等、冲突中止与三元组一次性领取，齐三方后
输出各方视图与重构乘积。

- Python 版本：**3.14.7**（仅使用标准库，如 `random`、`dataclasses`、`unittest`）
- 无需安装任何第三方依赖；不需要网络、账号、真实密钥、数据库、中间件、
  云 API 或 Docker。
- 核心、测试、演示全部离线、确定性（示例随机源均带固定种子）。

## 准确命令

```bash
# 运行全部必要测试（在仓库根目录）
python -m unittest discover -s tests -v

# 运行固定输入演示（约 0.1 秒，远小于 90 秒预算；真实计算，无 sleep/预录）
python demo.py
```

## 协议与公式

三方加法分享：秘密 `s = s0 + s1 + s2 (mod 101)`。

Beaver 三元组 `(a, b, c)` 满足 `c = a*b (mod 101)`，三个秘密分别做加法
分享，参与方 `i` 持有 `(a_i, b_i, c_i)`。

对输入分享 `(x_i)`、`(y_i)`，各方广播：

```
d_i = x_i - a_i (mod 101)
e_i = y_i - b_i (mod 101)
```

三方到齐后得到公开常量 `d = Σd_i`、`e = Σe_i`，各方输出乘积份额：

```
z_i = c_i + d*b_i + e*a_i + (i == 0 ? d*e : 0)   (mod 101)
```

常量 `d*e` **只加给 0 号参与方**。重构：`z_0 + z_1 + z_2 ≡ x*y (mod 101)`。

## 数据结构与接口

全部从 `sss_mult` 包导入：

| 名称 | 说明 |
| --- | --- |
| `P = 101`, `N_PARTIES = 3` | 域特征与参与方数量（固定） |
| `add/sub/neg/mul`、`sum_mod` | GF(101) 朴素精确运算 |
| `is_field_element(v)` / `require_element(v, what)` | 域元素判定/可定位报错校验 |
| `split_share(s, r1, r2)` / `random_share(s, rng)` / `reconstruct(shares)` | 三方加法分享构造与重构 |
| `make_triple(triple_id, a, b, rng)` | 显式合成三元组；`c` 由 `a*b mod 101` 精确算出，不由调用方提供 |
| `BeaverTriple`（不可变） | `a/b/c` 及三套分享；`party_view(i) -> (a_i,b_i,c_i)` |
| `DEFAULT_SESSION_ID = "default"` | 消息未显式绑定时落入的默认会话 |
| `Message(op_id, party, triple_id, d, e, session_id=DEFAULT_SESSION_ID)`（不可变） | 一条打开消息，绑定**会话/操作/参与方**；构造时即完成字段校验 |
| `derive_messages(op_id, triple, x_shares, y_shares, *, session_id=...)` | 诚实方一次性生成三条绑定同一会话的消息 |
| `TripleBank()` | 三元组注册表；`register/get/state/reserve/release/consume/abort`，生命周期 `free → reserved → consumed / aborted` |
| `Transcript(bank, *, max_operations=1000, max_messages=10000)` | 跨会话转录机（旧入口，冲突策略为拒绝不落盘） |
| `Transcript.session(session_id) -> Session` | 在共享转录机上打开一个固定会话缓冲视图（**本次新增**） |
| `Session(session_id, transcript)` | 会话缓冲层：路由校验、乱序缓冲、重复幂等、**冲突中止该操作** |
| `honest_prepare(op_id, triple, x, y, rng, *, session_id=...)` | 语法糖：返回 `(x_shares, y_shares, messages)` |

### 会话语义（本次迭代）

- **绑定**：每条消息携带 `session_id`；操作按 `(session_id, op_id)`
  索引，不同会话可以复用相同 `op_id` 而互不干扰。`Session.add_message`
  只接受本会话消息，送错会话抛 `SessionMismatchError` 且**无状态变更**，
  改投正确会话即可。
- **乱序缓冲**：消息可任意顺序、跨操作/跨会话交错投递；齐三方后
  `Session.open(op_id)` 计算结果。
- **重复幂等**：`(session_id, op_id, party)` 上内容完全相同的重复消息
  返回 `"duplicate"` 且不增加计数；打开已完成的操作返回缓存的同一结果。
- **冲突中止**：同一槽位出现内容不一致的消息（`d/e` 被改，或同操作引用
  不同 `triple_id`）时，**整个操作立即进入 `aborted` 终态**：之后该操作
  的任何投递与打开都抛 `OperationAbortedError`；其已领取的三元组转入
  `aborted` 终态（**烧毁**，任何操作、任何会话都不能再次领取）。中止只
  影响该操作：同会话其他操作与其他会话不受影响。
- **三元组不可跨操作复用**：领取在全局 `TripleBank` 层面互斥——预留中的
  三元组不能被另一操作/另一个会话领取；正常打开后为 `consumed`，冲突
  中止后为 `aborted`，两种终态都不可再领取。
- 旧入口 `Transcript.add_message/add_messages/open(op_id)` 全部保留，
  自动落在默认会话 `"default"`，且**冲突仍是"拒绝不落盘"**（不中止、
  不烧毁），历史调用与断言不受影响。

`Session` 方法：`add_message -> "accepted"|"duplicate"`、
`add_messages(iterable) -> list[str]`（批次原子；送错会话整批拒绝；
冲突操作按题面落终态，批次对其他操作的变更全部回滚）、`open(op_id)`，
只读 `is_complete / is_open / is_aborted / abort_reason /
missing_parties / result / operations_count / messages_count /
aborted_operations / session_id / transcript`。`Transcript` 的只读方法
与 `open` 均支持 `session_id=` 关键字。

`OperationResult` 新增字段 `session_id`（默认 `"default"`，追加在末尾，
旧的位置构造不受影响）；其余字段不变：`op_id, triple_id, d, e, x, y,
views, product`；`views` 为三个
`PartyView(party, a_i, b_i, c_i, d_i, e_i, d, e, z_i)`；
`output_shares()` 返回 `(z_0, z_1, z_2)`。

最小示例（两会话交错；旧的单会话示例见 git 历史与 `demo.py` 第 5 节）：

```python
import random
from sss_mult import TripleBank, Transcript, make_triple, honest_prepare

bank = TripleBank()
rng = random.Random(20260924)
bank.register(make_triple("tA", a=10, b=20, rng=rng))
bank.register(make_triple("tB", a=4, b=5, rng=rng))

t = Transcript(bank)
sa, sb = t.session("alpha"), t.session("beta")
_, _, ma = honest_prepare("mul", bank.get("tA"), 6, 7, rng, session_id="alpha")
_, _, mb = honest_prepare("mul", bank.get("tB"), 9, 8, rng, session_id="beta")

# 两会话消息可交错、乱序、重复投递。
sa.add_message(ma[0]); sb.add_message(mb[0])
sa.add_message(ma[1]); sb.add_message(mb[1])
sa.add_message(ma[0])          # -> "duplicate"
sb.add_message(mb[2]); sa.add_message(ma[2])
assert sa.open("mul").product == (6 * 7) % 101   # 42
assert sb.open("mul").product == (9 * 8) % 101   # 72
```

## 错误语义（固定契约）

错误均继承 `SSSMultError`，并混入内置 `TypeError`/`ValueError`：

- `FieldTypeError`：域元素不是严格 `int`；**`bool` 显式拒绝**
  （即使 Python 中 `bool` 是 `int` 子类）；字符串、复数、普通对象等同样拒绝。
- `FieldValueError`：整数越界（不在 `[0, 100]`）；浮点 `NaN`/`±Infinity`
  （按题目“数值输入拒绝 NaN/Infinity”）。错误信息包含参数名，便于定位。
- `IdentifierError`：`op_id`/`triple_id` 不是非空 `str` 或超过长度上限。
- `UnknownTripleError`：引用了未注册的 `triple_id`。
- `ConflictError`：`(op_id, party)` 上出现内容不一致的消息；重复注册同一
  `triple_id` 也报此错。**重复且完全一致的消息幂等忽略。** 旧入口
  `Transcript` 上冲突是"拒绝且不落盘"。
- `TripleMismatchError`（`ConflictError` 子类）：同一操作的消息引用了不同
  `triple_id`（仅旧入口直接抛出；会话层统一按冲突中止处理）。
- `SessionMismatchError`：消息的 `session_id` 与投递目标会话不符；**不
  改变任何状态**（消息未缓冲、未预留三元组），改投正确会话即可。
- `OperationAbortedError`（`ConflictError` 子类）：会话层出现冲突重复后
  该操作已永久中止；此后向该操作投递消息或打开它都抛本错误。携带
  `session_id / op_id / party / reason`。
- `TripleReusedError`：`triple_id` 已被其他操作预留
  （`consumed=False`），或已处于终态（`consumed=True`，属性
  `terminal_state` 为 `"consumed"` 正常打开或 `"aborted"` 冲突烧毁）。
  每个 `triple_id` **恰好领取一次**，预留/消费/烧毁均跨会话互斥。
- `IncompleteTranscriptError`：缺方时打开；携带 `missing` 参与方元组。
  该错误**不改变任何状态**（不消费三元组、不缓存结果），补齐后可重试。
- `UnknownOperationError`：打开一个从未收到消息的 `(session_id, op_id)`。
- `LimitError`：超过操作数/消息数上限（上限在共享转录机层面跨会话计数）。

失败原子性：单条投递的所有检查先于状态落盘；`add_messages` 整批回滚。
唯一的"有意终态落盘"是会话层冲突中止：操作被标记 `aborted`、三元组被
烧毁（题面要求"中止该操作"），但中止是自包含的——批次中其他操作的
变更仍全部回滚，不会出现未说明的半更新。

## 输入上限（接口边界）

| 上限 | 默认值 | 位置 |
| --- | --- | --- |
| 域元素范围 | `[0, 100]` 内整数 | 固定，`P=101` |
| 参与方编号 | `0, 1, 2` | 固定，三方 |
| 单个转录机操作数 | 1000（构造可覆盖） | `MAX_OPERATIONS` |
| 单个转录机消息数 | 10000（构造可覆盖） | `MAX_MESSAGES` |
| ID 长度 | 128 字符 | `MAX_ID_LENGTH` |

`TripleBank` 注册表本身不设数量上限（教学用途）；穷举测试中显式放大了
转录机上限。短演示不穷举上限规模。

## 测试与独立参考说明

- `tests/test_field.py`：GF(101) 四则运算全域对照 Python 朴素 `%` 算术；
  `bool`/浮点/`NaN`/`Infinity`/越界等校验。
- `tests/test_shares.py`：全秘密值分享-重构；三元组 `c=a*b` 关系；
  消息分量公式对照朴素差取模。
- `tests/test_protocol.py`：
  - 小值 16×16 穷举 + **全域 101×101 = 10201 例**穷举，期望值仅来自
    朴素 `(x*y) % 101`，**不调用被测核心生成期望值**；
  - 乱序投递、缺方打开无状态变更、重复一致幂等、冲突消息拒绝、
    `triple_id` 串用/跨操作复用拒绝、串操作各自消费、批次失败回滚、
    上限拒绝。
- `tests/test_sessions.py`（**本次新增**，26 例）：
  - 两个**手算合成会话**（份额/`d_i/e_i`/`z_i` 全部为字面量，见文件头
    手算明细）交错乱序投递，逐位核对手算输出份额与朴素乘积；
  - 消息级与转录级**重放**（同一交错流在全新转录机重放，逐位一致）；
  - 送错会话整批/单条拒绝且无状态变更、缺方打开无状态变更、未知操作/
    未知三元组、会话 ID 校验；
  - 冲突重复（篡改 `d/e`、串用 `triple_id`、已打开后再冲突）中止操作、
    烧毁三元组，中止后缓冲永久关闭，烧毁三元组跨操作跨会话不可再领取，
    中止与无关操作/无关会话相互隔离，批次中止只落终态不落半更新；
  - 预留/消费的跨会话互斥、两会话串操作交错领取各自三元组；
  - 上限跨会话计数、旧 `Transcript` 冲突不落盘不中止的语义不回归。
- “分享→重构”的往返一致只作为补充检查。

## 误差口径

本库**不使用任何浮点运算**。所有秘密、份额、公开值、输出份额都是
`[0, 100]` 内的 Python 任意精度精确整数，所有公式结果为精确模 101 的
整数，**不存在舍入误差或容差**；不存在“用宽容差掩盖边界错误”的情况。

## 设计取舍

1. **上帝视角本地模拟器**：一个进程内同时持有三方分享与全部消息，便于
   教学检查；不模拟真实网络、异步或磁盘持久化。
2. **显式合成输入**：三元组 `a,b` 与乘法输入 `x,y` 均由调用方给定，
   随机源由调用方注入（固定种子即可复现），不依赖外部随机设备。
3. **三元组在首条消息时领取、打开成功时消费、操作中止时烧毁**：领取在
   全局银行层面跨会话互斥，阻止"同一三元组被两个未完成操作/两个会话
   同时引用"；缺方打开不消费，允许补齐后重试；冲突中止后三元组进入
   `aborted` 终态，杜绝"换个操作重领"。
4. **消息不可变（frozen dataclass）**：会话槽位的冲突检测可用整体相等
   直接判定。
5. `c` 不由调用方提供而由 `a*b mod 101` 计算，杜绝三元组内部不一致。
6. **会话是视图、转录机是状态**：`Session` 只做路由与策略选择，所有
   缓冲与三元组状态都在共享的 `Transcript`/`TripleBank` 中，因此多个会话
   天然共享同一互斥域与上限，也便于交错投递后分别打开、重放核验。
7. **旧语义与新语义分层共存**：`Transcript` 保持"冲突拒绝不落盘"的
   历史契约，`Session` 提供题面要求的"冲突中止操作"。默认会话
   `"default"` 让旧消息与旧入口无需修改即可继续工作。

## 已知限制（不承诺的能力）

- **不提供恶意安全**：不验证参与方是否按真实份额构造 `d_i/e_i`，恶意方
  可提交不一致消息；会话层会把这种冲突表现为"中止该操作并烧毁三元组"，
  但不识别恶意方身份、不做可识别中止/检举。本库属于半诚实模型加转录层
  一致性/一次性教学模拟。
- **中止不可恢复**：被中止的操作与其三元组都是终态，没有撤回冲突消息、
  恢复操作或替换三元组的接口（本次不做崩溃恢复/日志，进程退出后内存
  状态不保留）。
- 只实现本题契约的有限数学子集：GF(101) 加法/乘法/取负与三方 Beaver
  乘法；**不包含**域逆元/除法、其他域、MAC、承诺、网络协议，不宣称与任何
  行业标准 MPC 实现兼容。
- 无并发保护（单线程教学使用）、无持久化；会话不是网络连接，只是同一
  转录机上的路由视图。
- 题目中“搜索预算耗尽/证明无解”的区分对本模块不适用：本模块没有搜索或
  求解过程，只有可直接判定的接受/幂等/中止/拒绝。
