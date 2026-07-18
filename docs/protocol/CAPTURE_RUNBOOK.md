# P0 单房间抖音 WSS 现场取证手册

本手册只用于你有权访问和记录的公开直播间或自有/已授权直播间。P0 工具的目标是回答协议事实，不是绕过平台权限、批量抓取用户信息或建立隐藏数据集。

## 1. 取证边界

现场工具只做以下事情：

1. 连接一个由操作者显式提供的当前 `wss://` 地址；
2. 保存收到的原始二进制帧及其 SHA-256；
3. 从最小外层 envelope 中列出 `Message.method`；
4. 对目标 method 单独保存 payload，并按显式 contract 尝试解码；
5. 生成不含完整 WSS URL、Cookie 和请求头的报告。

它不会自动发现签名地址，不会登录账号，不会从浏览器窃取 Cookie，也不会把私有抓包上传到 GitHub。

## 2. 私有目录

在仓库根目录创建：

```text
userdata/private/
├── wss-url.txt
├── cookie.txt          # 仅在现场连接确实需要时
└── headers.json        # 可选，禁止 Cookie/Host/Origin 等危险覆盖
```

这些目录已被 `.gitignore` 排除。仍应限制文件权限，不要通过聊天、Issue、PR 或普通日志发送内容。

`wss-url.txt` 只放一行完整地址。不要把签名 URL 直接放在命令参数里，因为它可能进入 shell history 或进程列表。

## 3. 从浏览器取得当前地址

1. 使用正常浏览器打开目标直播间；
2. 打开开发者工具的 Network / WS；
3. 刷新页面，找到直播 IM WebSocket；
4. 复制当前完整 Request URL 到 `userdata/private/wss-url.txt`；
5. 记录页面直播间 URL，但在报告中只保存去掉 query/fragment 的地址。

抖音前端、签名和 WSS 主机可能随版本变化。不要把某个 URL 硬编码到源码或文档。

## 4. 第一次运行：被动取证

```bash
python tools/douyin_wss_probe.py \
  --websocket-url-file userdata/private/wss-url.txt \
  --room-url "https://live.douyin.com/<web_rid>" \
  --duration 120 \
  --max-frames 2000
```

默认不发送应用 ACK 和 heartbeat。这样不会在协议尚未确认时主动发送可能错误的帧。

默认输出：

```text
userdata/protocol-probes/probe-<UTC>/
├── frames/
│   └── 000001.bin
├── target-payloads/
├── frames.jsonl
├── target-events.jsonl
└── report.json
```

`report.json` 只保存主机名、脱敏房间 URL、计数、method 分布、contract 哈希和字段摘要；不保存完整签名 WSS URL、Cookie 或请求头。

## 5. ACK 与 heartbeat

若被动连接在收到 `need_ack` 后很快关闭，并且现场帧已经确认当前 outer envelope 行为，可显式启用：

```bash
python tools/douyin_wss_probe.py \
  --websocket-url-file userdata/private/wss-url.txt \
  --room-url "https://live.douyin.com/<web_rid>" \
  --duration 120 \
  --send-ack \
  --send-heartbeat
```

P0 中：

- ACK 使用 PushFrame `logId`，并把 Response `internalExt` 放入 ACK 的 `payloadType`；
- heartbeat 作为 WebSocket ping data 发送；
- 两者均默认关闭；
- 现场不一致时立即关闭对应选项并保留原始帧供审查。

## 6. Cookie 和额外请求头

仅在当前连接确实需要时使用：

```bash
python tools/douyin_wss_probe.py \
  --websocket-url-file userdata/private/wss-url.txt \
  --cookie-file userdata/private/cookie.txt \
  --header-file userdata/private/headers.json \
  --room-url "https://live.douyin.com/<web_rid>"
```

`headers.json` 必须是字符串到字符串的 JSON 对象。工具拒绝其中的 `Cookie`、`Host`、`Origin`、`Connection`、`Content-Length` 和 `Sec-WebSocket-Key`，防止重复或危险覆盖。

## 7. 现场验证目标

需要在成员真实切换和 IM 重连场景中回答：

1. 进入直播间后是否立即下发当前推荐收礼人，还是只在变化时下发；
2. 每次切换是否稳定出现 `WebcastGroupLiveGiftRecipientRecommendMessage`；
3. 是否存在重复 `msg_id` 或相同 payload 重发；
4. 无推荐对象时 `recipient_user_id` 的真实值；
5. 当前 payload 是否存在可用的 `recipient_user_open_id` 字段；
6. 重连后是否重放当前对象；
7. `change_reason_enum` 的真实分布；
8. `common.create_time` 与本机接收时间的延迟分布；
9. outer envelope 字段、gzip、ACK 和 heartbeat 是否仍与当前现场一致。

## 8. 从私有抓包生成可提交 fixture

不要直接提交 `frames/*.bin` 或 `target-payloads/*.bin`。先在本地完成：

1. 删除 Cookie、签名、完整 WSS URL、真实昵称、头像和不必要的用户字段；
2. 将所有真实平台 ID替换为明确的合成字符串或合成整数；
3. 保留会影响状态机的顺序、空值、重复和断线事件；
4. 在 fixture 根节点标记来源和处理方式；
5. 人工复核没有真实个人数据；
6. 运行 replay 与仓库秘密扫描。

现场事实尚未完成前，运行 contract 必须保持：

```json
{
  "live_verified": false
}
```
