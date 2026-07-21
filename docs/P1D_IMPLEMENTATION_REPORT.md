# P1D 实施报告：单房间录制 Session 与 recipient 生命周期闭环

状态：**工程实现完成，等待远端 PR CI 与 Review；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #11、协议事实 Issue #1  
分支：`feature/p1d-single-room-recording-session`

## 已实现

- SQLite schema v5：录制协议/画质、输入 host/hash、FFmpeg 结果、progress 和 segment 审计字段；
- recording 与 recipient 使用同一个 `sessions.id`，启动时创建 Waiting interval；
- 单房间唯一 active session，由 SQLite active-session 约束和进程内 room lock 双重保护；
- resolver 私密候选经 `StreamInput`、`RecordingPlan` 直接进入 `RecorderSupervisor`；
- 完整流 URL、完整 path 和 query value 不进入 API、SQLite、日志、网页或测试快照；
- explicit stop、natural exit、nonzero exit、prepare/start failure、app shutdown 和 restart recovery；
- `segments.csv` 增量同步，只接受受控 `00000.mkv/ts`、已闭合行和非符号链接普通文件；
- recording state/sessions/segments API；
- 同源网页开始/停止录制控制；
- 本地 `recording_session_smoke.py` 使用 lavfi 验证 Session、recipient interval 和媒体分片闭环。

## API

```text
POST /api/rooms/{room_key}/actions/start-recording
POST /api/rooms/{room_key}/actions/stop-recording
GET  /api/rooms/{room_key}/recording
GET  /api/rooms/{room_key}/recording/sessions
GET  /api/rooms/{room_key}/recording/segments
```

## 状态语义

- explicit stop：`ended / explicit_stop`；
- room disable/config change：先停止录制，再变更房间；
- natural exit：`ended / natural_exit`；
- nonzero exit：`failed / ffmpeg_exit_nonzero`；
- prepare/start failure：`failed / recorder_start_failed`；
- app shutdown：`interrupted / app_shutdown`；
- restart recovery：`interrupted / app_restart_recovery`；
- 所有路径均幂等关闭 recipient 开放 interval。

## 安全 Review

- `RecordingPlan` 再次校验协议、默认端口、凭据、IP、本机、`.local` 和 CDN allowlist；
- 输出根、session/media 目录和 segment 文件拒绝符号链接与越界；
- 已存在 MKV/TS/`.writing`/segment CSV 时拒绝覆盖；
- progress 只保存受控字段，不保存 `raw`；
- API 与 SQLite 只保存 host、path/url SHA-256 和 query key 名称；
- recipient 状态变化不调用 recording start/stop。

## 本地验收

最终 PR 前执行锁定依赖、repository baseline、source boundary、compileall、Ruff、pytest、reducer/database replay、JavaScript syntax、FFmpeg Supervisor smoke 和 P1D recording Session smoke。最终远端 SHA、run ID 和测试计数以 PR 对应 GitHub Actions 为准。

## 仍未解决

- 真实目标 IM method 与字段事实；
- 多房间长期 RoomManager；
- 自动开播检测与无人值守运行；
- postprocess/export jobs；
- Windows 正式便携发布包。

因此 contract 保持 `live_verified=false`，Issue #1 继续开启。
