# P3A 实施计划：持久化后处理任务与 recipient 区间导出

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #15、协议事实 Issue #1  
分支：`feature/p3a-postprocess-jobs`  
基线：`main@d492e0e881f4fe39692e1d0c979554f87e6a4b88`

## 1. 目标

P3A 在已完成的 recording session、媒体分片和 recipient interval 基础上建立单进程、可恢复、可审计的后处理任务闭环：

```text
completed recording session
    + media_files
    + Waiting / Active / Unknown intervals
        ↓
确定性 ExportPlan
        ↓
持久化 postprocess job
        ↓
FFmpeg concat / trim / stream copy
        ↓
.writing → 原子 rename
        ↓
可审计 export output
```

## 2. 不可改变语义

1. recipient 只由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. Waiting、Active、Unknown 均是显式区间，不从昵称、画面或其他消息补全。
3. recipient 变化不得重启 FFmpeg 或切断录制媒体连接。
4. 所有 64 位平台 ID 作为字符串。
5. 完整签名流 URL、Cookie、WSS、raw payload 和 recipient 明文不得进入 job、API、日志或 Actions。
6. `live_verified=false`，Issue #1 保持 Open。
7. 只使用 FastAPI、SQLite 和 asyncio；不引入 Redis、Celery、PostgreSQL、多机器或公网管理。

## 3. 里程碑

### 3.1 Schema v6 与持久化模型

通过递增 migration 增加：

- postprocess jobs：queued/running/succeeded/failed/canceled；
- attempts：启动、结束、return code、停止阶段和脱敏错误码；
- outputs：interval、源媒体 ID、相对路径、大小和 SHA-256；
- 原子领取、idempotency key、retry/cancel 和查询索引；
- 应用启动时把遗留 running 标记 interrupted/failed，允许显式 retry。

数据库不保存绝对路径、完整流 URL、recipient 明文或 FFmpeg 完整 stderr。

### 3.2 确定性 ExportPlan

- 只接受已结束且有 closed/verified/recovered 媒体的 recording session；
- 同 session recipient intervals 按开始时间和 ID 排序；
- interval 边界裁剪到录制媒体总时间范围；
- 选择与 interval 相交的媒体分片；
- Waiting/Unknown 也可导出并保留状态/reason；
- 输出名只使用 interval ID、状态和 recipient key SHA-256 前缀；
- 相同 session/interval/参数产生稳定 idempotency key；
- 不把 recipient user ID/open ID 或原始 key 写入输出文件名。

### 3.3 FFmpeg 执行器与 Worker

- 使用 `asyncio.create_subprocess_exec(*argv)`，禁止 shell；
- concat 清单只引用已验证的 records 根目录内媒体；
- 使用 `-ss`、`-t`、`-c copy`、`-n`；
- 输出先写 `.writing`，成功后 fsync/原子 rename；
- 拒绝既有目标、符号链接、路径越界和危险文件名；
- stdout/stderr 并发消费并脱敏；
- cancel 使用 graceful → terminate → kill，最终状态必须为 canceled；
- 单进程 worker 原子领取，单任务失败不影响后续任务；
- retry 创建新 attempt，不覆盖既有成功 output。

### 3.4 API 与网页

```text
POST /api/recording/sessions/{session_id}/actions/create-export
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/actions/retry
POST /api/jobs/{job_id}/actions/cancel
```

网页显示任务状态、区间、输出文件、重试和取消。浏览器写操作继续受 Host/Origin/Referer 同源边界保护。

### 3.5 测试与 CI

- schema v1-v5 → v6 migration 和 checksum；
- ExportPlan Active/Waiting/Unknown、无媒体、越界、跨分片和确定性；
- queue 原子领取、duplicate/idempotency、retry、cancel、startup recovery；
- 运行中 cancel 最终为 canceled；
- 输出覆盖、符号链接、路径越界和隐私扫描；
- 两段 lavfi MKV 的真实 concat/trim/copy smoke；
- Python 3.12/3.13、Ruff、pytest、Windows、前端、既有 FFmpeg/recording smoke 和恢复资产。

## 4. 明确不做

- 转码质量优化、GPU 编码或水印；
- HTTP Range、媒体代理或公网播放；
- 多机器任务调度；
- 自动修改真实 recipient contract；
- 把真实直播媒体或 recipient 明文提交到 GitHub。

## 5. 提交顺序

1. `docs: define P3A postprocess implementation plan`
2. `feat: add postprocess schema v6`
3. `feat: build deterministic recipient export plans`
4. `feat: run persistent postprocess jobs`
5. `feat: expose postprocess job APIs and web controls`
6. `test: cover postprocess recovery cancellation and privacy`
7. `docs: finalize P3A review and limitations`

每个可验证里程碑完成后立即 push，禁止 force push。
