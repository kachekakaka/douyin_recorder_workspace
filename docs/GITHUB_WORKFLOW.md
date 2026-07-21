# GitHub 仓库、提交、发布与恢复流程

GitHub 仓库：

```text
kachekakaka/douyin_recorder_workspace
```

GitHub `main`/tag 是代码唯一权威源。聊天、临时沙箱、本地未提交文件和未 push 分支都不算保存。

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

随后创建 Draft PR。每个可验证里程碑都必须独立 commit + push；CI 全部通过、审查完成后才改为 Ready 或合并。运行机器只使用 `main` 或已验证 tag：

```bash
git switch main
git pull --ff-only origin main
```

根目录 `update.bat` 会拒绝：

- 非 `main` 分支；
- 有未提交受 Git 管理修改的工作区；
- 非 fast-forward 更新。

## 2. P0/P1A/P1B/P1C/P1D/P2A/P3A 必须通过的 CI

- Python 3.12、3.13；
- 依赖锁与 `pip check`；
- 仓库/秘密边界；
- `compileall`；
- Ruff；
- pytest；
- 合成 fixture 的确定性 reducer replay；
- P1B synthetic fixture 到临时 SQLite 的确定性 database replay；
- P1C synthetic CDP/WSS evidence、私人输出边界和 approval/hash 导出测试；
- P1D recording/recipient 共享 Session、启动/停止/退出/恢复、segment 持久化和 API 隐私测试；
- P2A 多房间 worker 串行、全局检查 semaphore、offline 阈值、unknown 保持媒体、退避和故障隔离测试；
- P3A ExportPlan、idempotency、原子领取、retry/cancel/recovery、API 隐私和输出路径边界测试；
- database replay 的 schema、duplicate、late、interval 和公开字段检查；
- 原生 JavaScript 语法；
- Windows `verify.bat`；
- FFmpeg Supervisor 本地 `lavfi` smoke；
- P1D recording Session 本地 `lavfi` smoke；
- P3A 两段 MKV postprocess concat/trim/copy smoke；
- Git Bundle、源码 ZIP、SHA-256 和临时克隆恢复。

P1B database replay 命令：

```bash
python tools/replay_recipient_fixture_to_db.py \
  --output userdata/recipient-db-replay.json
```

CI 和 Windows 都必须实际执行该命令。公开 replay 报告不得包含 `raw_payload_json`、`extra_json`、`unknown_fields_json`、原始 frame、真实 recipient 明文或数据库绝对路径。

现场抖音预检使用独立 workflow，对授权房间执行公开 HTTP、无登录 Chrome/CDP WSS 和媒体网络观察。网络失败、未观察到目标消息或只观察到媒体候选均写入脱敏报告，不能自动推导 recipient 协议结论。报告不得包含 Cookie、完整接口/流 URL、query value、响应正文、原始帧或真实 payload。

## 3. 阶段合并规则

- P1A 媒体基础可以在 `live_verified=false` 下完成和合并；
- P1B 事务投影基础可以消费 synthetic 或已经解码事件，但不能宣布真实 IM 自动接入完成；
- P1C 只提供私人证据采集与人工审批门禁；工具链通过不能自动修改 contract；
- P1D 提供显式单房间录制闭环，不因 recipient 变化重启媒体；
- P2A 仅在单进程内长期编排 enabled rooms；unknown/blocked/error 不得当作 offline，且不引入分布式队列；
- P3A 仅使用单进程 SQLite/asyncio worker；输出必须 no-overwrite、`.writing` 原子落盘并且不含 recipient 明文；
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

## 5. 一键源码与运行数据备份

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
- `git archive` 源码 ZIP；
- manifest 与 SHA-256；
- 从 Bundle 临时克隆；
- `git fsck`；
- 恢复仓库基线验证。

运行数据备份包括：

- 实际 `config.json`、`runtime.env`（存在时）；
- SQLite backup API 一致性副本和 `integrity_check`；
- records 文件路径、大小和时间索引。

`backups/` 被 `.gitignore` 排除。运行数据包可能含私人配置，禁止提交 GitHub；应额外保存在受保护的 NAS 或云盘。

## 6. 发布

```bash
git tag -a v0.1.0 -m "douyin recorder v0.1.0"
git push origin v0.1.0
```

GitHub Release 保存：

- 源码 ZIP；
- Git Bundle；
- Windows/runtime pack（正式发布阶段）；
- manifest 与 SHA256SUMS；
- 验证报告和迁移说明。

大体积 Python/FFmpeg 运行包不直接提交普通 Git 历史。

## 7. 恢复

```bash
git bundle verify douyin-recorder-<version>.bundle
git clone douyin-recorder-<version>.bundle restored-repo
cd restored-repo
python tools/verify_repository_baseline.py
```

再恢复同一时间点的实际配置、SQLite 和 records。不能只恢复数据库而忽略媒体，也不能只恢复媒体而忽略数据库索引。
