# P1B recipient 协议证据门禁

## 当前状态

```text
target_method=WebcastGroupLiveGiftRecipientRecommendMessage
live_verified=false
Issue #1=open
P1B transaction_projection=implemented
real_im_integration=not_verified
```

P1B 第一批已经完成已解码事件的事务持久化、Waiting/Active/Unknown 投影、只读 API 和 synthetic SQLite replay。这一工程完成状态不改变真实协议门禁。

## 可以实施

- 对合成或已经解码的 `DecodedRecipientEvent` 做事务持久化；
- 验证 Waiting、Active、Unknown、重复、迟到、断线和重连状态语义；
- 提供不含 raw payload 的只读审计 API；
- 使用临时 SQLite 做确定性数据库回放；
- 继续执行公开、无登录、被动、脱敏的协议观察；
- 开发只写本机私人目录的交互式证据采集工具，但不得提交真实载荷。

## 不得推断

- 合成 fixture 通过不等于真实目标消息已验证；
- 公开网页、媒体流或无登录 headless 成功不等于 IM WSS 已验证；
- 一次或多次未观察到 WSS 不等于永久不支持；
- 不得从昵称、礼物、弹幕、连麦成员、OCR、人脸、声纹、标题或画面位置推断 recipient；
- 不得因为重连成功而恢复断线前 recipient；
- 不得因为 SQLite replay 与 reducer 一致而修改 `live_verified`；
- 不得把 synthetic contract 的 open ID 字段事实写回 provisional live contract。

## 解除门禁的最小证据

1. 用户授权的真实直播间在普通交互浏览器中建立当前 IM WSS；
2. 观察到目标 method，且原始数据只在本地私人探测目录短暂保存；
3. 对字段号、ID 编码、空 recipient、重复、切换、重连和时间字段做人工审查；
4. 生成去标识、最小、可回放 fixture，不包含 Cookie、完整 WSS、真实 recipient 明文或其他隐私；
5. 在新 PR 中更新 Issue #1、协议状态文档和 contract，并由人工审查是否可设 `live_verified=true`。

## 当前仍缺少的事实

- 普通交互浏览器实际使用的 IM WSS endpoint 与握手上下文；
- `WebcastGroupLiveGiftRecipientRecommendMessage` 当前真实字段号；
- `recipient_user_id`、`recipient_user_open_id` 和空 recipient 的 wire 表示；
- `msg_id` 重复、成员切换、断线和重连后的实际消息序列；
- `change_reason_enum` 取值和服务端时间单位；
- 服务端事件时间到本机接收时间的延迟分布；
- 经去标识、人工审查、可回放的最小真实 fixture。

在上述证据完成前，P1B 能力必须描述为“已解码 recipient 事件的事务投影基础”，而非“真实抖音 recipient 自动接入完成”。Issue #1 保持开启，`app/douyin/contracts/provisional_v1.json` 保持 `live_verified=false`。
