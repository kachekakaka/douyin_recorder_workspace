# P1B 测试矩阵

## 迁移

- 空库直接迁移到 schema v4；
- v1、v2、v3 依次迁移到 v4；
- migration checksum 保持稳定；
- recipient event、interval 和 session 查询索引存在；
- 所有平台 ID 字段使用 TEXT；
- 应用 readiness、房间 API 和 repository 测试均断言 schema v4。

覆盖：`tests/unit/test_database_and_settings.py`、`tests/integration/test_app.py`、`tests/integration/test_room_api.py`、`tests/unit/test_rooms.py`。

## 事务状态机

- session 开始生成 Waiting；
- 首条有效事件关闭 Waiting 并开启 Active；
- 相同 recipient 的新 canonical event 不重复开启区间；
- 相同 dedup key 只更新 `duplicate_count` 和 `last_received_at_ms`；
- recipient 切换关闭旧 Active 并开启新 Active；
- 空 recipient 关闭 Active 并开启 `Unknown(empty_recipient)`；
- IM 断线关闭当前区间并开启 `Unknown(im_disconnected)`；
- 重连本身保持 Unknown；
- 重连后首条有效事件开启 Active；
- 迟到事件保存为 `is_late=1`，不回滚当前状态；
- 同 runtime monotonic 不得倒退；
- 跨 runtime 的 monotonic 值不直接比较；
- session 结束关闭开放区间和 session；
- 非目标 method 不得改变状态；
- raw payload 必须是有效 JSON 且不超过 1 MiB。

覆盖：`tests/unit/test_recipient_session_repository.py`。

## API

- `recipient-state`、`recipient-events`、`recipient-intervals` 只读接口；
- 非法 `room_key`、未知房间、无 session 和分页边界；
- 指定 `session_id` 时必须属于同一房间；
- 64 位 ID 保持字符串；
- API 不含 `raw_payload_json`、`extra_json`、`unknown_fields_json` 或真实 payload；
- GET 不放宽现有 Host 安全边界；
- `/api/status` 明确处于 P1B foundation，并继续返回 `protocol_live_verified=false`。

覆盖：`tests/integration/test_recipient_api.py` 及既有应用/房间 API 测试。

## 确定性数据库回放

- 相同 fixture 在两个空临时数据库中生成相同的公开 state/events/intervals 报告；
- SQLite interval 与既有 reducer replay 一致；
- 7 条目标消息投影为 6 条 canonical event、1 次 duplicate、1 条 late event 和 7 个 interval；
- 报告不含数据库绝对路径、runtime 随机值、raw payload 或隐私字段；
- replay 工具失败时返回非零退出码；
- synthetic fixture 和 contract 均保持 `live_verified=false`；
- CI 和 Windows `verify.bat` 实际执行 replay。

覆盖：`tests/unit/test_recipient_database_replay.py`、`tools/replay_recipient_fixture_to_db.py`。

## 远端矩阵

| 里程碑 | exact head | CI run | 结果 |
|---|---|---:|---|
| transaction core | `da2a6d57e3b0e35c0f52cdf65f2a266c6e98914c` | `29710780492` | success |
| read-only API | `caadc109d29f3eb1184cd696bdbdcc496fadec8a` | `29710945098` | success |
| SQLite replay | `6b55ea606983641c065e5130264d83ed1bbac402` | `29711086954` | success |

每个 run 均覆盖 Python 3.12、Python 3.13、Windows `verify.bat`、前端语法、FFmpeg smoke 和恢复资产。最终文档 head 的 CI 记录在 PR #8。
