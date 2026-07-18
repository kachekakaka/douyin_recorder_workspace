# 8. SQLite 数据模型与文件边界

## 8.1 SQLite 运行规则

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>PRAGMA journal_mode = WAL;<br />
PRAGMA synchronous = NORMAL;<br />
PRAGMA foreign_keys = ON;<br />
PRAGMA busy_timeout = 5000;</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

- 数据库固定为 userdata/douyin_recorder.db；禁止放在网络共享文件系统。

- 一个 Uvicorn worker、一个 DbWriter 连接负责所有写事务；读接口使用短连接或只读连接。

- 迁移使用递增 schema_version 和 app/db/migrations/\*.py；启动时幂等执行并写迁移日志。

- 正式备份优先使用 SQLite backup API；停机文件复制时必须同时考虑 db、-wal、-shm。

## 8.2 核心表

| **表**              | **用途**                    | **关键字段**                                                       |
|---------------------|-----------------------------|--------------------------------------------------------------------|
| rooms               | 房间配置                    | room_key, room_url, enabled, quality, protocol, poll_interval      |
| sessions            | 一次开播到下播              | room_key, external_room_id, title, started/ended, status           |
| recipient_events    | 去重后的不可变推荐事件      | dedup_key, msg_id, recipient IDs, times, payload, duplicate_count  |
| recipient_intervals | waiting/active/unknown 区间 | recipient_key, start/end, status, reason, event IDs                |
| media_files         | 原始媒体分片索引            | path, actual times, PTS, codec_signature, continuity_group, status |
| media_gaps          | 媒体不可用区间              | gap_start, gap_end, reason                                         |
| recorder_events     | 组件生命周期与错误          | component, event_type, code, detail, created_at                    |
| jobs                | 切片、转封装、校验、清理    | job_type, status, attempts, not_before, payload, last_error        |
| exports             | 导出产物                    | job_id, interval_id, source media IDs, path, size, hash            |
| audit_logs          | 管理写操作                  | action, object_type/id, remote_addr, created_at                    |

## 8.3 必须落地的约束

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>CREATE UNIQUE INDEX uq_active_session_per_room<br />
ON sessions(room_key)<br />
WHERE status = 'active';<br />
<br />
CREATE UNIQUE INDEX uq_open_interval_per_session<br />
ON recipient_intervals(session_id)<br />
WHERE ended_at_ms IS NULL;<br />
<br />
CREATE UNIQUE INDEX uq_event_dedup<br />
ON recipient_events(session_id, dedup_key);</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

raw event 不直接修改；需要修复时间线时，从 canonical events 重建 intervals，并把重建版本、原因和操作者写入审计记录。

