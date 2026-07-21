# P2A 实施计划：多房间 RoomManager 与自动录制编排

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #13、协议事实 Issue #1  
分支：`feature/p2a-multi-room-manager`  
基线：`main@dd6a8b39659ab5d891cd03db1e31c727dec5a7ba`

## 1. 目标

P2A 把 P1D 的显式单房间录制 Session 扩展为单进程、多房间长期编排，但仍保持 FastAPI + SQLite + asyncio：

```text
RoomManager
    ├─ RoomWorker(room-a) → check → live/offline/unknown → recording
    ├─ RoomWorker(room-b) → check → live/offline/unknown → recording
    └─ RoomWorker(room-c) → check → live/offline/unknown → recording
```

每个房间串行工作；全局检查有界并发；单房间失败不影响其他房间。

## 2. 不可改变语义

1. recipient 只由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. recipient 变化不得重启 FFmpeg，也不得切断媒体连接。
3. `unknown`、`blocked` 和 `error` 不得当作 `offline`。
4. 完整签名流 URL 只存在于 resolver 内存、RecordingPlan 和 FFmpeg argv。
5. 所有平台 64 位 ID 作为字符串。
6. `live_verified=false`，Issue #1 保持 Open。
7. 不增加 Redis、Celery、PostgreSQL、多机器或公网管理。

## 3. 里程碑

### 3.1 RoomManager 与 Worker 模型

- 应用内唯一 `RoomManager`；
- enabled room 对应一个 worker task；
- 每个 worker 内严格串行 check/start/stop；
- 全局 semaphore 限制并发直播页检查；
- create/enable/update/disable 后触发 reconcile；
- worker 停止时不遗留 task；
- 应用关闭先停止 manager，再关闭 recording service。

### 3.2 状态转换

- `live`：连续一次有效 live 即 `ensure_recording`；已有 active recording 时不重复启动；
- `offline`：连续达到配置阈值后停止 recording；
- `unknown`/`blocked`/`error`：保持已有 recording，不停止媒体连接；
- 检查异常：记录脱敏错误类型并指数退避；
- 成功检查：重置错误退避；
- disable/delete：停止 worker 和活动 recording；
- room URL/quality/protocol 改变：安全停止旧 recording，由下一次 live 检查启动新 Session。

### 3.3 退避与配置

复用现有 room `poll_interval_seconds`，增加应用配置：

- `poll.jitter_seconds`；
- `poll.offline_confirmations`；
- `poll.max_parallel_checks`；
- 错误退避倍数和最大秒数使用安全固定上限。

测试通过注入 clock、sleep 和 random 函数实现确定性，不在生产使用忙轮询。

### 3.4 API 与网页

```text
GET  /api/manager/status
POST /api/manager/actions/reconcile
GET  /api/rooms/{room_key}/worker
```

公开状态包含 worker lifecycle、最后 live state、连续 offline、错误次数、下次检查时间和 recording 状态；不返回 URL、Cookie、签名值或异常正文。

网页显示 manager 状态、每房间 worker 状态和手动 reconcile 按钮；浏览器写请求继续受同源边界保护。

### 3.5 测试与 CI

- 两个以上房间并发且单房间串行；
- 全局 semaphore 上限；
- live 启动一次、不重复启动；
- offline 阈值后停止；
- unknown/blocked/error 不停止；
- 异常退避和成功重置；
- disable/update/delete 的 task/recording 清理；
- 单 worker 失败不影响其他 worker；
- app startup/shutdown 顺序；
- API privacy、Host/Origin 边界；
- Python 3.12/3.13、Ruff、pytest、Windows、前端、双 FFmpeg smoke 和恢复资产。

## 4. 明确不做

- postprocess/export jobs；
- 多机器协调；
- 管理员认证和公网管理；
- 自动修改真实 recipient contract；
- 保存真实直播媒体到 GitHub。

## 5. 提交顺序

1. `docs: define P2A multi-room manager plan`
2. `feat: add RoomManager worker lifecycle`
3. `feat: reconcile room changes with automatic recording`
4. `feat: expose manager and worker APIs`
5. `feat: add multi-room status controls to web UI`
6. `test: cover multi-room isolation and backoff`
7. `docs: finalize P2A review and limitations`

每个里程碑完成后立即 push，禁止 force push。