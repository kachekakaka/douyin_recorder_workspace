# P1A 实施计划：单房间媒体基础

状态：**已开始实施**  
关联：GitHub Issue #3  
分支：`feature/p1a-single-room-media-foundation`

## 为什么拆出 P1A

P0 的可恢复工程骨架、严格合成回放和现场预检工具已经合并到 `main`，但授权账号的无登录 headless 预检没有观察到 IM WebSocket，`WebcastGroupLiveGiftRecipientRecommendMessage` 的真实空值、重连、重复与字段事实仍未验收。

媒体连续录制、房间配置、公开直播页解析和 FFmpeg 进程监督并不需要伪造这些协议结论，因此 P1 拆为：

- **P1A 单房间媒体基础**：房间 CRUD、直播页/流候选解析、FFmpeg Supervisor、可测试进程与文件边界；
- **P1B recipient 接线与单房间闭环**：真实 WSS、目标消息入库、Waiting/Active/Unknown 与媒体场次联动。

P1A 可以推进，但不能把它描述为完整 P1 已完成，也不能修改运行 contract 的 `live_verified=false`。

## P1A 交付范围

1. `GET/POST/PATCH /api/rooms` 和 enable/disable/check actions；
2. 抖音号与 `live.douyin.com` URL 规范化，拒绝非 HTTPS、凭据、自定义端口、多段路径和非抖音主机；
3. 手动跟随受限抖音主机重定向，避免 SSRF；
4. 从公开页面的 JSON/字符串化 JSON 中提取 room/web_rid/title 与 FLV/HLS 画质候选；
5. 完整签名流 URL 仅存在于当前进程内存，API/SQLite/日志只保存 host、path、query key、URL hash；
6. FFmpeg argv 构造、敏感字段脱敏、progress 解析、segment CSV 解析；
7. `RecorderSupervisor` 同时消费 stdout/stderr，独立进程组，优雅停止→terminate→kill；
8. 本地 `lavfi` smoke 工具和自动测试；
9. SQLite schema v2：`room_checks` 审计表；
10. 静态网页的直播间新增、启停和立即检查入口。

## 本批次仍不做

- 自动长期轮询和多个 RoomWorker；
- 从真实候选自动启动无限时长录制；
- 真实 recipient 事件入库和 session 联动；
- 切片、播放代理、任务队列；
- 认证、局域网或公网监听；
- 任何身份推断 fallback。

## 安全不变量

- 页面检查的入口只能是抖音号或 `https://live.douyin.com/<id>`；
- 页面重定向不能离开允许的抖音页面主机；
- 流候选只接受 HTTP(S)、无用户信息、无私网/本地主机，并限制在已知抖音/字节 CDN 后缀；
- API、SQLite、错误和 FFmpeg 日志不得包含完整 query、Cookie、签名值或 stream URL；
- 真实媒体和运行状态只写入 `userdata/`、`records/`，继续被 Git 忽略。

## 验收

- schema v2 可从空库和 v1 库幂等迁移；
- 房间 CRUD/check API 集成测试通过；
- 合成直播页 fixture 稳定解析 3 个候选，公开结果中不存在 fixture 的 secret；
- 假进程证明 stdout/stderr 同时消费和 SIGINT 停止；
- 本机安装 FFmpeg 时，`python tools/ffmpeg_supervisor_smoke.py` 能生成可探测的 MKV 分片；
- Python 3.12/3.13、Ruff、pytest、replay、JS、Windows verify 与恢复资产 CI 全绿。
