# douyin_recorder_workspace

抖音团播多直播间录播与“当前推荐收礼人”时间线系统。

> 当前分支进入 **P1A：单房间管理、直播流候选解析与 FFmpeg Supervisor 基础**。完整自动录制与真实 recipient 接线尚未完成。

## 不可改变的业务口径

系统只使用：

```text
WebcastGroupLiveGiftRecipientRecommendMessage
```

更新当前推荐收礼人。首条有效事件前、空 recipient、IM 断线及重连后尚未收到新事件时均为 `Unknown`。不使用 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称或画面位置补全；推荐对象变化不得重启 FFmpeg。

运行 contract 继续保持：

```text
live_verified=false
```

授权账号 `73504089679` 的无登录 headless 预检成功访问公开页面，但没有观察到 IM WebSocket；这不等于该账号永久不支持目标消息。

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

### P1A 当前批次

- `GET/POST/PATCH /api/rooms`；
- enable/disable/check actions；
- 抖音号与 `https://live.douyin.com/<id>` 严格规范化；
- 受限重定向和 SSRF 边界；
- 公开直播页 JSON/字符串化 JSON 解析；
- FLV/HLS/画质流候选只保留在进程内存；
- API/SQLite/日志只返回 host、path、query key 和 URL hash；
- FFmpeg argv、progress、segment CSV 和 RecorderSupervisor 核心；
- 本地 lavfi smoke 工具；
- SQLite schema v2 `room_checks` 审计；
- 网页新增直播间、启停和立即检查。

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

P1A 尚未实现管理员认证，配置会拒绝 `0.0.0.0`、局域网和公网绑定。

## 房间 API

```text
GET   /api/rooms
POST  /api/rooms
PATCH /api/rooms/{room_key}
POST  /api/rooms/{room_key}/actions/check
POST  /api/rooms/{room_key}/actions/enable
POST  /api/rooms/{room_key}/actions/disable
```

创建示例：

```json
{
  "room_key": "group-a",
  "room_url": "73504089679",
  "quality": "origin",
  "protocol": "flv"
}
```

“立即检查”不会在响应中返回完整签名流 URL。

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
python tools/verify_repository_baseline.py
python tools/verify_source.py
python -m compileall -q app tests tools
python -m ruff check --no-cache app tests tools
python -m pytest -q -p no:cacheprovider --tb=short
```

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
6. `docs/protocol/P0_PROTOCOL_STATUS.md`
7. `docs/GITHUB_WORKFLOW.md`

架构基线：`v2.0`。P1A 关联 GitHub Issue #3；真实目标消息事实继续由 Issue #1 跟踪。
