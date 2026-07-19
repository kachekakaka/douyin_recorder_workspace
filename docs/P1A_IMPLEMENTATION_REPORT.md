# P1A 实施报告：单房间媒体基础

状态：**工程实现完成，等待最终远端 CI 与 PR Review；不宣称完整 P1 已完成。**  
关联：GitHub Issue #3、Draft PR #4  
代码里程碑：`9c5df3537ce2115b2a148099ef292115de400fe2`

## 1. 已交付范围

- 房间 API：列表、创建、单房间读取、PATCH、立即检查、enable、disable；
- 抖音号和 `https://live.douyin.com/<id>` 规范化、唯一性和严格 SSRF/重定向边界；
- `DouyinStreamResolver`：静态页面优先，缺少候选时执行一次性 Chrome/CDP 网络回退；
- 最多 32 个房间、120 秒 TTL 的进程内私密候选缓存；URL 修改、disable、resolver 关闭或过期时清除；
- 合成 HTML/JSON/字符串化 JSON 的 FLV、HLS 和画质候选解析；
- FFmpeg argv、脱敏、progress、segment CSV 和 `RecorderSupervisor` 生命周期；
- SQLite 递增 migration、房间检查审计、规范化 URL 唯一索引；
- 基础同源网页、单元/集成测试、Windows 和恢复资产 CI。

## 2. API

```text
GET    /api/rooms
POST   /api/rooms
GET    /api/rooms/{room_key}
PATCH  /api/rooms/{room_key}
POST   /api/rooms/{room_key}/actions/check
POST   /api/rooms/{room_key}/actions/enable
POST   /api/rooms/{room_key}/actions/disable
```

`room_key`、PATCH、重复规范化 URL、显式 `null`、非法枚举、Host、Origin/Referer 和 CLI 无 Origin 规则均有集成测试。enable/disable 不删除历史检查；disable 会清除当前进程内私密候选。

## 3. SQLite

最终 schema version：**3**。

- v1：基础应用表；
- v2：`room_checks` 审计；
- v3：规范化 `room_url` 唯一索引和 `room_checks` 查询索引；
- migration 使用 checksum；已有重复规范化 URL 时明确失败；
- 抖音 64 位 ID 按字符串返回和保存；
- `detail_json` 拒绝完整 HTTP/WSS URL、Cookie、Authorization、token、signature 等秘密承载内容。

## 4. resolver 与隐私边界

```text
受限公开直播页请求
    ↓
HTML / JSON / 字符串化 JSON 解析
    ↓ 无候选
一次性 Chrome/CDP 网络观察
    ↓
进程内 StreamCandidate / TTL cache
    ↓
API 与 SQLite 只接收脱敏快照
```

浏览器回退具有并发锁、有界超时、独立临时 profile、独立进程组和可靠停止；最终页面必须仍是同一个规范化房间。候选只接受真实 2xx 媒体响应、HTTP(S) 默认端口和抖音/字节 allowlist CDN。

完整流 URL、完整 path、query value、Cookie、Authorization 和响应正文不得进入 API、SQLite、日志、网页、对象 repr、测试快照、Issue、PR 或 GitHub Actions artifact。公开候选仅包含协议、受控画质、host、媒体后缀、query key、path/url SHA-256 和受控 source 描述。

## 5. FFmpeg Review

- `asyncio.create_subprocess_exec(*argv)`，禁止 shell；
- 正式计划使用 `-n`，不使用 `-y`；
- stdout/stderr 始终并发消费；
- callback 异常隔离并计数，不阻塞管道；
- Cookie、Authorization、完整输入 URL 和签名值脱敏；
- `-headers` 与 `extra_input_args` 使用显式 allowlist；
- FFmpeg 输入再次校验 CDN、协议、默认端口、凭据、IP、本机和 `.local`；
- Unix/Windows 独立进程组；停止顺序 graceful → terminate → kill；
- `.writing` 和已有媒体/segment CSV 不会被覆盖或当作完成文件；
- segment CSV 容忍空行、正在写入和不完整末行。

## 6. 验证

本地完整验证：

```text
pip check：通过
repository baseline：通过
source boundary：通过
compileall：通过
Ruff：通过
pytest：45 passed
deterministic recipient replay：通过
JavaScript syntax：通过
FFmpeg lavfi smoke：通过
```

FFmpeg smoke 结果：自然退出、2 个非空 MKV、2 行 segment CSV、没有残留 `.writing` 文件或 callback 错误。真实授权房间只做公开、无登录、被动、脱敏候选发现，不保存媒体或原始帧。

最终远端 CI 和三房间脱敏预检 run ID 在 PR #4 描述中记录，并以承载本报告的最终分支 head 为准。

## 7. 真实现场事实与限制

授权房间 `79907888978`、`94771623313`、`40727638291` 在此前脱敏现场回归中均成功访问公开页面、请求 `webcast/room/web/enter`，并观察到 allowlist `douyincdn.com` FLV 媒体响应。这证明真实候选会在页面运行后的网络请求中出现。

仍未完成的事实：

- `web-enter` 响应正文在各 Chrome 环境中的稳定捕获；
- 三个真实房间的完整 `origin/uhd/hd/sd/ld/md` 与 FLV/HLS 矩阵；
- 签名 URL 离开浏览器上下文后的有效期和地区差异；
- 普通交互浏览器实际建立的 IM WSS；
- `WebcastGroupLiveGiftRecipientRecommendMessage` 的真实字段、空值、重复、切换和重连行为；
- 经去标识、人工审查、可回放的目标消息 fixture。

因此 `app/douyin/contracts/provisional_v1.json` 继续保持 `live_verified=false`，Issue #1 保持开启。

## 8. 不在 P1A 范围

- 多房间 RoomManager 和长期自动轮询；
- 从真实候选自动启动无限时长录制；
- 完整 Session/recipient 入库闭环；
- 真实 IM 自动接入；
- 直播后切片、播放代理和任务队列；
- 管理员认证、局域网或公网监听；
- OCR、人脸、声纹、礼物、弹幕、昵称、成员或画面位置 fallback。
