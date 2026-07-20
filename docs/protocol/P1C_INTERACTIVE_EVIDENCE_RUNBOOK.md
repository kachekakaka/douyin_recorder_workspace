# P1C 交互式 Chrome IM 证据采集 Runbook

## 安全前提

- 只用于用户明确授权的抖音直播间；
- 用户自行启动普通 Chrome 并完成正常页面交互；
- 工具只附加到回环 DevTools，不启动登录、不读取 Cookie storage、不发送互动；
- 私人 evidence 目录不得上传 GitHub、Actions、Issue、PR 或公开网盘；
- `live_verified=false`，观察到目标消息也不能自动修改 contract。

## 1. 启动普通 Chrome

Windows 示例：

```bat
"%ProgramFiles%\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-address=127.0.0.1 ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%LOCALAPPDATA%\douyin-recorder-evidence-profile"
```

Linux 示例：

```bash
google-chrome \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.local/share/douyin-recorder-evidence-profile"
```

只打开一个目标页面，例如：

```text
https://live.douyin.com/79907888978
```

工具要求 target URL 精确匹配该授权页面。不要把 DevTools 端口监听到局域网或公网。

## 2. 被动采集

```bash
python tools/douyin_interactive_evidence.py \
  --room-id 79907888978 \
  --devtools http://127.0.0.1:9222 \
  --duration 180 \
  --output userdata/protocol-probes/79907888978-20260720
```

输出目录必须不存在。默认边界：

```text
max frames       500
max frame bytes  8 MiB
max total bytes  64 MiB
max duration      900 s
```

私人目录包含：

```text
frames/*.bin
 target-payloads/*.bin
manifest.json
approval-template.json
public-report.json
```

`manifest.json` 只记录相对路径、大小、SHA-256、时间和 method；`public-report.json` 进一步删除私人文件名，只保留 WSS host、path/url hash、query key、method 计数、recipient hash、空值、reason 和延迟统计。

## 3. 人工审查

审查必须在本机私人目录完成：

1. 核对 `manifest.json` 与所有 target payload 的 SHA-256；
2. 人工确认 payload 确实来自 `WebcastGroupLiveGiftRecipientRecommendMessage`；
3. 确认字段号、ID 编码、空 recipient、change reason 和时间字段；
4. 把 `approval-template.json` 复制为 `approval.json`；
5. 将 `human_reviewed` 改为 `true`；
6. 在 `approved_payloads` 中显式列出允许导出的相对文件；
7. 对每个 recipient key 的 SHA-256 使用 `recipient-001` 形式的稳定别名。

approval 文件不得包含 recipient 明文。

## 4. 导出去标识 candidate fixture

先创建 Git 忽略的私人输出目录：

```bash
mkdir -p private-fixtures
```

```bash
python tools/export_recipient_evidence_fixture.py \
  --evidence userdata/protocol-probes/79907888978-20260720 \
  --approval userdata/protocol-probes/79907888978-20260720/approval.json \
  --name reviewed-79907888978-candidate \
  --output private-fixtures/reviewed-79907888978-candidate.json
```

导出器会重新检查：

- manifest SHA-256；
- contract SHA-256；
- 每个 payload SHA-256；
- approval 显式列表；
- recipient hash 到 `recipient-NNN` 别名；
- 输出不存在，拒绝覆盖。

candidate fixture 固定包含：

```text
human_reviewed=true
live_verified=false
contains_real_user_data=false
```

导出器默认只允许仓库内 `userdata/protocol-probes`、`captures` 或 `private-fixtures`。仓库外输出必须显式增加 `--allow-private-output`。

它仍不能直接进入公开仓库。先由人工确认不含真实 ID、payload、Cookie 或完整 WSS，再在独立 contract Review PR 中决定是否生成最小公开 replay fixture。

## 5. 未观察到目标消息

`completed-no-target` 或没有 WSS 只能记录为 unknown。可能原因包括：目标房间没有发生切换、页面游客上下文、观察窗口、风控或当前 WSS 实现变化。不得将其解释为永久不支持，也不得关闭 Issue #1。
