# Release tag promotion gate

`main` 的标准 `CI` push run 全绿后，永久工作流
`.github/workflows/release-promotion.yml` 才允许提升版本 tag。

门禁流程：

1. 精确使用成功 CI 的 `head_sha`，并确认它仍是当前 `main`；
2. 校验 `pyproject.toml`、`app.__version__`、`packaging/release-lock.json` 版本一致；
3. 校验对应 `docs/releases/v<version>.md` 非空；
4. 强制要求目标 method 保持
   `WebcastGroupLiveGiftRecipientRecommendMessage` 且 `live_verified=false`；
5. 强制要求 Issue #1 和 Issue #18 仍为 Open；
6. tag 不存在时，在精确 CI commit 创建 annotated tag 并普通 push；
7. 尚未发布的 tag 已存在时，只接受 annotated tag 且解引用 commit 完全一致；
8. 已有正式 Release 时，后续 `main` 报告提交只读确认，不移动既有 tag；
9. 永不 force push、删除或覆盖 tag。

`v0.1.0` 的 annotated tag message 明确记录：首个 Windows x64 可恢复发布、
多房间自动录制、recipient 时间线、后处理导出、`live_verified=false`，以及真实
recipient 协议继续由 Issue #1 跟踪。

Tag push 仍由既有 `.github/workflows/release.yml` 构建 Windows/source 资产并发布
GitHub Release。本工作流是版本派生的长期发布门禁，不是一次性
materializer/fix workflow。
