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
8. 已有正式 Release 时，验证成功的 exact-tag Release workflow 后保持 tag 不变；
9. 永不 force push、删除或覆盖 tag。

Actions 的仓库 `GITHUB_TOKEN` 推送 tag 不会递归触发普通 `push` workflow。因此，
promotion 在完成 tag 门禁后，显式对该 tag 执行：

```text
gh workflow run release.yml --ref v<version>
```

若已存在相同 tag/commit 的 queued 或 running Release workflow，则只记录现有 run；
若存在失败 run，则门禁失败并要求通过独立 PR 修复，不自动重跑或掩盖失败。

`v0.1.0` 的 annotated tag message 明确记录：首个 Windows x64 可恢复发布、
多房间自动录制、recipient 时间线、后处理导出、`live_verified=false`，以及真实
recipient 协议继续由 Issue #1 跟踪。

Promotion 与正式 Release workflow 都会把机器生成的审计 JSON 写入 Issue #18，
包括 CI/run ID、tag object/commit、Release ID、发布时间和每个资产的大小/digest。
正式 Release 已存在时，workflow 只允许下载并逐字节验证同一资产，不覆盖 Release。
