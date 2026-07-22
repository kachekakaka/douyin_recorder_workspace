# v0.1.0 最终实施报告

## 1. 报告口径

本报告只使用 GitHub 远端可验证事实：commit、PR、tag、Release、GitHub Actions、Release assets 及其 SHA-256。聊天描述、本地未提交文件和未 push 内容不作为完成证据。

需要区分两个 SHA：

- **发布源 main / annotated tag 解引用 commit**：`b8056289ea9c18504675ff1dd43df84a977b2436`；
- **最终报告 PR 合并后的 main**：由 PR #20 合并结果产生，在合并完成后的最终交付输出中记录。报告文件本身不能预先猜测尚未产生的 merge SHA。

## 2. 正式发布身份

| 字段 | GitHub 实际值 |
|---|---|
| 版本 | `0.1.0` |
| annotated tag | `v0.1.0` |
| tag object SHA | `567d8d9dc6559226c76625cd4cc0df040b1b903c` |
| tag 解引用 commit | `b8056289ea9c18504675ff1dd43df84a977b2436` |
| 发布源 main SHA | `b8056289ea9c18504675ff1dd43df84a977b2436` |
| Release ID | `357811419` |
| Release 名称 | `Douyin Recorder v0.1.0` |
| 发布时间 | `2026-07-22T06:21:41Z` |
| draft | `false` |
| prerelease | `false` |
| Release target_commitish | `main` |
| source main CI run | `29896339087` |
| tag promotion run | `29896396873` |
| Release workflow run | `29896406871` |
| 独立资产审计 run | `29897165335` |
| contract | `live_verified=false` |
| 协议追踪 | Issue #1，保持 Open |

`target_commitish=main` 是 GitHub Release 元数据中的符号值；Release workflow 已验证 annotated tag object，且 tag 解引用 commit 精确等于 `b8056289ea9c18504675ff1dd43df84a977b2436`。

## 3. 阶段 PR 与 merge 证据

| PR | 阶段 | 最终状态 | merge SHA |
|---:|---|---|---|
| #4 | P1A 单房间媒体基础 | merged | `e2fee9a320529935e4d88f587b12f233d848121f` |
| #8 | P1B recipient 持久化与严格状态投影 | merged | `a7cf68eb96d14200fb8541108e632ca40559546c` |
| #10 | P1C 交互式 IM 证据采集门禁 | merged | `e7e98117a574465061ebe07f4ca623d4ae101b32` |
| #12 | P1D 单房间录制 Session 闭环 | merged | `dd6a8b39659ab5d891cd03db1e31c727dec5a7ba` |
| #14 | P2A 多房间 RoomManager | merged | `d492e0e881f4fe39692e1d0c979554f87e6a4b88` |
| #16 | P3A 持久化后处理与区间导出 | merged | `c62768351f32fa044cfb27b83775bdc5438a3054` |
| #17 | P4A Windows 便携包与 Release 基础设施 | merged | `0d4c41162a9c4dc4f5f4645ce3112e170aaf7d86` |
| #19 | 不可变 annotated tag 提升门禁 | merged | `b8056289ea9c18504675ff1dd43df84a977b2436` |
| #20 | 最终实施报告与资产复核 | Draft/本报告 PR | 合并后由 GitHub 产生，不预写 |

## 4. CI 与发布工作流

### 4.1 发布源 main CI

Run `29896339087` 在发布源 main commit 上成功完成。门禁包括：

- Python 3.12；
- Python 3.13；
- Windows `verify.bat`；
- Frontend JavaScript syntax；
- repository baseline；
- source boundary；
- `compileall`；
- Ruff；
- pytest；
- deterministic recipient reducer replay；
- deterministic SQLite replay；
- FFmpeg Supervisor smoke；
- Recording Session smoke；
- postprocess smoke；
- Git Bundle/source ZIP 构建和恢复验证。

### 4.2 tag promotion

Run `29896396873` 只接受同仓库、`main`、push 事件且结论为 success 的精确 CI SHA。它确认：

- 项目、`app.__version__`、release lock 均为 `0.1.0`；
- `target_method=WebcastGroupLiveGiftRecipientRecommendMessage`；
- `live_verified=false`；
- Issue #1、#18 当时均为 Open；
- 创建的是 annotated tag；
- 未删除、覆盖、force push 或移动已有 tag。

### 4.3 正式 Release workflow

Run `29896406871` 的三个 job 均为 success：

1. `Build and verify Windows x64 package`；
2. `Build and restore source assets`；
3. `Publish GitHub Release`。

Publish job 进一步通过：最终资产集合、两个 SHA-256 清单、annotated tag object、tag commit、Release 创建和机器审计。

### 4.4 发布后独立资产复核

PR #20 的临时只读审计 workflow 在新的 Ubuntu 24.04 runner 临时目录下载正式 Release 资产。Run `29897165335` 成功执行：

