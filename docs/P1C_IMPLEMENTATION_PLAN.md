# P1C 实施计划：交互式浏览器 IM 证据采集与去标识门禁

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：GitHub Issue #9、协议事实 Issue #1  
分支：`feature/p1c-interactive-im-evidence-gate`  
基线：`main@a7cf68eb96d14200fb8541108e632ca40559546c`

## 1. 目标

P1C 不继续扩展 synthetic contract，而是补齐普通交互浏览器现场证据的安全工具链：用户自行启动 Chrome 并完成任何必要的普通页面交互；工具只附加到回环 DevTools、验证授权直播间页面、被动观察 WSS 和二进制 frame，将原始证据保存在 Git 忽略的私人目录，并输出可审查的脱敏报告。

```text
用户主动启动普通 Chrome + loopback DevTools
    ↓
选择已授权 live.douyin.com/<id> 页面
    ↓
被动观察 allowlist WSS / binary frame
    ├─ private evidence bundle（本机、Git 忽略）
    └─ public sanitized report（无 Cookie/WSS query/raw/明文 ID）
    ↓
人工 approval manifest
    ↓ hash/contract/字段门禁通过
去标识 candidate fixture
```

## 2. 不可改变语义

1. 当前推荐收礼人只由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. 首条有效事件前为 Waiting；空 recipient、IM 断线及重连后尚无新事件时为 Unknown。
3. 推荐对象变化不得重启 FFmpeg 或切断媒体连接。
4. 不增加 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称、标题或画面位置 fallback。
5. 所有 64 位平台 ID 作为字符串。
6. `app/douyin/contracts/provisional_v1.json` 保持 `live_verified=false`，Issue #1 保持 Open，直到真实 fixture 通过人工审查。

## 3. 里程碑

### 3.1 Evidence bundle 与脱敏模型

- 私有输出目录必须位于 `userdata/protocol-probes`、`captures`、`private-fixtures` 或显式 `--allow-private-output` 的非仓库路径；
- 拒绝已存在目录、符号链接、仓库受跟踪目录和世界可写父目录；
- Unix 尽力设置 `0700` 目录和 `0600` 文件；
- frame 数、单帧大小、总字节和运行时长有上限；
- manifest 只记录 SHA-256、大小、序号、时间、method 和私有相对文件名；
- public report 不包含 Cookie、完整 WSS、query value、raw frame、payload 或 recipient 明文。

### 3.2 交互 Chrome/CDP 附加

- DevTools endpoint 仅允许回环 HTTP；
- 不启动 Chrome、不自动登录、不读取 Cookie storage；
- 只附加到最终 URL 精确匹配授权 `https://live.douyin.com/<id>` 的 page target；
- 只观察 `douyin.com` allowlist WSS；
- 完整 WSS URL 只在当前进程内存中短暂存在；
- 页面越界、DevTools 非回环、WSS 非 allowlist 或容量越界立即失败关闭；
- 不发送互动消息，不默认 ACK/heartbeat。

### 3.3 人工审批与 fixture 导出

- evidence manifest、contract SHA、文件 SHA 与 approval manifest 必须完全匹配；
- approval 必须显式列出允许导出的 target payload；
- 导出时重新解码并使用审批的字段映射；
- 真实 ID 使用稳定占位字符串或 hash，不输出明文；
- candidate fixture 明确标记 `live_verified=false` 和 `human_reviewed=true`；
- provisional contract 只能在后续独立 PR 中经人工审查更新。

### 3.4 测试与 CI

- synthetic CDP server / fake page target；
- synthetic WSS 创建和 frame 事件；
- output symlink/overwrite/repo path/size/count 边界；
- public report secret scan；
- approval hash mismatch、未审批 payload、明文泄漏拒绝；
- Python 3.12/3.13、Ruff、pytest、Windows、前端、FFmpeg smoke 与恢复资产。

## 4. 明确不做

- 自动登录、账号密码/二维码处理；
- 验证码或风控绕过；
- 发送礼物、弹幕、点赞或其他互动；
- 把私人 evidence bundle 上传到 GitHub 或 Actions；
- 从媒体、昵称或其他消息推断 recipient；
- 因没有观察到目标消息而关闭 Issue #1；
- 在本 PR 修改 `live_verified`；
- 多房间长期 RoomManager、无限录制、切片或公网管理。

## 5. 退出条件

P1C 工具链、synthetic 测试和 CI 可以完成并合并；只有现场真实目标消息经人工审批形成去标识 fixture 后，Issue #1 才能进入 contract 更新审查。若现场仍无目标消息，应记录 unknown，而不是猜测 offline 或永久不支持。
