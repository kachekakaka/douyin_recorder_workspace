# 新实施窗口启动指令

当前实施批次为 **P1A：单房间管理、直播流候选解析与 FFmpeg Supervisor 基础**。

> 使用 GitHub 仓库 `kachekakaka/douyin_recorder_workspace` 作为唯一代码权威源。先读取 `AGENTS.md`、`docs/PRE_IMPLEMENTATION_REVIEW.md`、`docs/architecture/architecture-baseline-v2.0.md`、`docs/P1A_IMPLEMENTATION_PLAN.md` 和 GitHub Issue #3。从最新 `main` 创建或继续 `feature/p1a-single-room-media-foundation`。本批次只做房间 CRUD、严格 URL/SSRF 边界、公开直播页与 FLV/HLS 候选解析、脱敏检查结果、FFmpeg argv/progress/segment/process supervisor、本地 smoke、SQLite schema v2、基础静态网页和测试。不得宣称真实 recipient 协议已验证；`live_verified` 保持 false；不得增加多平台、多机、PostgreSQL、Redis、Celery、前端框架或任何身份推断 fallback。每个可验证里程碑测试、commit、push；真实 Cookie、签名流 URL、抓包、SQLite、日志和录像不得进入 Git。完成后创建 PR，报告远端 SHA、CI、运行命令和仍未解决的现场事实。
