# sss_mult —— 带会话绑定的秘密分享乘法离线转录模拟（教学模块）

在素数域 **GF(101)** 上模拟**三方加法秘密分享**，并用 **Beaver 三元组**
完成一次乘法的**离线转录**：消息按**会话/操作/参与方**三级键缓冲
（可乱序），做一致性/一次性检查，齐三方后输出各方视图与重构乘积。

本版迭代在原有转录之上加入：

- **会话绑定**：每条消息携带 `session_id`，不同会话的同名操作互不干扰；
  三元组注册表仍是全局的，领取后不可跨会话/跨操作复用。
- **乱序缓冲 + 重放幂等**：消息只按 `(session_id, op_id, party)` 缓冲，
  交付顺序无关；完全相同的重复消息返回 `"duplicate"` 且不落盘。
- **冲突中止**：同一键上内容不一致的重复（或同一操作引用了不同
  `triple_id`）会把该操作置为 `aborted` 终态并作废其三元组；此后任何
  重放（哪怕内容与原消息一致）都不能复活它，必须换新 `op_id` 与新
  三元组；中止严格限定在涉事操作，其他会话/操作不受影响。

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

演示内容：两个合成会话 `sA`/`sB`（含同名操作）严格交错乱序投递并分别
重构、全部消息打乱后整批重放（幂等）、一条篡改消息触发冲突中止并核验
中止隔离与重放拒绝。

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
| `DEFAULT_SESSION_ID = "default"` | 不带会话参数时归入的默认会话 |
| `add/sub/neg/mul`、`sum_mod` | GF(101) 朴素精确运算 |
| `is_field_element(v)` / `require_element(v, what)` | 域元素判定/可定位报错校验 |
| `split_share(s, r1, r2)` / `random_share(s, rng)` / `reconstruct(shares)` | 三方加法分享构造与重构 |
| `make_triple(triple_id, a, b, rng)` | 显式合成三元组；`c` 由 `a*b mod 101` 精确算出，不由调用方提供 |
| `BeaverTriple`（不可变） | `a/b/c` 及三套分享；`party_view(i) -> (a_i,b_i,c_i)` |
| `Message(op_id, party, triple_id, d, e, session_id="default")`（不可变） | 一条打开消息；构造时即完成字段校验。`session_id` 带默认值，旧的 5 参位置构造无需改动（也可作第 6 个位置参数） |
| `derive_messages(op_id, triple, x_shares, y_shares, *, session_id=...)` | 诚实方一次性生成三条消息 |
| `TripleBank()` | 三元组注册表；`register/get/state`，生命周期 `free → reserved(op) → consumed`，冲突中止时 `reserved → voided` |
| `Transcript(bank, *, max_operations=1000, max_messages=10000)` | 转录机（见下） |
| `honest_prepare(op_id, triple, x, y, rng, *, session_id=...)` | 语法糖：返回 `(x_shares, y_shares, messages)` |

`Transcript` 主要方法（凡接受 `op_id` 的读取/打开方法都带可选
`session_id`，默认 `"default"`）：

- `add_message(msg) -> "accepted" | "duplicate"`：投递一条消息（可乱序）。
- `add_messages(iterable) -> list[str]`：批量投递，**原子**：任一条失败则
  回滚本批次对存活操作的全部变更（消息、新建操作、三元组预留）；冲突
  中止是终态，被中止的操作及其三元组作废不回滚。
- `open(op_id, session_id=...) -> OperationResult`：齐三方才能打开；重复
  打开返回缓存结果。
- 只读：`is_complete / is_open / is_aborted / abort_reason /
  missing_parties / operations_count(session_id=None) /
  messages_count(session_id=None) / sessions_count / result`。
  `operations_count/messages_count` 传 `None` 统计全部会话。
- `OperationResult` 字段：`session_id, op_id, triple_id, d, e, x, y,
  views, product`；`views` 为三个
  `PartyView(party, a_i, b_i, c_i, d_i, e_i, d, e, z_i)`；
  `output_shares()` 返回 `(z_0, z_1, z_2)`。

最小示例：

```python
import random
from sss_mult import TripleBank, Transcript, make_triple, honest_prepare

bank = TripleBank()
rng = random.Random(20260923)
bank.register(make_triple("t1", a=17, b=9, rng=rng))
_, _, msgs = honest_prepare("mul-1", bank.get("t1"), x=12, y=8,
                            rng=rng, session_id="session-A")

t = Transcript(bank)
t.add_messages(msgs)          # 可乱序、可含重复一致消息
result = t.open("mul-1", session_id="session-A")
assert result.product == (12 * 8) % 101
# 不传 session_id 时全部归入 "default" 会话，旧代码无需改动。
```

## 错误语义（固定契约）

错误均继承 `SSSMultError`，并混入内置 `TypeError`/`ValueError`：

- `FieldTypeError`：域元素不是严格 `int`；**`bool` 显式拒绝**
  （即使 Python 中 `bool` 是 `int` 子类）；字符串、复数、普通对象等同样拒绝。
- `FieldValueError`：整数越界（不在 `[0, 100]`）；浮点 `NaN`/`±Infinity`
  （按题目“数值输入拒绝 NaN/Infinity”）。错误信息包含参数名，便于定位。
- `IdentifierError`：`session_id`/`op_id`/`triple_id` 不是非空 `str` 或超过长度上限。
- `UnknownTripleError`：引用了未注册的 `triple_id`。
- `ConflictError`：`(session_id, op_id, party)` 上出现内容不一致的消息；
  重复注册同一 `triple_id` 也报此错。**完全一致的重复幂等忽略
  （`"duplicate"`）；冲突则中止该操作。**
