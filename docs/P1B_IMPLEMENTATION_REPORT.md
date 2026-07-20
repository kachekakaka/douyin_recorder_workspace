# P1B 实施报告：单房间 recipient 持久化与状态投影

状态：**第一批工程实现完成，等待最终 exact-head CI 与 PR Review；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：GitHub Issue #7、协议事实 Issue #1  
分支：`feature/p1b-single-room-recipient-foundation`

## 1. 已完成里程碑

- P1A 已通过 PR #4 合并到 `main@e2fee9a320529935e4d88f587b12f233d848121f`；
- SQLite schema v4 已加入 recipient/session 审计字段、contract 快照和查询索引；
- recipient canonical event、重复计数、迟到标记与 Waiting/Active/Unknown 区间采用同一 SQLite 写事务；
- 断线后立即进入 Unknown，重连本身不恢复旧 recipient；
- 跨 runtime 的 monotonic 值不直接比较；
- 只读 state/events/intervals API 不公开 raw payload、extra 或未知字段内容；
- 同一合成 fixture 同时驱动 reducer 与临时 SQLite，两个状态投影结果一致；
- Python 3.12/3.13 和 Windows `verify.bat` 均执行数据库 replay；
- 所有 64 位 ID 继续作为字符串。

## 2. SQLite schema v4

新增字段与索引：

- `sessions.protocol_contract_sha256`；
- `sessions.protocol_live_verified`；
- session 开始/结束 monotonic 和结束 runtime 审计；
- `recipient_events.envelope_msg_id`、`server_time_unit`、`payload_size`、`unknown_fields_json`；
- `recipient_intervals.ended_runtime_instance_id`；
- recipient events、intervals、sessions 的查询索引。

migration 保持递增和 checksum，不修改 v1-v3 历史含义。

## 3. 事务状态投影

```text
session start
    → Waiting(waiting_first_event)

首条有效 recipient
    → Active(recipient)

相同 dedup key
    → duplicate_count + 1
    → 不重新改变状态

相同 recipient 的新 canonical event
    → 保存 event
    → 不开启重复 interval

recipient 切换
    → 关闭旧 Active
    → 开启新 Active

空 recipient
    → Unknown(empty_recipient)

IM 断线
    → Unknown(im_disconnected)

IM 重连
    → 继续 Unknown
    → 直到下一条有效目标事件

迟到事件
    → 保存 is_late=1
    → 不回滚当前状态
```

canonical event 写入、duplicate 处理、开放区间关闭和新 interval 开启都在单一数据库写事务中完成；service 同时使用每 session 异步锁。

## 4. 只读 API

```text
GET /api/rooms/{room_key}/recipient-state
GET /api/rooms/{room_key}/recipient-events
GET /api/rooms/{room_key}/recipient-intervals
```

API 验证房间存在、`session_id` 属于同一房间和分页边界。没有 session 时返回稳定空状态；响应不含：

```text
raw_payload_json
extra_json
unknown_fields_json
完整 WSS
Cookie
真实原始 payload
```

## 5. 确定性 SQLite replay

命令：

```bash
python tools/replay_recipient_fixture_to_db.py \
  --output userdata/recipient-db-replay.json
```

固定 synthetic fixture 的结果：

```text
target messages       7
canonical events      6
duplicate frames      1
late events           1
intervals             7
schema version        4
contract live_verified=false
```

公开数据库报告与既有 reducer 的 Waiting/Active/Unknown interval 完全一致，并且不含 raw payload、fixture frame 或数据库绝对路径。

## 6. 验证结果

本地全量验证：

```text
pip check                         passed
repository baseline               passed
source boundary                   passed
compileall                        passed
Ruff                              passed
pytest                            49 passed
deterministic reducer replay      passed
deterministic SQLite replay       passed
JavaScript syntax                 passed
FFmpeg lavfi smoke                passed
```

远端里程碑：

```text
da2a6d57e3b0e35c0f52cdf65f2a266c6e98914c
recipient transaction core
CI 29710780492 success

caadc109d29f3eb1184cd696bdbdcc496fadec8a
read-only recipient API
CI 29710945098 success

6b55ea606983641c065e5130264d83ed1bbac402
recipient SQLite replay + Python/Windows CI
CI 29711086954 success
```

CI 覆盖 Python 3.12、Python 3.13、Windows、FFmpeg smoke、前端语法和 Git Bundle/source ZIP 恢复资产。最终文档 head 的 exact CI 以 PR #8 描述为准。

## 7. 安全与隐私 Review

- 只有目标 method 可以改变 recipient 状态；
- API 不暴露数据库 raw payload、extra 或 unknown fields；
- session 查询必须与 `room_key` 匹配，防止跨房间读取；
- 64 位 ID 不转为 JavaScript number；
- synthetic fixture 与 synthetic contract 都保持 `live_verified=false`；
- CI 输出只包含合成 ID 和公开审计字段；
- 未新增登录、验证码/风控绕过、互动发送或身份推断 fallback；
- recipient 状态变化不调用 FFmpeg，也不重启媒体连接。

## 8. 协议证据门禁

本批次只消费“已经解码且符合显式 contract 的事件”。合成事件用于验证事务和状态机，不能替代真实现场事实。以下条件满足前，不得把本批描述为真实 recipient 自动接入完成：

- 普通交互浏览器实际建立当前 IM WSS；
- 观察到 `WebcastGroupLiveGiftRecipientRecommendMessage`；
- 字段号、空 recipient、重复、切换与重连行为经去标识人工审查；
- 形成可回放的最小真实 fixture；
- `app/douyin/contracts/provisional_v1.json` 的 `live_verified` 经审查后才可变更。

Issue #1 必须继续保持开启。
