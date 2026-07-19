# P1B recipient 协议证据门禁

## 当前状态

```text
target_method=WebcastGroupLiveGiftRecipientRecommendMessage
live_verified=false
Issue #1=open
```

## 可以实施

- 对合成或已经解码的 `DecodedRecipientEvent` 做事务持久化；
- 验证 Waiting、Active、Unknown、重复、迟到、断线和重连状态语义；
- 提供不含 raw payload 的只读审计 API；
- 使用临时 SQLite 做确定性数据库回放；
- 继续执行公开、无登录、被动、脱敏的协议观察。

## 不得推断

- 合成 fixture 通过不等于真实目标消息已验证；
- 公开网页、媒体流或无登录 headless 成功不等于 IM WSS 已验证；
- 一次或多次未观察到 WSS 不等于永久不支持；
- 不得从昵称、礼物、弹幕、连麦成员、OCR、人脸、声纹、标题或画面位置推断 recipient；
- 不得因为重连成功而恢复断线前 recipient。

## 解除门禁的最小证据

1. 用户授权的真实直播间在普通交互浏览器中建立当前 IM WSS；
2. 观察到目标 method，且原始数据只在本地私人探测目录短暂保存；
3. 对字段号、ID 编码、空 recipient、重复、切换、重连和时间字段做人工审查；
4. 生成去标识、最小、可回放 fixture，不包含 Cookie、完整 WSS、真实 recipient 明文或其他隐私；
5. 更新 Issue #1、协议状态文档和 contract 后再评审 `live_verified=true`。

在上述证据完成前，P1B PR 必须明确其能力是“事务投影基础”，而非“真实抖音 recipient 自动接入完成”。