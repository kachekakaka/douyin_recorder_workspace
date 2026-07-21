# P1D 实施计划：单房间录制 Session 与 recipient 生命周期闭环

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #11、协议事实 Issue #1  
分支：`feature/p1d-single-room-recording-session`  
基线：`main@e7e98117a574465061ebe07f4ca623d4ae101b32`

## 1. 目标

P1D 将 P1A 的流候选与 FFmpeg Supervisor、P1B 的 recipient 事务投影连接成一个显式启动、可审计、可停止的单房间录制 Session：

```text
start-recording
    ↓
Room + in-memory StreamCandidate
    ↓
同一 session_id
    ├─ recording session
    └─ recipient Waiting
    ↓
RecordingPlan → RecorderSupervisor
    ↓
segment CSV → media_files
    ↓
stop / natural exit / failure / shutdown
    ↓
幂等关闭 recording 与 recipient session
```

## 2. 不可改变语义

1. 当前推荐收礼人只由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. 首条有效事件前为 Waiting；空 recipient、IM 断线和重连后尚无新有效事件时为 Unknown。
3. recipient 变化不得重启 FFmpeg，也不得切断媒体连接。
4. 所有抖音 64 位 ID 在 Python、SQLite、JSON 和 JavaScript 中保持字符串。
5. 完整签名流 URL 只允许存在于当前进程内存、`StreamInput`、`RecordingPlan` 和 FFmpeg argv。
6. `live_verified=false`，Issue #1 保持 Open。
7. 不引入 Redis、Celery、PostgreSQL、多机器或公网管理。

## 3. 里程碑

### 3.1 Schema v5

通过递增 migration 增加：

- sessions 的录制协议、画质、输入 host/hash、退出码、停止阶段与最后 progress 审计字段；
- media_files 的 CSV 起止秒、容器、文件后缀与校验状态字段；
- recording session 与 media segment 查询索引；
- 旧 active session 的启动恢复策略：应用启动时标记 interrupted，并关闭开放 recipient interval。

数据库不得保存完整流 URL、完整 path、query value、Cookie 或 Authorization。

### 3.2 RecordingSessionRepository

实现：

- 创建 active recording session；
- 单房间只允许一个 active session；
- 写入脱敏输入指纹与启动审计；
- 增量同步 `segments.csv` 到 `media_files`；
- explicit stop、natural exit、nonzero exit、startup failure 与 shutdown 的幂等结束；
- 只读 state/sessions/segments。

### 3.3 SingleRoomRecordingService

- 使用 `RoomService` 读取房间配置；
- 从 `DouyinStreamResolver` 选择进程内候选；缓存为空时先执行一次立即解析；
- 构造 `StreamInput` 与 `RecordingPlan`；
- 启动 `RecorderSupervisor`，注册 progress/stderr callback；
- 以同一个 session_id 调用 `RecipientSessionService.start_session()`；
- 后台等待自然退出并完成统一收尾；
- stop 与 app shutdown 幂等；
- recipient 状态变化不得触碰 supervisor 生命周期。

### 3.4 API 与网页

```text
POST /api/rooms/{room_key}/actions/start-recording
POST /api/rooms/{room_key}/actions/stop-recording
GET  /api/rooms/{room_key}/recording
GET  /api/rooms/{room_key}/recording/sessions
GET  /api/rooms/{room_key}/recording/segments
```

响应只包含 session、状态、脱敏输入元数据、progress、分片和错误码，不包含完整 URL 或签名值。

### 3.5 测试与 CI

- schema v1-v4 → v5 migration；
- start/duplicate start/stop/idempotent stop；
- cached candidate 与 resolve fallback；
- startup failure、natural exit、nonzero exit、shutdown；
- recipient Waiting 与 recording 共用 session_id；
- segment CSV 增量同步、未完成尾行、既有文件和 `.writing`；
- API privacy、非法房间、无候选和 FFmpeg 未就绪；
- 本地 lavfi session smoke；
- Python 3.12/3.13、Ruff、pytest、Windows、前端和恢复资产。

## 4. 明确不做

- 多房间 RoomManager 与长期轮询；
- 自动开播检测与无人值守启停；
- postprocess/export jobs；
- 真实 recipient contract 更新；
- 保存授权直播间真实媒体到 GitHub 或 Actions。

## 5. 提交顺序

1. `docs: define P1D recording session implementation plan`
2. `feat: add recording session schema v5`
3. `feat: persist recording sessions and media segments`
4. `feat: run single-room recording lifecycle`
5. `feat: expose recording session APIs`
6. `test: cover recording lifecycle and privacy boundaries`
7. `docs: finalize P1D review and limitations`

每个里程碑完成后立即 push，禁止 force push。