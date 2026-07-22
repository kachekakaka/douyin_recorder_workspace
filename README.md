# douyin_recorder_workspace

抖音团播多直播间录播与“当前推荐收礼人”时间线系统。

> **v0.1.0 已正式发布。** annotated tag `v0.1.0` 解引用到 `b8056289ea9c18504675ff1dd43df84a977b2436`，Release ID 为 `357811419`。Windows/source 发布资产、SHA-256、manifest 和恢复验证均已通过。真实目标 recipient 协议仍未完成现场验证。

最终实施和发布证据见：

```text
docs/FINAL_IMPLEMENTATION_REPORT.md
docs/FINAL_IMPLEMENTATION_REPORT.json
```

## 不可改变的业务口径

系统只使用：

```text
WebcastGroupLiveGiftRecipientRecommendMessage
```

更新当前推荐收礼人。首条有效事件前为 `Waiting`；空 recipient、IM 断线及重连后尚未收到新有效事件时均为 `Unknown`。不使用 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称、标题或画面位置补全；推荐对象变化不得重启 FFmpeg 或切断媒体连接。

运行 contract 继续保持：

```text
live_verified=false
```

授权房间的无登录 headless 预检曾成功访问公开页面，并在浏览器网络层观察到 allowlist `douyincdn.com` FLV 媒体响应；仍没有形成普通交互浏览器 IM WSS、目标 method、字段号、空值、重复、切换和重连的去标识人工审查 fixture。Issue #1 必须继续保持 Open。

## v0.1.0 发布身份

```text
release source main: b8056289ea9c18504675ff1dd43df84a977b2436
tag object:          567d8d9dc6559226c76625cd4cc0df040b1b903c
tag commit:          b8056289ea9c18504675ff1dd43df84a977b2436
main CI run:         29896339087
release run:         29896406871
Release ID:          357811419
published at:        2026-07-22T06:21:41Z
draft/prerelease:    false / false
```

发布后还在新的 Ubuntu runner 临时目录完成独立下载复核：

```text
Final Release Asset Audit run: 29897165335  success
```

## 已实现

### P0：可恢复工程骨架

- Python 3.12/3.13、FastAPI、单 Uvicorn worker；
- 同源静态 HTML/CSS/原生 JavaScript ES Modules；
- SQLite WAL、校验和 migration、单写连接与 backup API；
- `/healthz`、`/readyz`、`/api/status`；
- FFmpeg/ffprobe readiness；
- 有界 Protobuf wire inspector、PushFrame/Response/Message envelope；
- 严格 Waiting/Active/Unknown reducer 和确定性 synthetic replay；
- WSS probe、HTTP/Chrome 脱敏现场预检；
- Windows `start.bat`、`update.bat`、`verify.bat`、`backup.bat`；
- GitHub Actions 与 Git Bundle/source ZIP 恢复验证。

### P1A：单房间媒体基础

- 房间 CRUD、enable/disable/check 和单房间读取 API；
- 抖音号与 `https://live.douyin.com/<id>` 严格规范化；
- 受限重定向、SSRF、Host 和浏览器同源写操作边界；
- 公开直播页 JSON/字符串化 JSON 解析；
- `DouyinStreamResolver`：静态页面优先，缺少候选时执行一次性 Chrome/CDP 网络回退；
- 完整签名流 URL 仅存在于有界、带 TTL 的进程内缓存；
- API、SQLite、日志只返回脱敏流候选元数据；
- FFmpeg argv、输入二次校验、progress、segment CSV 和 `RecorderSupervisor`；
- SQLite schema v3 的 `room_checks`、规范化 URL 唯一索引和查询索引。

### P1B：recipient 事务投影基础

- SQLite schema v4 recipient/session 审计字段和查询索引；
- session 开始时创建 `Waiting(waiting_first_event)`；
- canonical event、dedup、duplicate/late 审计和 interval 转换在同一 SQLite 写事务中完成；
- 空 recipient → `Unknown(empty_recipient)`；
- IM 断线 → `Unknown(im_disconnected)`；重连本身不会恢复断线前 recipient；
- 相同 recipient 不重复开启区间；切换 recipient 关闭旧区间并开启新 Active；
- 所有 64 位 ID 在 Python、SQLite 和 JSON 中保持字符串；
- recipient state/events/intervals API 不返回 raw payload、extra 或未知字段内容。

这些语义目前只由 synthetic fixture、reducer replay 和 SQLite replay 验证，不代表真实抖音协议已验证。

### P1C：交互式 IM 证据工具链

