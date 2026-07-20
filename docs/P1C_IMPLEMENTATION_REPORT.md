# P1C 实施报告：交互式浏览器 IM 证据采集与去标识门禁

状态：**工程实现完成，等待远端 PR CI 与 Review；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：Issue #9、协议事实 Issue #1  
分支：`feature/p1c-interactive-im-evidence-gate`

## 已实现

- `EvidenceBundle` 私人目录、权限、覆盖、符号链接和世界可写父目录边界；
- frame 数、单帧大小、总字节和运行时长上限；
- raw frame、target payload、manifest 和 approval template 只写 Git 忽略目录；
- WSS 仅接受默认 TLS 端口、无凭据、`douyin.com` allowlist；
- public report 仅保留 host、path/url SHA-256、query key、method 计数和去标识字段统计；
- DevTools endpoint 只接受 `http://127.0.0.1:<port>` 或 `http://[::1]:<port>`；
- 精确选择授权 `https://live.douyin.com/<id>` page target；
- 被动观察 WSS 和二进制 frame，不读取 Cookie storage，不发送 ACK/heartbeat/互动；
- approval manifest、contract SHA 和 payload SHA 全匹配后，才导出去标识 candidate fixture；
- recipient 别名强制使用 `recipient-NNN`，输出固定 `live_verified=false`；
- synthetic CDP/WSS、secret scan、容量和 approval mismatch 测试。

## 安全结论

完整 WSS、query value、raw frame、target payload 和 recipient 明文不会进入 public report、日志、Issue、PR 或 Actions artifact。私人 evidence 目录由 `.gitignore` 排除，工具拒绝仓库内非批准目录。

## 仍未解决

- 普通交互浏览器现场是否建立当前 IM WSS；
- 是否观察到 `WebcastGroupLiveGiftRecipientRecommendMessage`；
- 当前字段号、空 recipient、open ID、重复、切换、重连、reason 和延迟分布；
- 经人工审查、可公开提交的最小真实 fixture。

因此 provisional contract 不变，Issue #1 继续开启。P1C 工具链完成不等于真实 recipient 自动接入完成。
