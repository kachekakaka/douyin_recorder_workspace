# P4A 实施报告：Windows 便携包与 v0.1.0 Release

状态：**工程实现候选完成，等待真实 Windows package workflow 与 PR exact-head CI；`live_verified=false`。**
关联：Issue #18、协议事实 Issue #1
分支：`feature/p4a-release-packaging`

## 已实现

- 项目、应用和 release lock 统一版本 `0.1.0`；
- Python 3.13.14 embeddable x64 固定 URL 与 SHA-256；
- BtbN FFmpeg fixed tag、fixed checksums SHA-256 与 LGPL shared asset；
- 便携 start/verify/backup 和回环 health smoke；
- runtime dependencies 安装到包内 `Lib/site-packages`；
- Python dependency/license 清单；
- package manifest、禁止文件扫描、SHA256SUMS 和确定性 ZIP；
- source ZIP、Git Bundle、source tree manifest 与恢复验证；
- pull request/手动/tag 三种 Release workflow；
- tag 发布时检查资产集合和 digest，再创建 immutable GitHub Release。

## 安全结论

构建只复制默认配置，不复制本地 `config.json` 或 `runtime.env`。包构建与验证拒绝 `.env`、Cookie、SQLite、日志、媒体、raw frame、payload、完整签名 URL、符号链接和运行目录。便携服务继续只允许 loopback。

## 发布门禁

P4A PR 必须先在 Windows runner 完成干净解压 verify、loopback health 和三类 FFmpeg smoke。合并后创建 annotated `v0.1.0` tag；tag workflow 的 Windows/source jobs 与 Release publish 全绿、资产完整后才算交付。

真实 recipient method/字段仍未形成经人工审查的公开 fixture，因此 contract 保持 `live_verified=false`，Issue #1 保持 Open。