- 只附加到用户主动启动的回环 Chrome DevTools；
- 精确匹配一个授权 `live.douyin.com/<id>` page target；
- 被动观察 allowlist WSS 与 binary frame；
- raw frame、target payload 和 manifest 只写 Git 忽略的私人目录；
- public report 不包含 Cookie、完整 WSS、query value、raw payload 或 recipient 明文；
- approval/hash/contract 全匹配后才导出去标识 candidate fixture；
- fixture 强制保持 `live_verified=false`。

现场命令和人工审批流程见：

```text
docs/protocol/P1C_INTERACTIVE_EVIDENCE_RUNBOOK.md
```

### P1D：单房间录制 Session 闭环

- SQLite schema v5 记录脱敏输入指纹、FFmpeg 结果、progress 与媒体分片；
- 显式 start/stop，同一房间最多一个 active recording；
- 完整签名 URL 只在 resolver、`StreamInput`、`RecordingPlan` 和 FFmpeg argv 内存链路中存在；
- recording 与 recipient Waiting/Active/Unknown 使用同一个 session ID；
- explicit stop、natural exit、nonzero exit、startup failure、app shutdown 和 restart recovery 均有明确结束状态；
- `segments.csv` 只同步已闭合且位于受控目录的 MKV/TS 文件；
- recipient 状态变化不会重启媒体连接。

### P2A：多房间自动录制编排

- 单进程唯一 `RoomManager`，每个 enabled room 一个严格串行 worker；
- 全局 semaphore 限制并发直播检查，单房间故障不会阻塞其他房间；
- `live` 自动确保录制，同一房间不会重复启动 Session；
- 连续 `offline` 达配置阈值后停止录制；
- `unknown`、`blocked` 和 `error` 保持已有媒体连接并指数退避；
- create/enable/update/disable 后动态 reconcile；
- 网页显示 manager/worker 状态并提供同源手动 reconcile。

### P3A：持久化后处理与区间导出

- SQLite schema v6 保存 postprocess job、attempt 与 output；
- 已结束 recording session + media files + recipient intervals 生成确定性 `ExportPlan`；
- Waiting、Active、Unknown 均可导出；
- 输出名只使用 interval ID、状态和 recipient key SHA-256 前缀；
- 单进程 worker 原子领取，支持 retry、cancel 和应用重启 recovery；
- FFmpeg concat/trim/stream-copy 使用 `create_subprocess_exec`、`-n` 和 `.writing` 原子落盘；
- 网页显示任务、输出、重试和取消，不公开 recipient 明文。

### P4A：Windows x64 便携包与正式 Release

- Python `3.13.14` embeddable runtime 和锁定 runtime dependencies；
- 固定 GyanD `7.1` full build FFmpeg/ffprobe URL 与 SHA-256；
- FFmpeg license 为 `GPL-3.0-or-later`，Python license 为 `Python-2.0`；
- 便携版 start/verify/backup 不依赖系统 Python、pip 或 FFmpeg；
- package manifest、Python dependency/license 清单与 SHA256SUMS；
- 干净解压后执行 loopback health、FFmpeg Supervisor、Recording Session 和 postprocess smoke；
- source ZIP 与 Git Bundle 恢复到相同 Git tree；
- annotated tag、版本和 exact CI commit 严格一致后才发布 GitHub Release；
- 发布后再次下载全部资产，在新临时目录复核 checksum、manifest、禁止文件、source ZIP 和 Bundle 恢复。

## 快速开始

Windows Release 用户：

1. 下载 `douyin-recorder-v0.1.0-windows-x64.zip`；
2. 使用 `windows-asset-SHA256SUMS.txt` 校验下载文件；
3. 解压到新目录；
4. 首次运行 `verify.bat`；
5. 通过后运行 `start.bat`。

源码环境：

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements/dev.lock
# Linux/macOS
.venv/bin/python -m pip install -r requirements/dev.lock