- `TripleMismatchError`（`ConflictError` 子类）：同一操作的消息引用了不同
  `triple_id`；与普通冲突一样中止该操作。
- `OperationAbortedError`：向已中止的操作投递消息或尝试打开。冲突一旦
  发生，操作进入 `aborted` 终态：缓冲冻结在冲突前快照、三元组作废，
  **任何重放（包括与原消息完全一致的重放）都被此错误拒绝**；用
  `is_aborted`/`abort_reason` 观测。中止只影响这一个操作。
- `TripleReusedError`：`triple_id` 已被其他操作预留（`consumed=False`）、
  已被打开消费（`consumed=True`）或因原操作中止而作废（`voided=True`）。
  错误对象带 `holder`/`holder_session` 指明占用方。每个 `triple_id`
  **恰好被领取一次，不可跨操作（含跨会话同名操作）复用**。
- `IncompleteTranscriptError`：缺方时打开；携带 `missing` 参与方元组与
  `session_id`。该错误**不改变任何状态**（不消费三元组、不缓存结果），
  补齐后可重试。
- `UnknownOperationError`：打开该会话中一个从未收到消息的 `op_id`。
- `LimitError`：超过操作数/消息数上限。

失败原子性：单条 `add_message` 的所有检查先于状态落盘（冲突消息本身
不落盘）；`add_messages` 回滚本批次对存活操作的变更。冲突中止是声明的
终态语义：被中止的操作与三元组作废不会因批次回滚而撤销，同批次其他
新操作的新建记录与预留则全部回滚。不存在“未说明的部分变更”。

## 输入上限（接口边界）

| 上限 | 默认值 | 位置 |
| --- | --- | --- |
| 域元素范围 | `[0, 100]` 内整数 | 固定，`P=101` |
| 参与方编号 | `0, 1, 2` | 固定，三方 |
| 单个转录机操作数 | 1000（构造可覆盖） | `MAX_OPERATIONS` |
| 单个转录机消息数 | 10000（构造可覆盖） | `MAX_MESSAGES` |
| ID 长度（`session_id`/`op_id`/`triple_id` 共用） | 128 字符 | `MAX_ID_LENGTH` |

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
  - 乱序投递、缺方打开无状态变更、重复一致幂等、冲突消息中止操作、
    `triple_id` 串用/跨操作复用拒绝、串操作各自消费、批次失败回滚、
    上限拒绝。
- `tests/test_sessions.py`（本次新增）：
  - 消息会话绑定与默认会话兼容、非法 `session_id` 拒绝；
  - **两个合成会话 `sA`/`sB`（含同名操作）严格交错且各自乱序投递**，
    双方份额用独立朴素公式
    `(c_i + d·b_i + e·a_i + [i=0]·d·e) mod 101` 逐方核对；
  - 三元组领取全局互斥（跨会话预留/消费/作废均拒绝，错误带占用方会话）；
  - **全部消息打乱后整批重放**：打开前后重复均 `duplicate`、重复打开
    返回缓存对象；缺方重放不改变缺方集合；
  - 冲突只中止涉事操作（另一会话照常打开）、同操作引用不同三元组中止、
    批次内冲突中止与无关新操作回滚并存、已打开操作遇冲突中止但三元组
    保持 `consumed`；
  - 一个手算小例子（`a=b=0` 三元组，`d=x, e=y`；`a=b=1` 时
    `d=e=1` 且常量 `d·e=1` 只进入 0 号份额）。
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
3. **三元组在首条消息时领取（预留）、打开成功时消费、冲突中止时作废**：
   预留阻止“同一三元组被两个未完成操作（含跨会话同名操作）同时引用”；
   缺方打开不消费，允许补齐后重试；`voided`/`consumed` 都是终态。
4. **会话只隔离转录，不隔离三元组**：`session_id` 让同名操作可在不同
   会话中并存，但全局只有一个 `TripleBank`，三元组领取严格一次性。
5. **冲突即中止而非“拒绝后继续”**：冲突意味着同一参与方给出了两个版本
   的打开值，转录已无法判定哪份权威，故冻结该操作并作废其三元组；
   恢复方式只能是换新 `op_id` + 新三元组重做。中止是终态，重放一致
   消息也不复活，避免“靠再发一次掩盖冲突”。
6. **消息不可变（frozen dataclass）**：冲突检测可用整体相等直接判定。
7. `c` 不由调用方提供而由 `a*b mod 101` 计算，杜绝三元组内部不一致。

## 已知限制（不承诺的能力）

- **不提供恶意安全**：不验证参与方是否按真实份额构造 `d_i/e_i`，恶意方
  可提交不一致消息；本库只保证转录层的一致性、一次性与冲突中止语义，
  属于半诚实模型教学模拟。冲突被检出后没有“识别作恶方/自动恢复”协议，
  只有中止 + 作废 + 人工换新操作重做。
- 只实现本题契约的有限数学子集：GF(101) 加法/乘法/取负与三方 Beaver
  乘法；**不包含**域逆元/除法、其他域、MAC、承诺、网络协议，不宣称与任何
  行业标准 MPC 实现兼容。
- 无并发保护（单线程教学使用）、无持久化；进程结束后会话/操作/中止
  状态不保留，本次也不实现崩溃恢复日志。
- 会话/操作无身份认证：任何能调用投递接口的代码都可向任一会话发消息；
  本库不做认证份额或访问控制。
- 题目中“搜索预算耗尽/证明无解”的区分对本模块不适用：本模块没有搜索或
  求解过程，只有可直接判定的接受/幂等/中止/拒绝。
