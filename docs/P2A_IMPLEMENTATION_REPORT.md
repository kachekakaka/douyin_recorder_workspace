# P2A 实施报告：多房间 RoomManager 与自动录制编排

状态：**工程实现完成，等待远端 PR exact-head CI 与 Review；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #13、协议事实 Issue #1  
分支：`feature/p2a-multi-room-manager`

## 已实现

- 应用内唯一 `RoomManager`；production 默认模板启用，最小配置缺省禁用；
- 每个 enabled room 一个严格串行 worker；
- 全局 semaphore 限制并发直播页检查；
- live 时确保录制，同一房间不会重复 start；
- 连续 offline 达阈值后停止录制；
- unknown、blocked、error 或检查异常保持已有 recording，并按有界指数退避重试；
- create/enable/update/disable 动态 reconcile；URL/quality/protocol 更新安全停止旧 Session；
- disable 停止 worker 和活动 recording；
- 单 worker 异常仅记录脱敏 error code，不影响其他 worker；
- 应用启动时在 recording recovery/tool readiness 后启动 manager；关闭时先停止 manager，再关闭 recorder；
- manager status、manual reconcile 和 room worker API；
- 同源网页显示 manager/worker 状态。

## API

```text
GET  /api/manager/status
POST /api/manager/actions/reconcile
GET  /api/rooms/{room_key}/worker
```

API 不返回检查异常正文、完整直播 URL、Cookie、签名值、流 path 或 raw payload。

## 测试

- live 启动一次、重复 live 不重复启动；
- offline confirmation 阈值；
- unknown/blocked/error 保持媒体；
- 异常 code 脱敏与退避；
- 三房间并发检查受 semaphore 限制；
- 单房失败不影响其他房间；
- enable/disable reconcile 与 recording 清理；
- FastAPI 启动、创建 enabled room 自动录制、disable 自动停止；
- manager API、Host/Origin 和无 manager 配置的稳定状态。

## 安全结论

P2A 不改变 recipient 来源，不因 recipient 切换触碰 FFmpeg，不把 unknown 当作 offline，不保存完整流 URL，也没有增加 Redis、Celery、PostgreSQL、多机器或公网管理。

## 仍未解决

- 真实 recipient method/字段、空值、重复、切换与重连证据；
- postprocess/export jobs；
- 管理员认证与公网管理；
- Windows 正式 release package。

因此 provisional contract 继续保持 `live_verified=false`，Issue #1 保持 Open。