python -m app
```

默认只监听：

```text
http://127.0.0.1:3399/
```

尚未实现管理员认证，因此配置拒绝 `0.0.0.0`、局域网和公网绑定。HTTP 层同时拒绝非回环 `Host` 和浏览器跨源写操作。

## 当前 API

### 基础状态

```text
GET /healthz
GET /readyz
GET /api/status
```

### 房间

```text
GET    /api/rooms
POST   /api/rooms
GET    /api/rooms/{room_key}
PATCH  /api/rooms/{room_key}
POST   /api/rooms/{room_key}/actions/check
POST   /api/rooms/{room_key}/actions/enable
POST   /api/rooms/{room_key}/actions/disable
```

### recipient

```text
GET /api/rooms/{room_key}/recipient-state
GET /api/rooms/{room_key}/recipient-events
GET /api/rooms/{room_key}/recipient-intervals
```

### recording

```text
POST /api/rooms/{room_key}/actions/start-recording
POST /api/rooms/{room_key}/actions/stop-recording
GET  /api/rooms/{room_key}/recording
GET  /api/rooms/{room_key}/recording/sessions
GET  /api/rooms/{room_key}/recording/segments
```

### manager

```text
GET  /api/manager/status
POST /api/manager/actions/reconcile
GET  /api/rooms/{room_key}/worker
```

### jobs

```text
POST /api/recording/sessions/{session_id}/actions/create-export
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/actions/retry
POST /api/jobs/{job_id}/actions/cancel
```

recipient API 是只读审计接口。recording、manager 与 jobs API 只返回脱敏输入 host/hash、Session、分片、worker、任务和 recipient hash 审计状态；不会返回 raw payload、异常正文、recipient 明文、完整 WSS、Cookie、完整签名流 URL或真实原始 payload。

## 验证

Windows：

```bat
verify.bat
```

Linux/macOS：

```bash
python -m pip install -r requirements/dev.lock
python -m pip check
python tools/verify_repository_baseline.py
python tools/verify_source.py
python -m compileall -q app tests tools
python -m ruff check --no-cache app tests tools
python -m pytest -q -p no:cacheprovider --tb=short
python tools/replay_recipient_fixture.py --quiet
python tools/replay_recipient_fixture_to_db.py --output userdata/recipient-db-replay.json
python tools/douyin_interactive_evidence.py --help
python tools/export_recipient_evidence_fixture.py --help
python tools/ffmpeg_supervisor_smoke.py --duration 2
python tools/recording_session_smoke.py --duration 2
python tools/postprocess_smoke.py --duration 2
```

数据库 replay 只接受显式 synthetic fixture；公开报告不包含 raw payload。它验证 schema v6 投影与 reducer 的 Waiting/Active/Unknown 结果一致，但不能替代真实现场协议证据。

## GitHub 与防丢

GitHub `main`/tag 是代码唯一权威源。每个可验证里程碑必须 commit + push、Draft PR、exact-head CI、Ready、merge；未 push 内容不算保存。

```bat
backup.bat
```

会创建并恢复验证 Git Bundle、source ZIP、SHA-256，并通过 SQLite backup API 保存运行数据副本。`backups/` 不进 Git，运行数据包可能含私人配置，禁止上传公开仓库。

## 目录边界

```text
config/     默认模板；实际 config.json、runtime.env 和凭据不进 Git
userdata/   SQLite、日志、任务、缓存、私人协议探测和 smoke；不进 Git
records/    原始媒体、导出和代理；不进 Git
```

## 主要文档

```text
AGENTS.md
docs/PRE_IMPLEMENTATION_REVIEW.md
docs/architecture/architecture-baseline-v2.0.md
docs/P0_IMPLEMENTATION_REPORT.md
docs/P1A_IMPLEMENTATION_PLAN.md
docs/P1A_IMPLEMENTATION_REPORT.md
docs/P1B_IMPLEMENTATION_PLAN.md
docs/P1B_IMPLEMENTATION_REPORT.md
docs/P1B_TEST_MATRIX.md
docs/protocol/P1B_PROTOCOL_EVIDENCE_GATE.md
docs/P1C_IMPLEMENTATION_PLAN.md
docs/P1C_IMPLEMENTATION_REPORT.md
docs/protocol/P1C_INTERACTIVE_EVIDENCE_RUNBOOK.md
docs/P1D_IMPLEMENTATION_PLAN.md
docs/P1D_IMPLEMENTATION_REPORT.md
docs/P2A_IMPLEMENTATION_PLAN.md
docs/P2A_IMPLEMENTATION_REPORT.md
docs/P3A_IMPLEMENTATION_PLAN.md
docs/P3A_IMPLEMENTATION_REPORT.md
docs/P4A_RELEASE_PLAN.md
docs/P4A_RELEASE_REPORT.md
docs/RELEASE_PROMOTION.md
docs/FINAL_IMPLEMENTATION_REPORT.md
docs/FINAL_IMPLEMENTATION_REPORT.json
docs/GITHUB_WORKFLOW.md
```

架构基线为 `v2.0`。v0.1.0 交付由 Issue #18 跟踪；真实目标消息事实继续由 Issue #1 跟踪。在 Issue #1 形成去标识、人工审查、可回放的真实 fixture 前，`live_verified=false`。
