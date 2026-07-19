# AGENTS.md

本文件对所有开发窗口、自动化代理和人工开发者生效。

## 不可改变的业务语义

1. 当前对象仅由 `WebcastGroupLiveGiftRecipientRecommendMessage` 更新。
2. 首条有效事件前、空 recipient、IM 断线和重连后未收到新事件时必须为 `Unknown`。
3. 推荐对象变化不得重启 FFmpeg 或媒体连接。
4. 不得增加 OCR、人脸、声纹、礼物、弹幕、连麦成员、昵称或画面位置 fallback。
5. 所有 64 位平台 ID 必须按字符串传输和存储。

## 实施边界

1. 后端业务逻辑使用 Python；媒体由受监督的 FFmpeg/ffprobe 完成。
2. 前端为静态 HTML/CSS/原生 JavaScript ES Modules，无运行时 Node/npm 依赖。
3. 首版固定一个 Uvicorn worker，禁止多 worker 重复创建 RoomManager/FFmpeg。
4. SQLite 数据库必须位于本机 `userdata/`，不能放 SMB/NFS。
5. 真实 Cookie、`.env`、SQLite、日志、录像、导出、备份和解压运行时不得提交。

## Git 工作规则

1. 每次工作先从最新 `main` 创建 `feature/*` 或 `agent/*` 分支。
2. 小步提交；每个可验证里程碑都要 commit + push。
3. PR 合并前必须通过 compileall、Ruff、pytest、fixture replay 和前端 JS 语法检查。
4. 禁止无条件 `push --force`；禁止在未备份前运行 `git clean -fdx` 或 `reset --hard`。
5. 任何新需求同时更新架构基线/实施清单/测试，不能只存在于聊天记录。

## 第三方代码

- `biliup/biliup` 为 MIT，可在保留许可和版权声明的前提下借鉴或复用。
- `Johnserf-Seed/f2` 为 Apache-2.0，复用时必须履行 LICENSE/NOTICE 与修改说明义务。
- `qiaoruntao/douyin_contract` 当前基线审查未发现明确 LICENSE；在许可确认前只能作为技术参考，不得直接复制其脚本、源码或生成文件。
- 所有实际复用必须登记到 `THIRD_PARTY_NOTICES.md`。

## 开发顺序

- P0 工程骨架已合并；真实目标消息的现场事实仍由 Issue #1 跟踪，运行 contract 必须保持 `live_verified=false`。
- 可以实施不依赖目标消息已验收的 P1A 房间/媒体基础。
- 在 Issue #1 的真实字段、空值、重复和重连事实形成经审查 fixture 前，不得宣布 P1B recipient 接线或完整 P1 单房间闭环完成。
- 只实施当前 Issue/批次；不得顺手扩大到多平台、多机或推断 fallback。
