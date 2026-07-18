# 3. Python 单机总体架构

<img src="assets/media/image3.png" style="width:6.85in;height:3.99583in" />

图 1 Python 单机多直播间录播总体架构

## 3.1 核心组件

| **组件**              | **职责**                                                                                  |
|-----------------------|-------------------------------------------------------------------------------------------|
| AppState              | 创建数据库、RoomManager、JobRunner、Cookie 存储、事件总线；负责优雅停止                   |
| RoomManager           | 加载启用房间，带抖动地调度检查，限制启动并发，维护 RoomWorker 生命周期                    |
| RoomWorker            | 单个房间的故障域；持有状态检查、RecorderSupervisor、DouyinImClient、RecipientStateMachine |
| LiveStatusChecker     | 解析房间 URL/web_rid/room_id，获取在线状态、标题、封面和可用流                            |
| StreamResolver        | 根据 protocol/quality 选择 FLV/HLS 候选；断线后重新解析                                   |
| RecorderSupervisor    | 使用 asyncio 子进程启动 FFmpeg，消费 stdout/stderr，轮转、停止、ffprobe 校验              |
| DouyinImClient        | 独立 Cookie Jar 与 ttwid；WSS、心跳、ACK、gzip、外层帧、method 路由                       |
| RecipientStateMachine | 串行消费目标消息和连接生命周期，写入事件与 Active/Unknown 区间                            |
| DbWriter              | 单一 asyncio.Queue 顺序执行写事务，避免并发写锁和区间交叉闭合                             |
| JobRunner             | 从 jobs 表领取切片、转封装、校验、清理任务；有界并发，低于在线录制优先级                  |
| Web API               | 房间、场次、时间线、任务、媒体、设置、诊断；SSE 同源推送                                  |

## 3.2 进程与目录边界

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>douyin-recorder 主进程<br />
├── Uvicorn / FastAPI（固定 1 worker）<br />
├── RoomManager<br />
│ ├── RoomWorker(room-a) ── FFmpeg process<br />
│ ├── RoomWorker(room-b) ── FFmpeg process<br />
│ └── ...<br />
├── DbWriter（单写队列）<br />
└── JobRunner（有界后处理）<br />
<br />
config/ 实际配置模板与凭据引用<br />
userdata/ SQLite、任务、日志、缓存、临时状态<br />
records/ 永久原始媒体、导出文件、sidecar</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

目录职责必须固定：数据库、日志和任务不能混进 records/；Git 更新只能改变源码和模板，不能覆盖实际配置、数据库和录像。
