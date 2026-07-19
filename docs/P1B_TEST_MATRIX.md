# P1B 测试矩阵

## 迁移

- 空库直接迁移到 schema v4；
- v1、v2、v3 依次迁移到 v4；
- migration checksum 保持稳定；
- recipient event、interval 和 session 查询索引存在；
- 所有平台 ID 字段使用 TEXT。

## 事务状态机

- session 开始生成 Waiting；
- 首条有效事件关闭 Waiting 并开启 Active；
- 相同 recipient 的新 canonical event 不重复开启区间；
- 相同 dedup key 只更新 duplicate_count 和 last_received_at_ms；
- recipient 切换关闭旧 Active 并开启新 Active；
- 空 recipient 关闭 Active 并开启 Unknown(empty_recipient)；
- IM 断线关闭当前区间并开启 Unknown(im_disconnected)；
- 重连本身保持 Unknown；
- 重连后首条有效事件开启 Active；
- 迟到事件保存为 is_late=1，不回滚当前状态；
- 跨 runtime 的 monotonic 值不直接比较；
- session 结束关闭开放区间和 session。

## API

- state、events、intervals 只读接口；
- 非法 room_key、未知房间、无 session 和分页边界；
- 64 位 ID 保持字符串；
- API 不含 raw_payload_json、extra_json、unknown_fields_json 或真实 payload；
- GET 不放宽现有 Host 安全边界。

## 确定性数据库回放

- 相同 fixture 在两个空临时数据库中生成相同的公开 state/events/intervals 报告；
- 报告不含数据库绝对路径、runtime 随机值、raw payload 或隐私字段；
- replay 工具失败时返回非零退出码；
- CI 和 Windows `verify.bat` 实际执行 replay。