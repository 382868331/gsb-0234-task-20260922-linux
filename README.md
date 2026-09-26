# sss_mult —— 秘密分享乘法与份额刷新的离线转录模拟（教学模块）

在素数域 **GF(101)** 上模拟**三方加法秘密分享**，并用 **Beaver 三元组**
完成一次乘法的**离线转录**：收集各方打开消息、做一致性/一次性检查，
齐三方后输出各方视图与重构乘积。另提供**份额刷新（refresh）**：
每对参与方共享掩码、一方加一方减，**换份额不换秘密**，确认收齐后
原子切换到新世代。

- Python 版本：**3.14.7**（仅使用标准库，如 `random`、`dataclasses`、`unittest`）
- 无需安装任何第三方依赖；不需要网络、账号、真实密钥、数据库、中间件、
  云 API 或 Docker。
- 核心、测试、演示全部离线、确定性（示例随机源均带固定种子）。

## 隔离 venv 准备

不需要任何第三方依赖，但建议用隔离虚拟环境运行：

```bash
python3.14 -m venv .venv        # 需要 Linux 原生 Python 3.14.7
. .venv/bin/activate
python -V                        # 应显示 3.14.7
```

## 准确命令

```bash
# 运行全部必要测试（在仓库根目录）
python -m unittest discover -s tests -v

# 运行固定输入演示（约 0.1 秒，远小于 8 秒预算；真实计算，无 sleep/预录）
python demo.py

# 运行刷新迭代演示（< 90 秒，实际约 0.05 秒；真实离线计算）
python demo_iteration.py
```

## 份额刷新（refresh）

对三方加法分享 `s = s0 + s1 + s2 (mod 101)`，刷新为每一对参与方
`i < j` 引入一个新掩码 `r_ij`：`i` 把自己的份额**加上** `r_ij`，
`j` 把自己的份额**减去** `r_ij`。每对的 `+r`/`-r` 在总和中抵消，
因此**秘密不变、份额全部换新**。掩码由调用方注入；经 `MaskBook`
拆成逐方视图 `PartyMaskView`，每方只持有与自己相关的掩码项。

刷新由 `RefreshPlan` 绑定**刷新 ID、旧世代、新世代、参与方集合**，
`RefreshManager` 收齐三方确认后才在 `finalize` 中**原子切换**世代；
此前旧世代完整可用，新旧份额不会混入同一次乘法。语义：

- 重复且一致的 `begin`/`confirm` 幂等（返回 `"duplicate"`）。
- 参数冲突（同一方确认不同计划、同一刷新 ID 换参数、世代不匹配）
  使本轮**失败**并抛 `RefreshConflictError`，旧世代完整保留；
  之后可用新刷新 ID 重开一轮。
- 缺确认时 `finalize` 抛 `RefreshStateError`，不改变任何状态。
- 公开转录 `public_record()` 只含计划与确认方，**不含掩码或私有份额**。
- 实现**不重建秘密再分享**：逐方本地 `share + delta(mod p)`。

```python
from sss_mult import MaskBook, RefreshManager, RefreshPlan, random_share
import random

rng = random.Random(20260926)
m = RefreshManager(random_share(42, rng))
plan = RefreshPlan("refresh-1", old_generation=0, new_generation=1)
m.begin(plan)
for party in (2, 0, 1):          # 确认可乱序
    m.confirm(party, plan)
new_shares = m.finalize(MaskBook({(0, 1): 11, (0, 2): 22, (1, 2): 33}))
assert sum(new_shares) % 101 == 42   # 秘密不变
```

刷新相关接口（均从 `sss_mult` 导入）：

| 名称 | 说明 |
| --- | --- |
| `RefreshPlan(refresh_id, old_generation, new_generation, parties)` | 公开绑定参数（不可变），构造时校验 |
| `MaskBook(pairs)` / `MaskBook.random(rng)` | 调用方侧成对掩码；`party_view(i)` 拆逐方私有视图 |
| `PartyMaskView` / `MaskTerm` | 逐方掩码视图；`delta()` 为本方总修正量 |
| `refresh_share(share, view)` | 逐方本地更新：`share + delta (mod p)` |
| `RefreshManager(shares, *, generation=0)` | 协调器：`begin/confirm/finalize`，只读 `generation/shares/plan/failed/is_complete/missing_parties/public_record` |
| `RefreshError` / `RefreshConflictError` / `RefreshStateError` | 刷新错误（继承 `TranscriptError`） |

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
| `Message(op_id, party, triple_id, d, e)`（不可变） | 一条打开消息；构造时即完成字段校验 |
| `derive_messages(op_id, triple, x_shares, y_shares)` | 诚实方一次性生成三条消息 |
| `TripleBank()` | 三元组注册表；`register/get/state`，生命周期 `free → reserved(op) → consumed` |
| `Transcript(bank, *, max_operations=1000, max_messages=10000)` | 转录机（见下） |
| `honest_prepare(op_id, triple, x, y, rng)` | 语法糖：返回 `(x_shares, y_shares, messages)` |

