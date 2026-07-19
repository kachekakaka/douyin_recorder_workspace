# P1B 实施报告：单房间 recipient 持久化与状态投影

状态：**实施中；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：GitHub Issue #7、协议事实 Issue #1  
分支：`feature/p1b-single-room-recipient-foundation`

## 已完成里程碑

- P1A 已通过 PR #4 合并到 `main`；
- P1B 分支从 `main@e2fee9a320529935e4d88f587b12f233d848121f` 创建；
- SQLite schema v4 已加入 recipient/session 审计字段和查询索引；
- schema v4 从空库及既有 migration 链递增验证；
- recipient canonical event、重复计数、迟到标记与 Waiting/Active/Unknown 区间采用同一 SQLite 写事务；
- 断线后立即进入 Unknown，重连本身不恢复旧 recipient；
- 跨 runtime 的 monotonic 值不直接比较；
- 只读 state/events/intervals API 不公开 raw payload、extra 或未知字段内容；
- 所有 64 位 ID 继续作为字符串。

## 当前批次继续完成

- 将合成 fixture 确定性回放到临时 SQLite，并校验 canonical events 与 intervals；
- 补齐 API 集成测试、源边界检查和 Windows 验证；
- 更新 README、实施文档与 Draft PR；
- 在远端 CI 全绿后给出本批可审查结论。

## 协议证据门禁

本批次只消费“已经解码且符合显式 contract 的事件”。合成事件用于验证事务和状态机，不能替代真实现场事实。以下条件满足前，不得把 P1B 描述为完整真实 recipient 接线：

- 普通交互浏览器实际建立当前 IM WSS；
- 观察到 `WebcastGroupLiveGiftRecipientRecommendMessage`；
- 字段号、空 recipient、重复、切换与重连行为经去标识人工审查；
- 形成可回放的最小真实 fixture；
- `app/douyin/contracts/provisional_v1.json` 的 `live_verified` 经审查后才可变更。

Issue #1 必须继续保持开启。