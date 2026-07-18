# douyin_recorder_workspace

抖音团播多直播间录播与“当前推荐收礼人”时间线系统。

## 当前状态

当前仓库先冻结产品与架构基线，代码从 **P0：仓库骨架与协议验证** 开始实施。

唯一身份信号：

```text
WebcastGroupLiveGiftRecipientRecommendMessage
```

首事件前、空 recipient、IM 断线期间一律为 `Unknown`；不使用 OCR、人脸、声纹、礼物、弹幕、连麦成员或画面位置进行补全。

## 开工前必读

1. `AGENTS.md`
2. `docs/PRE_IMPLEMENTATION_REVIEW.md`
3. `docs/architecture/architecture-baseline-v2.0.md`
4. `docs/IMPLEMENTATION_WINDOW_PROMPT.md`
5. `docs/GITHUB_WORKFLOW.md`

## 架构快照

- Python 3.12/3.13 + FastAPI + asyncio
- 静态 HTML/CSS/原生 JavaScript ES Modules
- SQLite WAL，数据库固定在本机 `userdata/`
- 每个在线直播间一个由 Python 监督的 FFmpeg 子进程
- 单主进程、单 Uvicorn worker
- Windows 与 Docker Compose

## 目录边界

```text
config/     实际配置模板；真实配置和凭据不进 Git
userdata/   SQLite、日志、任务、缓存和临时状态；不进 Git
records/    原始媒体和导出；不进 Git
```

## 当前版本

架构基线：`v2.0`，状态为“批准实施，附开工前修订项”。
