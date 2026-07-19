# P1A 实施报告：单房间媒体基础

状态：**工程实现与自动验收已完成，等待 PR 审查；真实抖音流与 recipient 协议仍需现场验证。**
关联：GitHub Issue #3  
分支：`feature/p1a-single-room-media-foundation`

## 为什么拆出 P1A

P0 的可恢复工程骨架、严格合成回放和现场预检工具已经合并到 `main`，但授权账号的无登录 headless 预检没有观察到 IM WebSocket，`WebcastGroupLiveGiftRecipientRecommendMessage` 的真实空值、重连、重复与字段事实仍未验收。

媒体连续录制、房间配置、公开直播页解析和 FFmpeg 进程监督并不需要伪造这些协议结论，因此 P1 拆为：

- **P1A 单房间媒体基础**：房间 CRUD、直播页/流候选解析、FFmpeg Supervisor、可测试进程与文件边界；
- **P1B recipient 接线与单房间闭环**：真实 WSS、目标消息入库、Waiting/Active/Unknown 与媒体场次联动。

P1A 已经推进完成，但不能把它描述为完整 P1 已完成，也不能修改运行 contract 的 `live_verified=false`。

## 已交付

1. `GET/POST/PATCH /api/rooms` 和 enable/disable/check actions；
2. 抖音号与 `live.douyin.com` URL 规范化，拒绝非 HTTPS、凭据、自定义端口、多段路径和非抖音主机；
3. 手动跟随受限抖音主机重定向，并拒绝重定向凭据、自定义端口和越界主机；
4. 从公开页面的 JSON/字符串化 JSON 中提取 room/web_rid/title 与 FLV/HLS 画质候选；
5. 完整签名流 URL 仅存在于当前进程内存；API/SQLite/日志只保存 host、媒体后缀、path/url hash、query key 和脱敏 source path；
6. FFmpeg argv 构造、敏感字段脱敏、progress 解析、segment CSV 解析；
7. `RecorderSupervisor` 同时消费 stdout/stderr，使用独立进程组，执行优雅停止→terminate→kill；观察回调异常不会中断管道消费；
8. FFmpeg 输入层再次校验抖音/字节 CDN allowlist、协议默认端口、安全路径段、危险请求头和参数白名单，并用 `-n` 拒绝覆盖已有媒体；
9. 本地 `lavfi` smoke 工具和自动测试；
10. SQLite schema v2：`room_checks` 审计表；同一规范化房间 URL 不允许新增第二个 `room_key`；
11. 静态网页的直播间新增、启停和立即检查入口；
12. 回环 `Host` 校验和浏览器同源写操作校验，降低 DNS rebinding/CSRF 风险。

## 本批次仍不做

- 自动长期轮询和多个 RoomWorker；
- 从真实候选自动启动无限时长录制；
- 真实 recipient 事件入库和 session 联动；
- 切片、播放代理、任务队列；
- 认证、局域网或公网监听；
- 任何身份推断 fallback。

## 安全不变量

- 页面检查的入口只能是抖音号或 `https://live.douyin.com/<id>`；
- 页面重定向不能离开允许的抖音页面主机，也不能携带凭据或自定义端口；
- 流候选只接受 HTTP(S) 协议默认端口、无用户信息、无 IP 字面量/本地主机，并限制在已知抖音/字节 CDN 后缀；FFmpeg 计划独立重复该检查；
- API、SQLite、错误和 FFmpeg 日志不得包含完整 query、Cookie、签名值、原始 stream path 或 stream URL；
- 未认证阶段只接受回环 Host；浏览器写请求必须同源，命令行客户端可不带 Origin；
- `-n` 防止同一目录中的已有媒体被静默覆盖；
- 真实媒体和运行状态只写入 `userdata/`、`records/`，继续被 Git 忽略。

## 自动验收

- schema v2 可从空库和 v1 库幂等迁移；
- 房间 CRUD/check API、重复 URL、显式 null、Host 与 Origin 边界均有集成测试；
- 合成直播页 fixture 稳定解析 3 个候选；畸形端口、空 userinfo、越界候选和未信任 source key 不进入公开结果；
- 非 2xx 页面保持 error，不把网络/风控失败猜成 offline；
- 假进程证明 stdout/stderr 同时消费、Windows `SIGBREAK`/Unix `SIGINT` 停止及回调异常隔离；
- 本机/CI 安装 FFmpeg 时，`python tools/ffmpeg_supervisor_smoke.py` 生成 MKV 分片并解析 segment CSV；
- Python 3.12/3.13、Ruff、pytest、确定性 recipient replay、JavaScript、Windows `verify.bat`、FFmpeg smoke 与恢复资产 CI 全部通过。

## 进入 P1B 前仍需的现场事实

- 普通交互浏览器实际建立的当前 WSS；
- 目标 method 在成员切换、空对象和重连时的真实行为；
- 当前字段号、`msg_id` 重复、`change_reason_enum` 和延迟分布；
- 至少一条经去标识、人工审查、可回放的现场 fixture。

以上事实完成前，`app/douyin/contracts/provisional_v1.json` 必须继续保持 `live_verified=false`，Issue #1 继续开放。