- Release ID、tag、draft/prerelease 和恰好 9 个非空资产的 API digest 检查；
- `windows-asset-SHA256SUMS.txt`；
- `source-SHA256SUMS.txt`；
- Windows ZIP 新目录解压；
- `tools/release_package.py verify`，包括 manifest 文件集合、逐文件 bytes/SHA-256、禁止文件、`live_verified=false`、依赖清单和 FFmpeg license 文件；
- Windows manifest 的 `source_commit` 等于 tag commit；
- source ZIP 文件集合、bytes 和 SHA-256 对照 `source-tree-manifest.json`；
- Git Bundle verify、clone、checkout 和 Git tree 精确恢复到 tag commit。

临时审计 workflow 在写入本报告后从分支删除，不进入最终树。

## 5. 正式 Release assets

| 资产 | bytes | GitHub digest |
|---|---:|---|
| `douyin-recorder-v0.1.0-source.bundle` | 638856 | `sha256:97cbe06f43e7d241d36bac2b607931835bfa023750921752f307e5e7a2467ce5` |
| `douyin-recorder-v0.1.0-source.zip` | 368885 | `sha256:ee3758927e1f45222e80eeb8431d86051598ab87cc502550afed4df54cdc837d` |
| `douyin-recorder-v0.1.0-windows-x64.zip` | 197099060 | `sha256:13a66bccdf8990db948ed69bf76c06b03ef476483dbe298a069323070002a523` |
| `python-dependencies.json` | 4778 | `sha256:a8aaad6dfec2231437966706ed9a6a3d4bfe76b41e3261ab08768e28e23e14fd` |
| `source-SHA256SUMS.txt` | 295 | `sha256:9bf80b4b6bdf6f1030842d98f1ce14f90b4541d7aa6df60d141e49c3c5d422d8` |
| `source-tree-manifest.json` | 34454 | `sha256:b3fdb73cb474ae30506ab76e0ae25cf7792ea07d3c93a2d76c3772760107e981` |
| `windows-asset-SHA256SUMS.txt` | 106 | `sha256:3eb85a15642eff22149b7aeac70432370cece734a55bb6c2423e5869c2c8f0a2` |
| `windows-manifest.json` | 155711 | `sha256:ed81a93836f3c7878afa99c71143963e4c2ba29b0db4b72cbcfd3e9237aa98a0` |
| `windows-SHA256SUMS.txt` | 98012 | `sha256:b35b87a13d117ca2292de2e814493242c77f7aaf4e8e0d079c580fbd6dcbd3c0` |

所有资产均存在、非空，GitHub API digest 为 SHA-256。Windows/source 清单和下载后复核均通过。

## 6. Windows x64 发布验证

Windows ZIP 包含 Python 3.13.14 embeddable runtime、锁定 runtime dependencies，以及固定到 GyanD `7.1` full build 的 FFmpeg/ffprobe。release lock 记录：

- FFmpeg provider：`GyanD/codexffmpeg`；
- asset：`ffmpeg-7.1-full_build.zip`；
- license：`GPL-3.0-or-later`；
- Python license：`Python-2.0`。

已验证：

- 包内 `start.bat`、`verify.bat`、`backup.bat`；
- 不依赖系统 Python、pip 或 FFmpeg；
- manifest 文件集合与 ZIP 解压内容一致；
- manifest 每项 bytes/SHA-256 一致；
- package 内部 `windows-SHA256SUMS.txt` 通过；
- 外部 Windows ZIP digest 与 `windows-asset-SHA256SUMS.txt` 一致；
- embedded Python import、loopback health、FFmpeg Supervisor、Recording Session、postprocess smoke；
- `python-dependencies.json` 非空；
- `THIRD_PARTY_NOTICES.md`、Gyan build notice、FFmpeg notice 和 FFmpeg archive license 文件存在；
- 包中没有 `.env`、Cookie、SQLite、日志、媒体、raw frame、真实 payload、完整签名 URL、Actions artifact ZIP 或符号链接。

## 7. Source ZIP / Git Bundle 恢复验证

已验证：

- source ZIP、Git Bundle、source tree manifest 的 SHA-256 清单通过；
- `git bundle verify` 成功；
- 从 Bundle 在新目录 clone 成功；
- detached checkout 到 `b8056289ea9c18504675ff1dd43df84a977b2436`；
- 恢复后的 `HEAD` 与 tag commit 一致；
- 恢复后的 Git tree 与发布源 tree 一致；
- source ZIP 的文件集合、bytes 和 SHA-256 与 `source-tree-manifest.json` 一致；
- 恢复仓库通过 repository baseline。

## 8. 数据库与当前 API

最终 SQLite schema version：`6`。

### 基础状态

```text
GET /healthz
GET /readyz
GET /api/status
```

### 房间

```text
GET    /api/rooms
POST   /api/rooms
GET    /api/rooms/{room_key}
PATCH  /api/rooms/{room_key}
POST   /api/rooms/{room_key}/actions/check
POST   /api/rooms/{room_key}/actions/enable
POST   /api/rooms/{room_key}/actions/disable
```

### recipient 审计

```text
GET /api/rooms/{room_key}/recipient-state
GET /api/rooms/{room_key}/recipient-events
GET /api/rooms/{room_key}/recipient-intervals
```

