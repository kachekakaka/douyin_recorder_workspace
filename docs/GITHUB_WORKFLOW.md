# GitHub 仓库、提交、发布与恢复流程

## 1. 建仓库

建议仓库：

```text
kachekakaka/douyin_recorder_workspace
visibility: private
branch: main
```

创建空仓库后，从本地基线推送：

```bash
git remote add origin https://github.com/kachekakaka/douyin_recorder_workspace.git
git push -u origin main
```

## 2. 日常工作

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/p0-bootstrap

# 修改并验证
git status --short
git add -A
git commit -m "chore: bootstrap P0 workspace"
git push -u origin feature/p0-bootstrap
```

在 GitHub 创建 PR，CI 通过后合并。运行机器只更新 `main` 或已验证 tag：

```bash
git switch main
git pull --ff-only origin main
```

## 3. 防止“窗口丢内容”

每个独立里程碑都执行：

```bash
git add -A
git commit -m "<type>: <clear description>"
git push
```

只有远端可见 commit SHA 才算已保存。聊天记录、临时沙箱文件和本地未提交改动都不是项目权威源。

## 4. 发布

```bash
git tag -a v0.1.0 -m "douyin recorder v0.1.0"
git push origin v0.1.0
```

源码 ZIP、runtime pack、manifest、SHA256SUMS 和验证报告放 GitHub Release。大体积运行包不要直接提交普通 Git 历史。

## 5. 完整 Git 历史备份与恢复

```bash
git bundle create douyin-recorder-all.bundle --all
git bundle verify douyin-recorder-all.bundle
git clone douyin-recorder-all.bundle douyin-recorder-restored
```

## 6. 运行数据

GitHub 保存代码，不保存真实配置、Cookie、SQLite 和录像。运行数据按架构文档另行备份。