`Transcript` 主要方法：

- `add_message(msg) -> "accepted" | "duplicate"`：投递一条消息。
- `add_messages(iterable) -> list[str]`：批量投递，**原子**：任一条失败则
  回滚本批次全部变更（消息、新建操作、三元组预留）。
- `open(op_id) -> OperationResult`：齐三方才能打开；重复打开返回缓存结果。
- 只读：`is_complete / is_open / missing_parties / operations_count /
  messages_count / result`。
- `OperationResult` 字段：`op_id, triple_id, d, e, x, y, views, product`；
  `views` 为三个 `PartyView(party, a_i, b_i, c_i, d_i, e_i, d, e, z_i)`；
  `output_shares()` 返回 `(z_0, z_1, z_2)`。

最小示例：

```python
import random
from sss_mult import TripleBank, Transcript, make_triple, honest_prepare

bank = TripleBank()
rng = random.Random(20260923)
bank.register(make_triple("t1", a=17, b=9, rng=rng))
_, _, msgs = honest_prepare("mul-1", bank.get("t1"), x=12, y=8, rng=rng)

t = Transcript(bank)
t.add_messages(msgs)          # 可乱序、可含重复一致消息
result = t.open("mul-1")
assert result.product == (12 * 8) % 101
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
  `triple_id` 也报此错。**重复且完全一致的消息幂等忽略。**
- `TripleMismatchError`（`ConflictError` 子类）：同一操作的消息引用了不同
  `triple_id`。
- `TripleReusedError`：`triple_id` 已被其他操作预留（`consumed=False`）或
  已被打开消费（`consumed=True`）。每个 `triple_id` **恰好消费一次**。
- `IncompleteTranscriptError`：缺方时打开；携带 `missing` 参与方元组。
  该错误**不改变任何状态**（不消费三元组、不缓存结果），补齐后可重试。
- `UnknownOperationError`：打开一个从未收到消息的 `op_id`。
- `LimitError`：超过操作数/消息数上限。

失败原子性：单条 `add_message` 的所有检查先于状态落盘；`add_messages`
整批回滚。不存在“未说明的部分变更”。

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
- `tests/test_refresh.py`：刷新语义——零掩码、份额恰巧不变（修正量
  恰为 0）、乱序/重复确认、缺确认阻塞且状态不变、冲突失败保留旧世代、
  同一刷新 ID 换参数拒绝、世代重放拒绝、失败后可换新 ID 重开、
  刷新后 Beaver 乘法正确；秘密不变性只用朴素 `sum(shares) % 101`
  独立检查，不调用被测核心生成期望值。
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
3. **三元组在首条消息时预留、打开成功时消费**：预留阻止“同一三元组被
   两个未完成操作同时引用”；缺方打开不消费，允许补齐后重试。
4. **消息不可变（frozen dataclass）**：冲突检测可用整体相等直接判定。
5. `c` 不由调用方提供而由 `a*b mod 101` 计算，杜绝三元组内部不一致。

## 已知限制（不承诺的能力）

- **不提供恶意安全**：不验证参与方是否按真实份额构造 `d_i/e_i`，恶意方
  可提交不一致消息；本库只保证转录层的一致性与一次性语义，属于半诚实
  模型教学模拟。
- 刷新同样是**半诚实本地模拟**：假定各方如实应用自己的掩码修正，
  不验证其知识一致性，不宣称抵抗恶意参与方。
- 只实现本题契约的有限数学子集：GF(101) 加法/乘法/取负与三方 Beaver
  乘法；**不包含**域逆元/除法、其他域、MAC、承诺、网络协议，不宣称与任何
  行业标准 MPC 实现兼容。
- 无并发保护（单线程教学使用）、无持久化。
- 题目中“搜索预算耗尽/证明无解”的区分对本模块不适用：本模块没有搜索或
  求解过程，只有可直接判定的接受/拒绝。
