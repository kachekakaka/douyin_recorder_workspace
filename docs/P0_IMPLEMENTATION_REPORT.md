# P0 实施报告

状态：**P0 工程实现与 CI 已完成；授权现场预检已执行，但未建立 IM transport，协议事实验证继续进行；不进入完整 P1。**

关联：GitHub Issue #1、Pull Request #2

## 1. 已完成工程交付

### Web 与运行骨架

- FastAPI、Uvicorn 单 worker、同源静态网页；
- `/healthz`、`/readyz`、`/api/status`；
- P0 无认证期间强制 loopback-only；
- 实际配置从 `.default` 创建并只递归补缺，不覆盖用户值；
- `runtime_instance_id` 隔离跨重启 monotonic 时间。

### SQLite

- `journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout=5000`；
- 校验和 migration 历史；
- 单有序写连接；
- active session、open interval 和 event dedup 唯一约束；
- SQLite backup API 和 `integrity_check` 恢复验证。

### 协议验证工具

- 独立实现的最小 Protobuf wire inspector；
- PushFrame、Response、Message、gzip 解压上限；
- 只路由 `WebcastGroupLiveGiftRecipientRecommendMessage`；
- 显式 provisional contract 和 SHA-256；
- Waiting/Active/Unknown、重复、迟到、断线、重连严格 reducer；
- 合成 fixture 与确定性报告；
- 显式 WSS URL probe；
- HTTP 直播页安全预检；
- Chrome/CDP 被动观察浏览器实际 WSS，报告不保存 Cookie、签名 query、原始帧或 recipient 明文 ID。

### Windows、CI 与恢复

- `start.bat`：准备 Python 环境、配置和服务；
- `update.bat`：只允许干净 `main`，执行 `git pull --ff-only`；
- `verify.bat`：依赖、源码、Ruff、pytest、replay、前端语法和 Git Bundle 恢复；
- `backup.bat`：源码与运行数据双轨备份；
- GitHub Actions 覆盖 Python 3.12/3.13、Windows 和前端；
- CI 生成并验证 Git Bundle、源码 ZIP 和 SHA-256。

## 2. 自动测试范围与结果

覆盖：

- 设置模板与 loopback 安全边界；
- SQLite migration、WAL、外键、唯一约束和一致性备份；
- envelope gzip round-trip；
- 64 位 recipient ID 始终为字符串；
- open ID 只作 user ID 缺失时的备用；
- IM 断线立即 Unknown，重连不恢复上一位；
- 同一 runtime monotonic 不倒退，跨 runtime 不直接比较；
- 合成 fixture 确定性、重复和迟到行为；
- FastAPI health/readiness/status/static page；
- 直播页预检不泄露完整 WSS query。

GitHub Actions run `29669746416` 全部通过：

```text
Python 3.12                       success
Python 3.13                       success
Ruff                              success
pytest                            success
确定性 fixture replay             success
Git Bundle / source ZIP 恢复      success
原生 JavaScript 语法              success
Windows verify.bat                success
```

CI 生成的恢复资产包含完整 Git Bundle、源码 ZIP、manifest 和 SHA-256；临时克隆、`git fsck` 和仓库基线校验均通过。

## 3. 授权测试目标与结果

用户提供并授权用于 P0 现场测试的抖音号/直播路径标识：

```text
73504089679
```

GitHub 的 `P0 Douyin live preflight` run `29669746415` 成功执行：

1. 无登录 HTTP 直播页预检；
2. 无登录 Chrome/CDP WSS 观察 60 秒；
3. 只上传脱敏 JSON 报告，保留 3 天；
4. 未上传网页正文、Cookie、完整签名 URL、原始帧、payload 或 recipient 明文 ID。

实际结果：

- HTTP 200、无重定向，页面中存在 FLV/HLS、stream data、room/webcast 等直播结构标记；
- Chrome 正常启动且页面已加载；
- 没有观察到浏览器创建抖音 WSS；
- 二进制帧、envelope、method 和目标消息数量均为 0；
- `transport_live_verified=false`；
- `target_live_verified=false`。

该结果不能自动判定房间永久不支持目标消息；可能是测试时未开播、无登录/自动化环境降级、需要额外交互或游客上下文，或者页面在观察窗口内未建立 WSS。完整脱敏记录见 `docs/protocol/live-probes/2026-07-19-73504089679.md`。

## 4. 仍未解决的协议事实

- 进入直播间后是否立即下发当前推荐对象；
- 每次真实成员切换是否稳定下发；
- 重复帧与 `msg_id` 分布；
- 空 recipient 的真实 wire 表示；
- `recipient_user_open_id` 是否存在及字段号；
- 重连后是否重放当前对象；
- `change_reason_enum` 分布；
- `common.create_time` 延迟；
- 当前 ACK、heartbeat、outer envelope 与 gzip 行为。

在目标消息形成经脱敏、人工确认、可回放的 fixture 前，`live_verified` 必须保持 `false`，Issue #1 不关闭，也不进入依赖这些事实的完整 P1 自动录制闭环。
