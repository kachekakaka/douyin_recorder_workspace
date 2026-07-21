# P4A 实施计划：Windows 便携包与 v0.1.0 Release

状态：**开始实施；真实目标消息仍未现场验证，`live_verified=false`。**  
关联：协议事实 Issue #1  
分支：`feature/p4a-release-packaging`  
基线：`main@c62768351f32fa044cfb27b83775bdc5438a3054`

## 1. 目标

在已经完成 P0–P3A 工程能力的基础上，交付可在 Windows x64 解压后本机运行、可验证、可恢复的 `v0.1.0`：

```text
main exact commit
    ├─ Windows portable ZIP
    │   ├─ Python 3.13 embeddable runtime
    │   ├─ locked runtime dependencies
    │   ├─ FFmpeg / ffprobe
    │   ├─ application source and web assets
    │   ├─ portable start / verify / backup
    │   └─ manifest / licenses / SHA-256
    └─ source ZIP + Git Bundle + source manifest
        ↓
clean extraction / restore / smoke
        ↓
annotated tag v0.1.0
        ↓
GitHub Release assets
```

## 2. 不可改变语义

1. `app/douyin/contracts/provisional_v1.json` 必须保持 `live_verified=false`。
2. Issue #1 保持 Open；Release 不宣称真实 recipient 协议已经验证。
3. 不把 `.env`、Cookie、SQLite、日志、媒体、raw frame、真实 payload、完整签名 URL或运行目录打入 Release。
4. Windows 包仍只允许 loopback 监听；不增加管理员认证假象或公网绑定。
5. 不增加 Redis、Celery、PostgreSQL、多机器或远程控制。
6. 第三方二进制和 Python 依赖必须有来源、版本、许可证和 SHA-256 记录。

## 3. Windows 便携包

Release ZIP 根目录至少包含：

```text
start.bat
verify.bat
backup.bat
README.md
THIRD_PARTY_NOTICES.md
app/
web/
config/*.default
requirements/runtime.lock
scripts/windows/
runtime/python/
runtime/ffmpeg/bin/ffmpeg.exe
runtime/ffmpeg/bin/ffprobe.exe
licenses/
windows-manifest.json
windows-SHA256SUMS.txt
```

要求：

- 便携版 `start.bat` 只使用包内 Python 与 FFmpeg；
- 首次运行只从 `.default` 文件生成本地配置，不覆盖已有用户配置；
- 运行数据只写 `userdata/`、`records/` 和 `logs/`，三者不进入构建资产；
- 解压目录可包含空格和非 ASCII 字符；
- 不依赖系统 Python、pip、Git 或 FFmpeg；
- 包内 Python 依赖按 `requirements/runtime.lock` 固定版本安装；
- 清理 `__pycache__`、测试缓存、开发依赖和构建临时文件。

## 4. Release 构建与验证

新增可复现构建工具和 GitHub Actions workflow：

1. 校验 tag 与项目版本一致；
2. 下载固定版本 Python embeddable ZIP 和 FFmpeg x64 ZIP；
3. 校验上游 SHA-256；
4. 构建包内 `site-packages`；
5. 生成 Python 依赖许可证/元数据清单；
6. 生成 package manifest 和 SHA-256 清单；
7. 扫描禁止文件与秘密载体；
8. 在新的干净目录解压；
9. 运行便携版 `verify.bat`；
10. 启动服务并验证 loopback `/api/status`；
11. 运行 FFmpeg Supervisor、Recording Session 和 postprocess smoke；
12. 构建 source ZIP 与 Git Bundle，并在新目录恢复验证；
13. 上传 Actions artifact；tag 触发时创建 GitHub Release。

所有下载地址、版本和 SHA-256 必须集中在可审查的 release lock/manifest 中，不能使用漂移的 `latest` URL。

## 5. Release 资产

```text
douyin-recorder-v0.1.0-windows-x64.zip
douyin-recorder-v0.1.0-source.zip
douyin-recorder-v0.1.0-source.bundle
windows-manifest.json
source-tree-manifest.json
windows-SHA256SUMS.txt
source-SHA256SUMS.txt
python-dependencies.json
THIRD_PARTY_NOTICES.md
```

Release notes 明确：

- 已完成单进程多房间自动录制和区间导出；
- 真实 `WebcastGroupLiveGiftRecipientRecommendMessage` 仍为 provisional/unverified；
- 仅本机 loopback 使用；
- 不提供公网管理、账号登录或风控绕过。

## 6. 测试与完成定义

- package manifest 确定性、路径安全、禁止文件扫描；
- 版本/tag 不一致时失败；
- 缺失许可证、SHA 或二进制时失败；
- Windows 解压后 `verify.bat` 全通过；
- `/api/status` loopback health smoke；
- 三类 FFmpeg smoke；
- source ZIP/Git Bundle 恢复后 tree SHA 一致；
- Python 3.12/3.13、Ruff、pytest、前端、Windows 和恢复资产 CI 继续全绿；
- P4A PR 合并后才能创建 `v0.1.0` annotated tag；
- Release workflow 全绿且所有资产存在、digest 可核验后，才宣称 v0.1.0 已交付。

## 7. 提交顺序

1. `docs: define P4A Windows release plan`
2. `feat: add deterministic release manifests`
3. `feat: build Windows portable package`
4. `test: verify extracted Windows package`
5. `ci: publish v0.1.0 release assets`
6. `docs: finalize P4A release review`

每个可验证里程碑完成后立即 push，禁止 force push。
