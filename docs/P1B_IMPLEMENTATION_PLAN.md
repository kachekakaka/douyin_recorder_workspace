# P1B 实施计划：单房间 recipient 持久化与状态投影

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：GitHub Issue #7、协议事实 Issue #1  
分支：`feature/p1b-single-room-recipient-foundation`  
基线：`main@e2fee9a320529935e4d88f587b12f233d848121f`

## 1. 目标

P1B 第一批只把“已经解码且符合显式 contract 的目标事件”事务化写入 SQLite，并生成严格的 Waiting/Active/Unknown 时间线。它不负责猜测协议字段，也不把合成 fixture 当作真实现场证据。

```text
DecodedRecipientEvent
    ↓
单房间串行事务
    ├─ canonical recipient_event 去重/重复计数/迟到标记
    └─ recipient_interval 关闭与开启
    ↓
只读 state / events / intervals API
```

## 2. 不可改变语义

1. 当前推荐收礼人只由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. 首条有效事件前为 `waiting`；空 recipient、IM 断线，以及重连后尚未收到新有效事件时为 `unknown`。
3. 推荐对象变化不得重启 FFmpeg，也不得切断媒体连接。
4. 不增加 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称、房间标题或画面位置 fallback。
5. 所有抖音 64 位 ID 在 Python、SQLite、JSON 和 JavaScript 中保持字符串。
6. `app/douyin/contracts/provisional_v1.json` 必须保持 `live_verified=false`，Issue #1 保持开启。

## 3. 当前批次范围

### 3.1 SQLite schema v4

通过新增 migration 而非修改历史 migration，补齐：

- `recipient_events.envelope_msg_id`；
- `recipient_events.server_time_unit`；
- `recipient_events.payload_size`；
- `recipient_events.unknown_fields_json`；
- `recipient_intervals.ended_runtime_instance_id`；
- recipient event/interval 的按 session、时间查询索引。

迁移继续使用 checksum；旧库必须能从 v1/v2/v3 递增升级。

### 3.2 RecipientRepository

实现单事务操作：

- 开始 session 并开启 `waiting_first_event` 区间；
- 写入 canonical event；
- `UNIQUE(session_id, dedup_key)` 去重；
- 重复帧只增加 `duplicate_count` 和 `last_received_at_ms`；
- 迟到事件保存为 `is_late=1`，不回滚当前区间；
- 空 recipient 进入 `unknown(empty_recipient)`；
- IM 断线进入 `unknown(im_disconnected)`；
- IM 重连本身不恢复断线前对象；
- 新有效对象关闭当前区间并开启 `active`；
- 相同 recipient 不重复开启区间；
- 结束 session 时关闭当前区间；
- 同一 runtime 才比较 monotonic；跨 runtime 关闭时 `ended_monotonic_ns` 为 null，并记录 `ended_runtime_instance_id`。

canonical event 与 interval 转换必须在同一个 `BEGIN IMMEDIATE` 写事务中完成。

### 3.3 只读 API

新增：

```text
GET /api/rooms/{room_key}/recipient-state
GET /api/rooms/{room_key}/recipient-events
GET /api/rooms/{room_key}/recipient-intervals
```

公开响应不得包含：

- `raw_payload_json`；
- 原始 payload；
- Cookie、完整 WSS 或请求头；
- 未经 allowlist 的 `extra` 内容。

API 在没有 active session 时返回稳定的空状态，而不是猜测当前对象。

### 3.4 确定性数据库回放

新增工具把现有合成 fixture 投影到临时 SQLite，并验证：

- canonical event 数；
- duplicate count；
- late event；
- waiting/active/unknown 区间；
- 断线后 Unknown；
- 重连不恢复旧对象；
- 所有 ID 仍为字符串；
- fixture 和 contract 均不得标记 `live_verified=true`。

## 4. 安全与隐私边界

- 真实 probe 输出继续只写 `userdata/`，不会提交 GitHub。
- GitHub 测试只使用合成 ID、合成 payload 和合成 fixture。
- raw payload 只允许存放在本机 SQLite/私有 probe 目录，不由管理 API 返回。
- 错误、日志和 CI 输出不得包含真实 recipient 明文、Cookie、完整 WSS 或原始 payload。
- 当前批次不新增登录、验证码、风控绕过或互动发送。

## 5. 分阶段提交

1. `docs: define P1B recipient persistence plan`
2. `feat: add recipient persistence schema v4`
3. `feat: persist recipient events and strict timeline transactions`
4. `feat: expose read-only recipient state APIs`
5. `test: replay recipient fixture through SQLite`
6. `docs: report P1B evidence gate and remaining protocol facts`

每个里程碑完成后立即 push；禁止 force push。

## 6. 验收命令

```bash
python -m pip install -r requirements/dev.lock
python -m pip check
python tools/verify_repository_baseline.py
python tools/verify_source.py
python -m compileall -q app tests tools
python -m ruff check --no-cache app tests tools
python -m pytest -q -p no:cacheprovider --tb=short
python tools/replay_recipient_fixture.py --quiet
python tools/replay_recipient_fixture_to_db.py --quiet --database <临时数据库>
find web -type f -name '*.js' -print0 | xargs -0 -n1 node --check
```

Windows 必须由 GitHub Actions 实际执行 `verify.bat`。

## 7. 完成门禁

本 Issue 的工程范围可以在合成 fixture 下完成，但以下事实形成经去标识、人工审查、可回放的真实 fixture 前：

- 不得把 contract 改为 `live_verified=true`；
- 不得宣布 P1B recipient 接线完成；
- 不得自动建立长期真实 IM 连接；
- 不得关闭 Issue #1。

真实待验证事实包括目标 method 的当前字段号、空 recipient wire 值、open ID、重复、切换、重连、`change_reason_enum` 和延迟分布。