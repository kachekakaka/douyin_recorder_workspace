# GitHub 仓库、提交、发布与恢复流程

GitHub 仓库：

```text
kachekakaka/douyin_recorder_workspace
```

GitHub `main`、commit、PR、annotated tag、Release、Actions 和 Release assets 是唯一权威源。聊天、临时沙箱、本地未提交文件和未 push 分支都不算保存或完成证据。

## 1. 日常开发

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/<milestone>

# 修改并验证
verify.bat
git status --short
git add -A
git commit -m "<type>: <clear description>"
git push -u origin feature/<milestone>
```

随后创建 Draft PR。每个可验证里程碑都必须独立 commit + push；CI 全部通过、审查完成后才改为 Ready 或合并。

运行机器只使用 `main` 或已验证 tag：

```bash
git switch main
git pull --ff-only origin main
```

根目录 `update.bat` 会拒绝：

- 非 `main` 分支；
- 有未提交受 Git 管理修改的工作区；
- 非 fast-forward 更新。

## 2. 标准 CI 门禁

标准 CI 必须覆盖并通过：

- Python 3.12、3.13；
- 依赖锁与 `pip check`；
- repository baseline；
- source/secret boundary；
- `compileall`；
- Ruff；
- pytest；
- synthetic fixture 的确定性 recipient reducer replay；
- synthetic fixture 到临时 SQLite 的确定性 database replay；
- P1C synthetic CDP/WSS evidence、私人输出边界和 approval/hash 导出测试；
- P1D recording/recipient 共享 Session、启动/停止/退出/恢复、segment 持久化和 API 隐私测试；
- P2A 多房间 worker 串行、全局检查 semaphore、offline 阈值、unknown 保持媒体、退避和故障隔离测试；
- P3A ExportPlan、idempotency、原子领取、retry/cancel/recovery、API 隐私和输出路径边界测试；
- database replay 的 schema、duplicate、late、interval 和公开字段检查；
- Frontend JavaScript syntax；
- Windows `verify.bat`；
- FFmpeg Supervisor 本地 `lavfi` smoke；
- Recording Session 本地 `lavfi` smoke；
- 两段 MKV postprocess concat/trim/copy smoke；
- Git Bundle、source ZIP、SHA-256 和临时克隆恢复。

Database replay 命令：

```bash
python tools/replay_recipient_fixture_to_db.py \
  --output userdata/recipient-db-replay.json
