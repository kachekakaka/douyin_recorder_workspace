# P1A 实施计划：单房间媒体基础

状态：**P1A 工程实现已完成，正在等待最终远端 CI 与 PR Review；不宣称完整 P1 已完成。**
关联：GitHub Issue #3  
分支：`feature/p1a-single-room-media-foundation`

## 为什么拆出 P1A

P0 的可恢复工程骨架、严格合成回放和现场预检工具已经合并到 `main`，但授权账号的无登录 headless 预检没有观察到 IM WebSocket，`WebcastGroupLiveGiftRecipientRecommendMessage` 的真实空值、重连、重复与字段事实仍未验收。

媒体连续录制、房间配置、公开直播页解析和 FFmpeg 进程监督并不需要伪造这些协议结论，因此 P1 拆为：

- **P1A 单房间媒体基础**：房间 CRUD、直播页/流候选解析、FFmpeg Supervisor、可测试进程与文件边界；
- **P1B recipient 接线与单房间闭环**：真实 WSS、目标消息入库、Waiting/Active/Unknown 与媒体场次联动。

P1A 已完成当前计划中的工程实现，但不能把它描述为完整 P1 已完成，也不能修改运行 contract 的 `live_verified=false`。

## 真实流候选 resolver 实施结果（2026-07-19）

三个经用户授权且在测试时确认正在直播的房间，均在脱敏 Chrome/CDP 观察中实际请求了
`live.douyin.com/webcast/room/web/enter`，并各自发起了一个 allowlist `douyincdn.com`
FLV 媒体请求。对应远端验证为 CI `29684016011` 和脱敏预检 `29684015993`。
这说明主要缺口不是“房间不可访问”，而是初始 HTML 解析发生得太早，真实候选在页面运行后的
结构化接口或媒体网络请求中出现。

P1A 已按以下最小可靠链路实现，不把浏览器扩展为长期 RoomWorker：

```text
受限公开直播页请求
    ↓
静态 HTML / JSON / 字符串化 JSON 解析
    ↓ 无候选
一次性、受控 Chrome/CDP 网络观察
    ↓
进程内 StreamCandidate
    ↓
房间立即检查返回脱敏候选元数据
```

实施约束：

1. 已新增应用内 `DouyinStreamResolver`，先复用静态解析；只有一次性检查没有候选时才使用浏览器网络回退。
2. Chrome 回退只允许访问规范化房间 URL，只读取固定 allowlist 房间接口，并只接受抖音/字节 CDN 媒体 URL。
3. 完整签名 URL、请求 query value、Cookie 和结构化响应正文只可短暂存在于当前进程内存；API、SQLite、日志、网页和 CI artifact 只保留 host、媒体后缀、query key、path/url SHA-256 和受控 source 描述。
4. `POST /api/rooms/{room_key}/actions/check` 已接入 resolver；完整候选放入最多 32 个房间、120 秒 TTL 的进程内缓存，URL 修改、disable、关闭、过期或进程退出时丢失，不写入数据库。
5. 当前浏览器回退从真实 2xx 媒体响应提取候选；`web-enter` JSON 的稳定响应体捕获、完整 FLV/HLS 画质矩阵和直接结构化接口客户端仍保留为后续现场增强，不在 P1A 中复制签名算法。
6. 本批次不直接复制 biliup 的固定 `ttwid`、A-Bogus、验证码或风控处理。只有结构化接口被证明必须直接请求时，才单独评审匿名访客上下文、许可证和签名来源。
7. 真实房间只做公开、无登录、被动、脱敏的候选发现和短时连接验证；实际媒体落盘仍使用本地 `lavfi` smoke。
8. resolver 的浏览器进程必须具备有界观察时间、并发限制和可靠停止，不能演变为长期自动轮询或多房间 RoomManager。

分阶段结果：

- resolver 设计与隐私边界已文档化；
- 进程内 resolver 和有界候选缓存已实现；
- 房间立即检查、disable/URL 修改清理和 API/SQLite 泄漏回归测试已完成；
- 三个授权房间的脱敏现场回归已观察到实际 FLV 媒体响应；
- 最终 CI 全绿后更新 P1A 报告和 PR 描述，并改为 Ready for review。

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
10. SQLite schema v3：`room_checks` 审计表；同一规范化房间 URL 不允许新增第二个 `room_key`；
11. 静态网页的直播间新增、启停和立即检查入口；
12. 回环 `Host` 校验和浏览器同源写操作校验，降低 DNS rebinding/CSRF 风险［
13. 应用内 resolver 的一次性 Chrome/CDP 回退、进程组清理、2xx 媒体响应校验和 TTL 私密候选缓存。

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

- schema v3 可从空库、v1 和 v2 库按校验和递增迁移；
- 房间 CRUD/check API、重复 URL、显式 null、Host 与 Origin 边界均有集成测试；
- 合成直播页 fixture 稳定解析 3 个候选；畸形端口、空 userinfo、越界候选和未信任 source key 不进入公开结果［
- 非 2xx 页面保持 error，不把网络/风控失败猜成 offline；
- 假进程证明 stdout/stderr 同时消费、Windows `SIGBREAK`/Unix `SIGINT` 停止及回调异常隔离；
- 本机/CI 安装 FFmpeg 时，`python tools/ffmpeg_supervisor_smoke.py` 生成 MKV 分片并解析 segment CSV；
- Python 3.12/3.13、Ruff、pytest、确定性 recipient replay、JavaScript、Windows `verify.bat`、FFmpeg smoke 与恢复资产 CI 全部通过。

## 进入 P1B 前仍需的现场事实

- 普通交互浏览器实际建立的当前 WSS；
- 目标 method 在成员切换、空对象和重连时的真实行为［
- 当前字段号、`msg_id` 重复、`change_reason_enum` 和延迟分布［
- 至少一条经去标识、人工审查、可回放的现场 fixture。

以上事实完成前，`app/douyin/contracts/provisional_v1.json` 必须继续保持 `live_verified=false`，Issue #1 继续开放。
