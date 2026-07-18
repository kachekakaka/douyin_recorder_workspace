# 开工前审查结论

状态：**批准实施，附修订项**  
基线：`docs/architecture/architecture-baseline-v2.0.md`  
日期：2026-07-18

## 总结

v2.0 的主架构可以直接作为开发基线，不再进行一次大规模架构重写。实施前只冻结以下修订项；新窗口不得自行扩大到多平台、多机、Redis/Celery、前端框架或实时按成员切流。

## 必须冻结的修订项

### 1. GitHub 是代码唯一权威源

- GitHub `main`/tag 中必须直接存在可审查的 Python、网页、迁移、测试、脚本和文档。
- 章节化 Markdown 是 Git diff 和 PR 审查的权威文本；DOCX/PDF 是 GitHub Release 阅读快照。
- 每次工作离开窗口前必须 commit + push；未 push 的内容不视为已保存。

### 2. Windows/FFmpeg 运行包不进入普通 Git 历史

- 首版源码仓库不提交大体积 Python/FFmpeg runtime pack。
- P4 发布时由 GitHub Actions 构建，并作为 GitHub Release 资产保存；仓库只跟踪 manifest、SHA-256 和构建脚本。
- 单文件不得接近 GitHub 普通对象 100 MiB 限制。

### 3. 单调时钟必须带运行实例标识

`time.monotonic_ns()` 只在同一进程生命周期内可比较。事件和媒体记录除 `received_monotonic_ns` 外，必须增加：

```text
runtime_instance_id
```

重启后的 monotonic 值不得与重启前直接比较；跨重启审计使用 UTC、场次恢复事件和实际媒体 PTS。

### 4. P0 先验证协议事实，再实现完整状态机

P0 必须先产出可重复的：

- 单房间 WSS 连接器；
- method/raw-frame dump；
- 目标消息 fixture；
- 空 recipient、重复帧、IM 断线/重连序列；
- 字段与时间延迟报告。

在目标房间未确认确实下发该消息前，可以实现工程骨架，但不得宣称推荐收礼人功能完成。

### 5. 第三方许可先于代码复用

- 可借鉴 `bili_workspace` 的工程结构，但新项目独立设计。
- `biliup` MIT、`f2` Apache-2.0；复用必须保留相应声明。
- `douyin_contract` 未确认明确许可前只作技术参考，不直接复制。

### 6. 仓库元数据在 P0 起始时落地

```text
README.md
AGENTS.md
.gitignore
.gitattributes
THIRD_PARTY_NOTICES.md
docs/architecture/*
docs/PRE_IMPLEMENTATION_REVIEW.md
docs/GITHUB_WORKFLOW.md
.github/workflows/ci.yml
```

### 7. API 小修订

```text
GET  /api/jobs
GET  /api/jobs/{id}
POST /api/jobs/{id}/actions/retry
POST /api/jobs/{id}/actions/cancel
```

P0/P1 可由 FastAPI OpenAPI 作为请求/响应模型权威源，后续保持向后兼容或显式版本迁移。

## 开工门槛

满足以下条件后，新实施窗口可以开始 P0：

- 仓库已创建并完成初始 push；
- 架构 Markdown、SVG、审查文件和 CI 已在 GitHub；
- 新窗口具有 GitHub 写权限；
- 从 `feature/p0-bootstrap` 分支工作；
- 每个阶段结束前 commit + push。

DOCX/PDF 作为 Release 阅读快照，不阻塞 P0；首个正式发布前补齐并记录 SHA-256。
