# douyin_recorder_workspace

抖音团播多直播间录播与“当前推荐收礼人”时间线系统。

> `main` 已合并 **P1A：单房间媒体基础**。当前分支完成 **P1B 第一批：已解码 recipient 事件的事务持久化、严格状态投影与只读审计 API**；真实 IM 自动接入、长期轮询和完整录制闭环仍未完成。

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

授权房间 `79907888978`、`94771623313`、`40727638291` 的无登录 headless 预检均成功访问公开页面，并在浏览器网络层观察到 allowlist `douyincdn.com` FLV 媒体响应；仍没有观察到 IM WebSocket 或目标推荐收礼人消息。一次未观察到不能证明永久不支持。

## 已实现

### P0

- Python 3.12/3.13、FastAPI、单 Uvicorn worker；
- 同源静态 HTML/CSS/原生 JavaScript ES Modules；
- SQLite WAL、校验和 migration、单写连接与 backup API；
- `/healthz`、`/readyz`、`/api/status`；
- FFmpeg/ffprobe readiness；
- 有界 Protobuf wire inspector、PushFrame/Response/Message envelope；
- 严格 Waiting/Active/Unknown reducer 和确定性合成 replay；
- WSS probe、HTTP/Chrome 脱敏现场预检；
- Windows `start.bat`、`update.bat`、`verify.bat`、`backup.bat`；
- GitHub Actions 与 Git Bundle/源码 ZIP 恢复验证。

### P1A：单房间媒体基础

- 房间 CRUD、enable/disable/check 和单房间读取 API；
- 抖音号与 `https://live.douyin.com/<id>` 严格规范化；
- 受限重定向、SSRF、Host 和浏览器同源写操作边界；
- 公开直播页 JSON/字符串化 JSON 解析；
- `DouyinStreamResolver`：静态页面优先，缺少候选时执行一次性 Chrome/CDP 网络回退；
- 完整签名流 URL 仅存在于有界、带 TTL 的进程内缓存；
- API/SQLite/日志只返回脱敏流候选元数据；
- FFmpeg argv、输入二次校验、progress、segment CSV 和 `RecorderSupervisor`；
- SQLite schema v3 的 `room_checks`、规范化 URL 唯一索引和查询索引；
- 本地 `lavfi` smoke、Windows 和恢复资产 CI。

### P1B 第一批：recipient 事务投影基础

- SQLite schema v4 recipient/session 审计字段和查询索引；
- session 开始时创建 `Waiting(waiting_first_event)`；
- canonical event、dedup、`duplicate_count`、迟到标记和 interval 转换在同一 SQLite 写事务中完成；
- 空 recipient → `Unknown(empty_recipient)`；
- IM 断线 → `Unknown(im_disconnected)`；重连本身不会恢复断线前 recipient；
- 相同 recipient 不重复开启区间；切换 recipient 关闭旧区间并开启新 Active；
- 跨 `runtime_instance_id` 不比较 monotonic 值；
- 所有 64 位 ID 在 Python、SQLite 和 JSON 中保持字符串；
- 房间级只读 recipient state/events/intervals API 不返回 raw payload、extra 或未知字段内容；
- 同一合成 fixture 同时驱动 reducer replay 与临时 SQLite replay，并验证公开结果一致；
- Python 3.12/3.13 与 Windows `verify.bat` 均实际执行数据库 replay。

## 快速开始

Windows：

```bat
start.bat
```

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

尚未实现管理员认证，因此配置会拒绝 `0.0.0.0`、局域网和公网绑定。HTTP 层同时拒绝非回环 `Host` 和浏览器跨源写操作，降低 DNS rebinding/CSRF 风险。

## 房间与 recipient API

```text
GET   /api/rooms
POST  /api/rooms
GET   /api/rooms/{room_key}
PATCH /api/rooms/{room_key}
POST  /api/rooms/{room_key}/actions/check
POST  /api/rooms/{room_key}/actions/enable
POST  /api/rooms/{room_key}/actions/disable

GET   /api/rooms/{room_key}/recipient-state
GET   /api/rooms/{room_key}/recipient-events
GET   /api/rooms/{room_key}/recipient-intervals
```

recipient API 是只读审计接口。它不会返回 `raw_payload_json`、`extra_json`、`unknown_fields_json`、完整 WSS、Cookie 或真实原始 payload。没有 session 时返回稳定空状态，不猜测当前对象。

## FFmpeg Supervisor 本地 smoke

本机安装 FFmpeg 后：

```bash
python tools/ffmpeg_supervisor_smoke.py --duration 3
```

它使用 FFmpeg `lavfi` 生成短测试音视频，验证进程监督、progress、segment CSV 和 MKV 文件写入，不访问抖音。

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
```

数据库 replay 只接受显式 synthetic fixture；公开报告不包含 raw payload。它验证 schema v4 投影与既有 reducer 的 Waiting/Active/Unknown 结果一致，但不能替代真实现场协议证据。

## GitHub 与防丢

GitHub `main`/tag 是代码唯一权威源。每个可验证里程碑必须 commit + push；未 push 内容不算保存。

```bat
backup.bat
```

会创建并恢复验证 Git Bundle、源码 ZIP、SHA-256，并通过 SQLite backup API 保存运行数据副本。`backups/` 不进 Git，运行数据包可能含私人配置，禁止上传公开仓库。

## 目录边界

```text
config/     默认模板；实际 config.json、runtime.env 和凭据不进 Git
userdata/   SQLite、日志、任务、缓存、私人协议探测和 smoke；不进 Git
records/    原始媒体、导出和代理；不进 Git
```

## 开发文档

1. `AGENTS.md`
2. `docs/PRE_IMPLEMENTATION_REVIEW.md`
3. `docs/architecture/architecture-baseline-v2.0.md`
4. `docs/P0_IMPLEMENTATION_REPORT.md`
5. `docs/P1A_IMPLEMENTATION_PLAN.md`
6. `docs/P1A_IMPLEMENTATION_REPORT.md`
7. `docs/P1B_IMPLEMENTATION_PLAN.md`
8. `docs/P1B_IMPLEMENTATION_REPORT.md`
9. `docs/P1B_TEST_MATRIX.md`
10. `docs/protocol/P1B_PROTOCOL_EVIDENCE_GATE.md`
11. `docs/GITHUB_WORKFLOW.md`

架构基线：`v2.0`。P1B 第一批关联 Issue #7；真实目标消息事实继续由 Issue #1 跟踪。在 Issue #1 形成去标识、人工审查、可回放的真实 fixture 前，`live_verified=false`。
