# sss_mult —— 秘密分享乘法的离线转录模拟（教学模块）

在素数域 **GF(101)** 上模拟**三方加法秘密分享**，并用 **Beaver 三元组**
完成一次乘法的**离线转录**：收集各方打开消息、做一致性/一次性检查，
齐三方后输出各方视图与重构乘积。

此外提供**点积（dot_product）**：一次操作打包多个乘法门，每个位置独占
一个预先给定的 triple_id，所有位置三方齐全后**只发布一次**总结果。

- Python 版本：**3.14.7**（仅使用标准库，如 `random`、`dataclasses`、`unittest`）
- 无需安装任何第三方依赖；不需要网络、账号、真实密钥、数据库、中间件、
  云 API 或 Docker。
- 核心、测试、演示全部离线、确定性（示例随机源均带固定种子）。

## 准确命令

```bash
# 运行全部必要测试（在仓库根目录；也可用 PATH 中的 python3.14）
./.venv/bin/python -m unittest discover -s tests -v

# 运行固定输入演示（实测约 0.05 秒，远小于 90 秒预算；真实计算，无 sleep/预录）
./.venv/bin/python demo.py
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
| `Message(op_id, party, triple_id, d, e)`（不可变） | 一条打开消息；构造时即完成字段校验 |
| `derive_messages(op_id, triple, x_shares, y_shares)` | 诚实方一次性生成三条消息 |
| `TripleBank()` | 三元组注册表；`register/get/state`，生命周期 `free → reserved(op) → consumed` |
| `Transcript(bank, *, max_operations=1000, max_messages=10000)` | 转录机（见下） |
| `honest_prepare(op_id, triple, x, y, rng)` | 语法糖：返回 `(x_shares, y_shares, messages)` |

点积相关（新增）：

| 名称 | 说明 |
| --- | --- |
| `DotMessage(op_id, pos, party, triple_id, d, e)`（不可变） | 点积某位置的一条打开消息，键为 `(op_id, pos, party)` |
| `derive_dot_messages(op_id, triples, x_shares_list, y_shares_list)` | 诚实方一次生成全部 n×3 条点积消息 |
| `dot_product_prepare(op_id, triples, x_values, y_values, rng)` | 语法糖：返回 `(x_shares_list, y_shares_list, messages)` |
| `Transcript.begin_dot_product(op_id, triple_ids)` | 声明点积操作：**先完整验证长度/op_id/全部三元组可用性，再整批原子预留**；空列表合法 |
| `Transcript.add_dot_message(s)` | 投递点积消息；重复一致幂等，冲突拒绝并保留失败证据 |
| `Transcript.open_dot(op_id) -> DotProductResult` | 所有位置三方齐全才打开，整个点积只发布一次；重复打开幂等 |
| `Transcript.dot_product(op_id, triple_ids, x_shares_list, y_shares_list)` | 一站式诚实点积（声明→派生消息→投递→打开） |
| `Transcript.dot_conflicts(op_id)` / `is_dot_complete(op_id)` | 只读：失败证据 / 是否齐消息 |
| `DotProductResult` | 字段 `op_id, length, positions, party_sums, value`；`output_shares()` 返回各方总份额 |

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

## 点积协议要点

- 每个位置 k 是一个独立 Beaver 乘法门，独占 triple_ids[k]；公开值 d_k、e_k
  按位置分别求和，各方输出份额 z_i(k) 后，**各方先把自己的 z_i 跨位置相加**，
  重构时三方总份额再相加即得点积值。
- begin_dot_product 的验证顺序：op_id 未占用 → 长度上限 → 每个 triple_id 合法
  → 批内无重复 → 全部已注册且空闲；**任一失败都不预留任何三元组**
  （不存在消费一半才发现重复 ID）。
- 预留是单向的：点积三元组一经预留**永不释放**——即使操作中途失败终止，
  也不能重新用于另一秘密运算；打开成功后整批转为 consumed。
- d/e 消息可任意乱序到达；每个位置仍需三方齐全；任一位置缺方时 open_dot
  抛 IncompleteTranscriptError（missing 为 (pos, party) 二元组），不改变状态。
- 空点积（长度 0）合法：value = 0，不消耗任何三元组。

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
| 单次点积长度 | 1000（构造可覆盖，可为 0） | `MAX_DOT_PRODUCT_LEN` |

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
- `tests/test_dot_product.py`：二维手算点积（含绕模 100·100）、随机向量对照
  朴素域运算、点积与普通乘法消息全局乱序交织、批内重复/未知/已消费三元组
  在预留前整批拒绝、长度上限、op_id 冲突、中途冲突拒绝且证据保留、
  失败后三元组不可转用、批次投递回滚、空点积返回零且不消耗三元组。
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
- 只实现本题契约的有限数学子集：GF(101) 加法/乘法/取负与三方 Beaver
  乘法；**不包含**域逆元/除法、其他域、MAC、承诺、网络协议，不宣称与任何
  行业标准 MPC 实现兼容。
- 无并发保护（单线程教学使用）、无持久化。
- 题目中“搜索预算耗尽/证明无解”的区分对本模块不适用：本模块没有搜索或
  求解过程，只有可直接判定的接受/拒绝。