```

CI 和 Windows 都必须实际执行该命令。公开 replay 报告不得包含 `raw_payload_json`、`extra_json`、`unknown_fields_json`、原始 frame、真实 recipient 明文或数据库绝对路径。

现场抖音预检使用独立 workflow，对授权房间执行公开 HTTP、无登录 Chrome/CDP WSS 和媒体网络观察。网络失败、未观察到目标消息或只观察到媒体候选均写入脱敏报告，不能自动推导 recipient 协议结论。报告不得包含 Cookie、完整接口/流 URL、query value、响应正文、原始 frame 或真实 payload。

## 3. 阶段合并规则

- P1A 媒体基础可以在 `live_verified=false` 下完成和合并；
- P1B 事务投影基础可以消费 synthetic 或已经解码事件，但不能宣布真实 IM 自动接入完成；
- P1C 只提供私人证据采集与人工审批门禁；工具链通过不能自动修改 contract；
- P1D 提供显式单房间录制闭环，不因 recipient 变化重启媒体；
- P2A 仅在单进程内长期编排 enabled rooms；unknown/blocked/error 不得当作 offline，且不引入分布式队列；
- P3A 仅使用单进程 SQLite/asyncio worker；输出必须 no-overwrite、`.writing` 原子落盘并且不含 recipient 明文；
- P4A Windows/source package 必须经过干净目录验证和恢复验证；
- `WebcastGroupLiveGiftRecipientRecommendMessage` 的真实字段、空值、重复、切换和重连证据继续由 Issue #1 门禁；
- 只有去标识、人工审查、可回放的真实 fixture 形成后，才能在独立 PR 中评审 contract 或 `live_verified` 变更；
- 不得为使 CI 通过而删除测试、降低断言、使用 `continue-on-error` 或提交真实凭据。

## 4. 防止“窗口丢内容”

每个可验证里程碑都执行：

```bash
git add -A
git commit -m "<type>: <clear description>"
git push
```

离开开发窗口前必须在 GitHub 网页确认 commit SHA 可见。分支上的代码不会因为聊天窗口结束而丢失。

## 5. 源码与运行数据备份

Windows：

```bat
backup.bat
```

或分别运行：

```bash
python tools/create_recovery_assets.py --output-dir backups/source
python tools/backup_runtime.py --output-dir backups/runtime
```

源码备份包括：

- `git bundle --all`；
- `git archive` source ZIP；
- manifest 与 SHA-256；
- 从 Bundle 临时 clone；
- `git fsck`；
- 恢复仓库 baseline 验证。

运行数据备份包括：

- 实际 `config.json`、`runtime.env`（存在时）；
- SQLite backup API 一致性副本和 `integrity_check`；
- records 文件路径、大小和时间索引。

`backups/` 被 `.gitignore` 排除。运行数据包可能含私人配置，禁止提交 GitHub；应额外保存在受保护的 NAS 或云盘。

## 6. v0.1.0 已验证发布身份

```text
release source main: b8056289ea9c18504675ff1dd43df84a977b2436
tag:                 v0.1.0
tag object:          567d8d9dc6559226c76625cd4cc0df040b1b903c
tag commit:          b8056289ea9c18504675ff1dd43df84a977b2436
main CI run:         29896339087
tag promotion run:  29896396873
Release run:         29896406871
Release ID:          357811419
published at:        2026-07-22T06:21:41Z
```

最终发布报告：

```text
docs/FINAL_IMPLEMENTATION_REPORT.md
docs/FINAL_IMPLEMENTATION_REPORT.json
```

## 7. 不可变发布提升流程

发布不再依赖人工直接在本地执行 tag 命令。永久工作流 `.github/workflows/release-promotion.yml` 在标准 `CI` 的 `main` push run 成功后执行。

Promotion 必须确认：

1. 成功 CI 来自同仓库、事件为 push、分支为 `main`；
2. CI 的 `head_sha` 仍是当前最新 `main`；
3. `pyproject.toml`、`app.__version__` 和 `packaging/release-lock.json` 版本一致；
4. 对应 `docs/releases/v<version>.md` 存在且非空；
5. `target_method=WebcastGroupLiveGiftRecipientRecommendMessage`；
6. `live_verified=false`；
7. 创建首次 tag 时 Issue #1 和当前发布 Issue 保持 Open；
8. tag 不存在时使用 `git tag -a` 创建 annotated tag；
9. tag 已存在时不删除、不覆盖、不 force push、不移动；
10. 尚未发布的既有 tag 必须是 annotated tag 且解引用 commit 精确等于已验证 commit。

仓库 `GITHUB_TOKEN` 推送 tag 不依赖递归 push 触发。Promotion 在 tag 门禁通过后显式调用：

```text
gh workflow run release.yml --ref v<version>
```

若同一 tag/commit 已有 queued 或 running Release workflow，只记录现有 run；若存在失败 run，门禁失败并要求通过独立 branch/PR 修复，不自动掩盖红灯。

## 8. 正式 Release workflow

`.github/workflows/release.yml` 在精确 tag ref 上执行三个 job：

```text
Build and verify Windows x64 package
Build and restore source assets
Publish GitHub Release
```

Windows job 必须：

- 校验 release lock、项目版本和 tag；
- 构建 deterministic Windows x64 ZIP；
- 在新的临时目录解压；
- 校验 manifest、内部 SHA256SUMS、依赖和许可证；
- 运行 embedded Python import、loopback health、FFmpeg Supervisor、Recording Session、postprocess smoke；
- 上传正式 Windows 资产。

Source job 必须：

- 构建 `git archive` source ZIP；
- 构建 Git Bundle；
- 生成 source tree manifest 和 SHA-256；
- `git bundle verify`；
- 新目录 clone；
- detached checkout 精确 commit；
- Git tree 对比；
- repository baseline。

Publish job 必须：

- 确认 9 个规定资产存在且非空；
- 通过 Windows/source 外部 SHA-256 清单；
- 确认 tag object 类型为 `tag`；
- 确认 tag 解引用 commit 等于 workflow `GITHUB_SHA`；
- Release 不得是 draft 或 prerelease；
- 第一次创建时使用 `--verify-tag`；
- Release 已存在时只允许下载并逐字节比较同一资产，不覆盖 Release；
- 把 Release ID、run ID、tag object/commit、发布时间和全部资产 size/digest 写入发布 Issue 的机器审计评论。

## 9. 正式 Release 资产集合

```text
douyin-recorder-v<version>-windows-x64.zip
douyin-recorder-v<version>-source.zip
douyin-recorder-v<version>-source.bundle
windows-manifest.json
windows-SHA256SUMS.txt
windows-asset-SHA256SUMS.txt
source-tree-manifest.json
source-SHA256SUMS.txt
python-dependencies.json
```

资产必须不存在：

- `.env`；
- Cookie、Authorization、token 或账号密码；
- SQLite 文件；
- 日志；
- 媒体；
- raw frame；
- 真实 payload；
- 完整签名 URL；
- Actions artifact ZIP；
- 符号链接和路径越界成员。

大体积 Python/FFmpeg runtime 不直接提交普通 Git 历史，只存在于经过 manifest/SHA-256 验证的 Release asset。

## 10. 发布后独立资产复核

正式 Release 成功后，应在新的临时目录重新下载 Release assets，独立执行：

```text
Release API asset count/size/digest
windows-asset-SHA256SUMS.txt
source-SHA256SUMS.txt
Windows ZIP unzip
Windows manifest + internal SHA256SUMS
forbidden-file scan
Python dependency/license checks
source ZIP manifest
Git Bundle verify/clone/tree comparison
```

v0.1.0 的独立复核 run：

```text
29897165335  Final Release Asset Audit  success
```

该审计 workflow 只用于 PR #20 的临时复核，取得证据后从分支删除，最终树不保留一次性 materializer/fix workflow。

## 11. 最终实施报告 PR

Release 成功后，从最新已发布 `main` 创建：

```text
feature/v<version>-final-report
```

更新：

```text
docs/FINAL_IMPLEMENTATION_REPORT.md
docs/FINAL_IMPLEMENTATION_REPORT.json
README.md
docs/GITHUB_WORKFLOW.md
```

必须完成：

```text
commit
push
Draft PR
exact-head CI
Ready for review
merge main
```

报告使用发布源 main/tag commit 作为 Release source SHA。报告 PR 合并后的新 main merge SHA 只能在 GitHub 实际产生后记录于最终交付输出，不能在报告中预写猜测。

Issue #18 只有在 tag、Release、9 项资产、独立复核和最终报告 PR 全部完成后才允许关闭。Issue #1 必须保持 Open。

## 12. 恢复

```bash
git bundle verify douyin-recorder-v<version>-source.bundle
git clone douyin-recorder-v<version>-source.bundle restored-repo
cd restored-repo
git checkout --detach <tag-commit>
python tools/verify_repository_baseline.py
```

再恢复同一时间点的实际配置、SQLite 和 records。不能只恢复数据库而忽略媒体，也不能只恢复媒体而忽略数据库索引。