### recording

```text
POST /api/rooms/{room_key}/actions/start-recording
POST /api/rooms/{room_key}/actions/stop-recording
GET  /api/rooms/{room_key}/recording
GET  /api/rooms/{room_key}/recording/sessions
GET  /api/rooms/{room_key}/recording/segments
```

### manager

```text
GET  /api/manager/status
POST /api/manager/actions/reconcile
GET  /api/rooms/{room_key}/worker
```

### postprocess jobs

```text
POST /api/recording/sessions/{session_id}/actions/create-export
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/actions/retry
POST /api/jobs/{job_id}/actions/cancel
```

## 9. 已通过代码、CI、Release 和资产验证的功能

- 单机、单进程、loopback-only FastAPI 管理服务；
- SQLite migration、WAL、backup 和 schema v6；
- 房间 CRUD、规范化、检查、启停和脱敏媒体候选；
- FFmpeg Supervisor、segment CSV、无覆盖和进程停止边界；
- 单房间 Recording Session 与 recipient 生命周期；
- 多房间 RoomManager、enabled room worker、并发检查 semaphore、offline 阈值和错误退避；
- 持久化 postprocess job、retry/cancel/recovery、recipient interval 导出；
- Windows x64 portable package；
- source ZIP / Git Bundle 可恢复资产；
- immutable annotated tag、正式 GitHub Release 和 9 个校验资产。

## 10. 仅通过 synthetic fixture 验证的 recipient 语义

以下语义由合成 fixture、reducer replay、SQLite replay、单元/集成测试验证：

- 首条有效事件前 `Waiting(waiting_first_event)`；
- 有 recipient 时 `Active`；
- 空 recipient 时 `Unknown(empty_recipient)`；
- IM 断线时 `Unknown(im_disconnected)`；
- 重连本身不恢复断线前 recipient；
- duplicate 计数；
- late event 保存但不回滚当前状态；
- recipient 切换关闭旧 interval 并开启新 interval；
- recipient 变化不重启或切断 FFmpeg；
- 64 位 ID 始终按字符串处理。

这些结果证明实现对**显式输入事件**的确定性行为，不证明当前抖音现场协议字段已经正确接入。

## 11. 尚未通过真实现场证据验证的协议事实

截至本报告，仍未形成可公开提交的、去标识、人工审查、可回放的真实目标消息 fixture。以下事实未被真实现场证据确认：

- 普通交互浏览器当前是否稳定建立目标 IM WebSocket；
- 真实 method 是否仍精确为 `WebcastGroupLiveGiftRecipientRecommendMessage`；
- 当前 Protobuf 字段号与嵌套结构；
- recipient 的 user ID / open ID 字段；
- 空 recipient 的真实编码；
- duplicate、切换和重连的现场序列；
- `change_reason_enum` 的真实含义；
- 服务端时间字段及时间单位；
- 接收延迟分布；
- 地区、登录状态和风控差异。

因此：

```text
live_verified=false
```

Issue #1 必须继续保持 Open，且不得宣称真实 recipient 协议已经验证。

## 12. 安全边界

- 管理服务只允许 loopback，拒绝 `0.0.0.0`、局域网和公网管理；
- HTTP Host 和浏览器同源写操作有独立限制；
- 公开直播页和媒体 URL 有 scheme/host/port/redirect/SSRF 边界；
- 完整签名流 URL 只存在于有界进程内存和 FFmpeg argv；
- API、SQLite、网页和日志不返回 Cookie、Authorization、完整 WSS、完整签名 URL、raw payload 或 recipient 明文；
- 私人 evidence、运行数据和媒体位于 Git 忽略目录；
- 输出拒绝覆盖、符号链接和目录越界，使用 `.writing` 后原子 rename；
- 不包含 Redis、Celery、PostgreSQL、多机器或公网管理；
- 不使用 OCR、人脸、声纹、昵称、礼物、弹幕、连麦或画面位置推断 recipient。

## 13. 后续建议范围

1. 继续通过 Issue #1 的交互式、用户授权、回环 Chrome evidence 流程收集去标识真实证据；
2. 先人工审查 manifest/hash，再在独立 PR 中更新 fixture 或 contract；
3. 只有真实字段、空值、重复、切换和重连均可回放后，才讨论 `live_verified`；
4. 保持 v0.1.0 tag 与 Release 不可变，后续修复使用新版本 tag；
5. 不在 v0.1.x 范围引入 Redis、Celery、PostgreSQL、多机器或公网管理。

## 14. 结论

`v0.1.0` 的代码、CI、annotated tag、正式 Release、Windows/source 构建、9 项资产、SHA-256、Windows manifest、source ZIP 与 Git Bundle 恢复均已通过 GitHub 和独立发布后审计验证。

正式发布完成不改变协议事实：recipient 状态机与持久化语义目前仍以 synthetic fixture 为验证基础，真实抖音目标协议尚未完成现场证据验证，`live_verified=false`，Issue #1 保持 Open。
