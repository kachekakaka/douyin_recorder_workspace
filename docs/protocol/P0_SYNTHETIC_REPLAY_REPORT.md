# P0 协议回放报告：strict-unknown-duplicate-reconnect-late

- Fixture 为合成数据：`true`
- Fixture 现场验证：`false`
- Contract 现场验证：`false`
- Contract SHA-256：`05fd47cdd0156d06fe730773ca98109383644cb6e64e8451c765928c77645410`
- 目标 method：`WebcastGroupLiveGiftRecipientRecommendMessage`
- 目标消息数：7
- 解码失败数：0
- 去重后事件数：6
- 重复帧数：1
- 迟到事件数：1
- 空 recipient 数：1
- IM 断线/重连：1/1

## ID 与 change_reason

- user_id：`["738291000001", "738291000002", "738291000003", "738291000004"]`
- open_id：`["OPEN-A", "OPEN-B", "OPEN-C", "OPEN-D", "OPEN-E"]`
- change_reason：`{"2": 2, "3": 1, "4": 1, "5": 1, "6": 1}`
- 未知目标字段号：`[]`

## common.create_time 到本机 receive_time 延迟

- 样本：6
- min/median/p95/max：20 / 45.0 / 60 / 60 ms

> 此报告只证明代码可重复处理合成 fixture；
> `live_verified=false` 时不得声称目标房间已经现场验证。
