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

随后创建 PR。CI 全部通过、审查完成后才合并。运行机器只使用 `main` 或已验证 tag：

```bash
git switch main
git pull --ff-only origin main
```

根目录 `update.bat` 会拒绝：

- 非 `main` 分支；
- 有未提交受 Git 管理修改的工作区；
- 非 fast-forward 更新。

## 2. P0/P1A 必须通过的 CI

- Python 3.12、3.13；
- 依赖锁与 `pip check`；
- 仓库/秘密边界；
- `compileall`；
- Ruff；
- pytest；
- 合成 fixture 确定性 replay；
- 原生 JavaScript 语法；
- Windows `verify.bat`；
- FFmpeg Supervisor 本地 `lavfi` smoke；
- Git Bundle、源码 ZIP、SHA-256 和临时克隆恢复。

现场抖音预检使用独立 workflow，对授权房间执行公开 HTTP、无登录 Chrome/CDP WSS 和媒体网络观察。网络失败、未观察到目标消息或只观察到媒体候选均写入脱敏报告，不能自动推导 recipient 协议结论。报告不得包含 Cookie、完整接口/流 URL、query value、响应正文、原始帧或真实 payload。

## 3. 防止“窗口丢内容”

每个可验证里程碑都执行：

```bash
git add -A
git commit -m "<type>: <clear description>"
git push
```

离开开发窗口前必须在 GitHub 网页确认 commit SHA 可见。分支上的代码不会因为聊天窗口结束而丢失。

## 4. 一键源码与运行数据备份

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

## 5. 发布

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

## 6. 恢复

```bash
git bundle verify douyin-recorder-<version>.bundle
git clone douyin-recorder-<version>.bundle restored-repo
cd restored-repo
python tools/verify_repository_baseline.py
```

再恢复同一时间点的实际配置、SQLite 和 records。不能只恢复数据库而忽略媒体，也不能只恢复媒体而忽略数据库索引。
