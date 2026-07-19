# douyin_recorder_workspace

抖音团播多直播间录播与“当前推荐收礼人”时间线系统。

> 当前处于 **P0：可恢复工程骨架与协议事实验证**。完整自动录制闭环属于 P1，尚未开始。

## 不可改变的业务口径

系统只使用：

```text
WebcastGroupLiveGiftRecipientRecommendMessage
```

更新当前推荐收礼人。首条有效事件前、空 recipient、IM 断线及重连后尚未收到新事件时均为 `Unknown`。不使用 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称或画面位置补全；推荐对象变化不得重启 FFmpeg。

## P0 已实现

- Python 3.12/3.13 + FastAPI + 单 Uvicorn worker；
- FastAPI 同源托管静态 HTML/CSS/原生 JavaScript ES Modules；
- `/healthz`、`/readyz`、`/api/status`；
- SQLite WAL、递增校验和 migration、单写连接和一致性 backup API；
- FFmpeg/ffprobe readiness；
- 有界 Protobuf wire inspector、PushFrame/Response/Message envelope 与 gzip 上限；
- 显式 contract、严格 recipient reducer、合成 replay fixture；
- 单房间显式 WSS 探测工具；
- 无登录 HTTP 直播页预检和 Chrome/CDP WSS 安全观察工具；
- Windows `start.bat`、`update.bat`、`verify.bat`、`backup.bat`；
- GitHub Actions：Python 3.12/3.13、Ruff、pytest、replay、前端语法、Windows 自检和 Git Bundle 恢复验证。

P0 的现场 contract 仍保持：

```text
live_verified=false
```

合成 fixture 通过不等于真实直播间已验证。

## 快速开始

Windows：

```bat
start.bat
```

源码环境：

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -r requirements/dev.lock
# Linux/macOS:
.venv/bin/python -m pip install -r requirements/dev.lock
python -m app
```

默认只监听：

```text
http://127.0.0.1:3399/
```

P0 尚未实现管理员认证，因此配置会拒绝 `0.0.0.0`、局域网地址和公网绑定。

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

## 经授权直播间预检

只做不保存正文的 HTTP 预检：

```bash
python tools/douyin_room_preflight.py --room-id 73504089679
```

在装有 Chrome/Chromium 的环境中，通过 CDP 观察浏览器实际创建的 WSS，并只输出脱敏统计：

```bash
python tools/douyin_browser_probe.py \
  --room-id 73504089679 \
  --duration 60 \
  --output userdata/protocol-probes/browser-report.json
```

完整签名 WSS URL、Cookie、原始帧和真实 payload 不得提交仓库。现场取证流程见 `docs/protocol/CAPTURE_RUNBOOK.md`。

## GitHub 与防丢

GitHub `main`/tag 是代码唯一权威源。每个可验证里程碑必须 commit + push；未 push 的内容不算保存。

创建源码与运行数据备份：

```bat
backup.bat
```

它会：

1. 创建完整 Git Bundle、源码 ZIP 和 SHA-256；
2. 从 Bundle 临时克隆并执行 `git fsck` 与仓库基线校验；
3. 使用 SQLite backup API 备份数据库；
4. 保存实际配置和 records 文件索引。

`backups/` 被 Git 忽略。运行数据备份可能含私人配置，禁止上传公开仓库。

## 目录边界

```text
config/     默认模板；实际 config.json、runtime.env 和凭据不进 Git
userdata/   SQLite、日志、任务、缓存、临时状态和私人协议探测；不进 Git
records/    原始媒体、导出和代理；不进 Git
```

## 开发文档

1. `AGENTS.md`
2. `docs/PRE_IMPLEMENTATION_REVIEW.md`
3. `docs/architecture/architecture-baseline-v2.0.md`
4. `docs/P0_IMPLEMENTATION_REPORT.md`
5. `docs/protocol/P0_PROTOCOL_STATUS.md`
6. `docs/GITHUB_WORKFLOW.md`

架构基线：`v2.0`，状态为“批准实施，附开工前修订项”。
