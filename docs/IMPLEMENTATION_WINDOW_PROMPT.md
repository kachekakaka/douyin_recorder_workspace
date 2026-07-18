# 新实施窗口启动指令

把下面内容作为新窗口的第一条任务说明：

> 使用 GitHub 仓库 `kachekakaka/douyin_recorder_workspace` 作为唯一代码权威源。先读取 `AGENTS.md`、`docs/PRE_IMPLEMENTATION_REVIEW.md` 和 `docs/architecture/architecture-baseline-v2.0.md`。只实施 P0：可恢复仓库骨架、FastAPI + 静态网页最小启动、SQLite migration 框架、FFmpeg/ffprobe readiness、自检、CI，以及单房间抖音 WSS method/raw-frame 验证工具和 replay fixture。不要进入完整 P1 录制，不要增加多平台、PostgreSQL、Redis、Celery、前端框架或任何身份推断 fallback。先从最新 main 创建 `feature/p0-bootstrap`；每个可验证里程碑执行测试并 commit + push；任何真实 Cookie、抓包隐私数据、SQLite、日志和录像不得进入 Git。完成后提交 PR，并给出已 push 的 commit SHA、CI 结果、运行命令和未解决协议事实。
