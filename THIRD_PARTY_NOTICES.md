# Third-Party Notices

本文件记录当前实际依赖与技术参考边界。发布包含第三方二进制或复制代码时必须继续补充完整 LICENSE、NOTICE、上游提交 SHA 和修改说明。

## 独立实现声明

以下协议、页面解析、状态机、房间与媒体监督源码由本项目独立编写，没有复制第三方生成 Proto、抓包样本或运行时代码：

- `app/douyin/protobuf_wire.py`
- `app/douyin/envelope.py`
- `app/douyin/recipient.py`
- `app/douyin/timeline.py`
- `app/douyin/replay.py`
- `app/douyin/probe.py`
- `app/douyin/live_page.py`
- `app/douyin/stream_resolver.py`
- `app/rooms/*`
- `app/media/ffmpeg.py`
- `app/api/rooms.py`

P0/P1A 只实现最小 outer envelope/wire inspection、显式 contract、受限公开页面解析和 FFmpeg 进程监督，不包含从第三方仓库复制的完整抖音 schema、签名算法或浏览器脚本。

## 参考但未复制

- `kachekakaka/bili_workspace`：参考 FastAPI + 静态网页、Windows 入口、CI、配置/运行数据/媒体目录边界和 Git Bundle 恢复流程。
- `qiaoruntao/douyin_contract`：参考 schema/method mapping 的生成思路和公开字段事实。基线审查未确认明确 LICENSE，因此没有复制其脚本、Rust 源码、`mapping.json` 或生成的 `.proto`。
- `Johnserf-Seed/f2`：Apache License 2.0。仅对照公开的 WSS outer envelope、ACK 与 heartbeat 行为；本项目实现重新编写，未复制文件。
- `biliup/biliup@adf6a1c03be9f777a76c8c501038c27f3d90a097`：MIT License。仅参考 `webcast/room/web/enter` / reflow 房间接口、`live_core_sdk_data/pull_data/stream_data` 结构、FLV/HLS 画质层级和相邻画质回退的工程思路。P1A 使用独立 Python 实现，没有复制其 Rust/Python 文件、固定 `ttwid`、A-Bogus、验证码/风控逻辑或原始响应日志行为。

## Python 与前端依赖

运行/开发依赖锁定在：

```text
requirements/runtime.lock
requirements/dev.lock
```

主要包括 FastAPI、Starlette、Uvicorn、httpx、websockets、protobuf、aiosqlite、pytest 与 Ruff。它们各自受上游许可证约束。当前仓库不提交这些包的源码或二进制；正式离线运行包/Release 必须生成第三方许可证清单。

## FFmpeg

FFmpeg/ffprobe 由操作者安装或后续 Release 单独提供。本仓库不提交 FFmpeg 二进制。发布打包时必须根据实际构建选项履行 LGPL/GPL 及相关第三方组件许可义务。
