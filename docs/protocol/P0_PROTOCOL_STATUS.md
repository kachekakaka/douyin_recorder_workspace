# P0 抖音协议验证状态

状态：**工程工具、最小 envelope、严格 reducer 和合成 fixture 已完成；目标直播间现场事实尚未验证。**

## 已实现

- `tools/douyin_wss_probe.py`：连接一个显式提供的、主机受限的抖音 `wss://` 地址。
- 独立实现的有界 protobuf wire inspector：提取 PushFrame、Response、Message 的 method、msg_id 和 payload bytes；支持 gzip 上限保护。
- 默认被动取证；ACK 和应用 heartbeat 必须由操作者显式启用。
- 每帧二进制、SHA-256、接收 UTC、`monotonic_ns`、`runtime_instance_id` 和 method 清单写入 `userdata/protocol-probes/`；该目录禁止提交。
- 目标 method payload 单独保存，并按 `app/douyin/contracts/provisional_v1.json` 尝试解码。
- `RecipientTimelineReducer` 实现 Waiting、Active、Unknown、去重、迟到事件和断线/重连严格语义。
- 合成 replay fixture 覆盖重复帧、无关 method、空 recipient、`open_id` 备用、IM 断线、重连不恢复旧对象和迟到事件。
- FastAPI `/api/status` 暴露 contract 哈希和 `protocol_live_verified=false`。

## 当前 contract

运行 contract：

```text
app/douyin/contracts/provisional_v1.json
```

它是独立编写的最小 wire contract，当前假定：

```text
common                         field 1
recipient_user_id              field 2, varint
change_reason_enum             field 3
extra map<string,string>       field 4
recipient_user_open_id         尚未确认
```

这些字段与公开技术资料中的当前 schema 快照一致，但**没有目标直播间用户授权现场抓包就不算完成验证**。因此 contract 必须保持 `live_verified=false`。

## 合成 fixture 结果

固定 fixture：

```text
tests/replay/fixtures/recipient-strict-unknown.synthetic.json
```

当前可重复结果：

- 目标消息 7 条；去重后 canonical event 6 条；
- 重复帧 1 条，只增加 duplicate count；
- 无关 method 1 条，被严格忽略；
- 空 recipient 进入 `Unknown(empty_recipient)`；
- 合成 contract 中 `recipient_user_id` 优先，只有 open ID 时使用 `openid:`；
- IM 断线进入 `Unknown(im_disconnected)`，重连本身不恢复上一位；
- 迟到事件 1 条，保存但不回滚当前状态；
- 合成 server→receive 延迟 20–60 ms，不代表真实网络。

完整报告：

```text
docs/protocol/P0_SYNTHETIC_REPLAY_REPORT.md
docs/protocol/P0_SYNTHETIC_REPLAY_REPORT.json
```

## 现场运行

先阅读 `docs/protocol/CAPTURE_RUNBOOK.md`。推荐命令：

```bash
python tools/douyin_wss_probe.py \
  --websocket-url-file userdata/private/wss-url.txt \
  --room-url "https://live.douyin.com/<web_rid>" \
  --duration 120
```

完整 WSS URL、Cookie、私有 raw frame、真实 payload、SQLite 和录像不得粘贴到 Issue、PR、聊天或仓库。

## 仍未解决的现场事实

1. 进入目标直播间后是否立即下发当前推荐收礼人，还是只在变化时下发；
2. 每次实际成员切换是否稳定下发，重复帧频率是多少；
3. 无推荐对象时 `recipient_user_id` 的真实 wire 值；
4. 当前消息是否存在可用 `recipient_user_open_id`，若有其字段位置是什么；
5. 重连后服务器是否重放当前对象；
6. `change_reason_enum` 在目标房间的真实分布；
7. `common.create_time` 与本机接收时间的真实延迟分布；
8. 当前最小 outer envelope、gzip、ACK 和 heartbeat 行为是否仍与现场一致。

在以上事实形成经脱敏、可回放、可审查的 fixture 前，不得宣称推荐收礼人功能完成，也不得进入依赖该事实的完整 P1 状态入库与自动录制闭环。
