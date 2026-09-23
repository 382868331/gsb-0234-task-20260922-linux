# 秘密分享乘法的离线转录模拟（beaver_transcript）

教学用的三方加法秘密分享 + Beaver 三元组乘法的**离线转录模拟**库。在素数域
**F_101（p=101）** 上，用确定性合成数据模拟一次乘法协议的完整转录：三元组
分发、各本地广播 `d_i / e_i`、收齐后打开、输出各方视图并重构乘积。

**安全声明：仅模拟半诚实（诚实但好奇）教学场景，不承诺恶意安全**，不包含
MAC、承诺、零知识证明或任何主动攻击防御。

## 环境与依赖

- Python **3.14.7**（3.10+ 亦可运行，目标版本为 3.14.7）
- **仅标准库**（`random`、`dataclasses`、`math`、`unittest`），`pip install` 无需任何第三方包
- 完全离线：不需要网络、账号、真实密钥、数据库、中间件、云 API 或 Docker

## 运行命令

```bash
# 必要测试（含 101×101 穷举对照）
python -m unittest discover -s tests -v

# 固定输入演示：一个正确重构 + 实际触发的复用/冲突拒绝
python demo.py
```

demo 为纯内存计算，无 `sleep`、无预录结果，正常在 1 秒内完成（远低于约 8 秒上限）。

## 协议回顾（本模块实现的数学子集）

- 三方加法分享：`x = x_0 + x_1 + x_2 (mod 101)`。
- 离线三元组 `(a, b, c)`，其中 `c = a·b mod 101`，分别以加法份额预发给各方。
- 在线阶段各方广播：`d_i = x_i − a_i`、`e_i = y_i − b_i`。
- 收齐后公开 `d = Σd_i`、`e = Σe_i`。
- 各方输出份额：`z_i = c_i + d·b_i + e·a_i (mod 101)`，**常量 `d·e` 只加给 0 号方**。
- 重构：`z = Σz_i ≡ x·y (mod 101)`。

## 公开数据结构与接口

| 名称 | 说明 |
|---|---|
| `P = 101` | 域特征常量 |
| `check_field_element(v, name=...)` | F_101 元素校验，返回规范化 int |
| `share_secret(value, rng)` | 加法分享，返回长度 3 的份额 list |
| `reconstruct(shares)` | `sum(shares) mod 101` |
| `TripleSource(rng)` | 离线三元组源 |
| `.issue(triple_id)` | 随机 a、b 合成三元组 |
| `.issue_values(triple_id, a, b)` | 显式 a、b 合成（`c=a*b mod 101`） |
| `.consumed_ids` | 已消费 id 的只读集合 |
| `TripleShare(triple_id, a, b, c)` | 单方的三元组份额（frozen dataclass） |
| `Party(index)` | 参与方本地状态 |
| `.receive_triple(share)` | 接收三元组份额（同份额幂等，冲突拒绝） |
| `.contribute(op_id, triple_id, x_i, y_i)` | 生成 `d_i/e_i` 两条 `Message` |
| `Message(op_id, sender, kind, value)` | 转录消息，`kind ∈ {"d","e"}`（frozen，构造即校验） |
| `Transcript(parties)` | 中心转录板 |
| `.begin(op_id, triple_id)` | 登记操作（同参幂等，改绑拒绝） |
| `.post(message)` / `.post_bulk(messages)` | 投递消息 / 原子批量投递 |
| `.is_complete(op_id)` / `.missing(op_id)` | 齐套判断 / 缺失明细 |
| `.open(op_id)` | 打开，返回 `OpResult` |
| `OpResult(op_id, triple_id, d, e, views, z)` | `views` 为三方 `PartyView`，`z` 为重构积 |
| `PartyView(party, triple_id, a_i, b_i, c_i, d_i, e_i, d, e, z_i)` | 单方打开后视图 |

### 错误语义（`beaver_transcript.errors`，均可 `except BeaverError` 兜底）

| 异常 | 触发条件 |
|---|---|
| `FieldValueError` | 域元素非法、id 非法、参与方编号非法（继承 `ValueError`） |
| `TripleReuseError` | 同一 `triple_id` 在 `TripleSource` 或某 `Party` 处第二次消费 |
| `MessageConflictError` | 同一 `(op_id, sender, kind)` 收到取值不同的消息；或收到与本地不符的三元组份额 |
| `IncompleteTranscriptError` | `open` 时三方 d/e 未收齐，消息内含缺失的 `(sender, kind)` 明细 |
| `UnknownPartyError` | 消息发送方不在转录板参与方集合内 |
| `ProtocolStateError` | 未 `begin` 即投递、同 op 改绑 triple、缺本地份额等状态机违规 |

幂等与原子性约定：

- 一致消息重复投递幂等（`post` 返回 `False`）；冲突消息被拒绝且**不覆盖**已记录值。
- `post_bulk` 先整体模拟再落盘：批次内含任何非法/冲突消息时整批不留变更。
- `TripleSource.issue_values` 参数非法时不登记消费；`Party.contribute` 拒绝复用时不登记新 op。
- 打开失败不产生任何输出对象，转录板状态不变，补齐后可重试。

## 输入上限（接口边界）

- 所有域元素：`int`，范围 `[0, 100]`。
- `op_id` / `triple_id`：非空 `str`，长度 ≤ `MAX_ID_LEN = 256`。
- 参与方数固定为 **3 方**（`NUM_PARTIES = 3`），参与方编号必须是 `int` 且在 `{0,1,2}`。
- 明确拒绝：`bool`（即使是 int 子类）、`float`（**含 NaN / ±Infinity，任何 float 都拒绝**）、
  `str` 数字、`None` 等非 int；越界 int **不取模、不截断**，直接报错。
- 错误消息均带参数名或 `op_id/triple_id/sender`，可定位。

## 误差口径

本模块全部运算为 F_101 上的**精确整数模运算**，不存在浮点参与，
因此**没有数值近似误差，也不使用任何容差**。穷举测试对 101² 个 `(x,y)` 组合
逐字段精确比对独立参考与朴素域乘法 `x*y mod 101`。

## 设计取舍

- **中心转录板**：真实 MPC 中 d/e 由点对点/广播网络传播；本模拟用 `Transcript`
  集中收集，专注转录语义（齐套、幂等、冲突），不模拟网络故障与异步时序。
- **确定性合成**：所有随机数来自调用方传入的 `random.Random`（固定种子），
  三元组“可信分发方”由 `TripleSource` 扮演；测试与 demo 完全可复现。
- **双层一次性消费**：`TripleSource` 防止同一三元组被重复签发，`Party` 防止
  同一份额被同一方用于两个操作——两者独立报错，便于定位。
- **独立测试参考**：`tests/reference.py` 只用裸 int 取模算术重算期望值，
  不 import 被测核心的任何函数；往返一致（分享→重构）仅作补充断言。

## 已知限制

- 仅实现 p=101、加法分享、单轮 Beaver 乘法这一有限数学/协议子集，
  **不兼容、也不宣称任何行业标准 MPC 框架**。
- 半诚实模型：不验证份额正确性，恶意方投错份额/冲突消息只能在“转录位置”
  被检测（同一槽位两个不同值），无法发现语义错误的单份消息。
- 无网络、持久化、并发锁与认证；`Transcript`/`Party` 非线程安全。
- 不支持定点/浮点编码、截断、比较等高层运算。

## 目录

```
beaver_transcript/   # 本题库：field / sharing / triples / protocol / errors
tests/               # unittest（穷举、乱序、缺方、复用、冲突、串操作、独立参考）
demo.py              # 固定输入演示
README.md
```
