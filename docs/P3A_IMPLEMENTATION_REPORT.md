# P3A 实施报告：持久化后处理任务与 recipient 区间导出

状态：**工程实现完成，等待远端 exact-head CI；`live_verified=false`。**
关联：Issue #15、协议事实 Issue #1
分支：`feature/p3a-postprocess-jobs`

## 已实现

- SQLite schema v6：postprocess jobs、attempts、outputs 和查询索引；
- recording session、媒体分片和 recipient intervals 的确定性 ExportPlan；
- Waiting、Active、Unknown 区间均显式导出；
- recipient 只以 SHA-256 出现在文件名和公开审计字段中；
- idempotent create、原子领取、retry、queued/running cancel 和启动恢复；
- 运行中 cancel 最终持久化为 canceled，不因 FFmpeg 非零退出误报 failed；
- 单进程 asyncio worker，单任务异常不终止 worker；
- FFmpeg concat/trim/stream-copy、双管道消费和 graceful → terminate → kill；
- `.writing` 成功后原子 rename，拒绝覆盖、符号链接和路径越界；
- jobs API 与同源网页任务控制；
- Linux/Windows CI 接入 P3A postprocess smoke。

## 本地验证

锁定依赖环境执行：

```text
pip check                         passed
repository baseline               passed
source boundary                   passed
compileall                        passed
Ruff                              passed
pytest                            passed
recipient reducer replay          passed
recipient SQLite replay           passed
JavaScript syntax                 passed
FFmpeg Supervisor smoke           passed
P1D Recording Session smoke       passed
P3A Postprocess smoke             passed
```

P3A smoke 实际先生成 2 个本地 lavfi MKV 分片，再创建 Waiting interval 导出任务。结果：schema v6、1 个 succeeded output、audio + video、无 `.writing` 残留。该 smoke 不访问抖音，也不保存真实直播媒体。

## 安全结论

- job、plan、API、网页和日志不包含完整签名流 URL、Cookie、WSS、raw payload 或 recipient 明文；
- 数据库只保存 records 相对路径、媒体 ID、recipient key hash 和受控错误码；
- FFmpeg stderr 仅被消费，不写入持久化 job 字段；
- 输出文件名不含 user ID/open ID；
- final output 使用 `-n` 和原子 rename，绝不静默覆盖；
- `live_verified=false` 与 Issue #1 门禁保持不变。

## 仍未解决

- 真实 recipient method/字段、空值、重复、切换与重连证据；
- HTTP Range、播放代理和公网管理；
- 正式 Windows release package 与 `v0.1.0` Release。
