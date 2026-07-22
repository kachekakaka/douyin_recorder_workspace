# Diagnostics and Recovery Baseline

## Purpose

定义 v0.1.1 稳定性增强阶段的运行诊断边界。

## Diagnostic report

允许记录：

- application version;
- schema version;
- runtime status;
- component health;
- error category;
- timestamps.

禁止记录：

- Cookie;
- token;
- 完整签名 URL;
- raw payload;
- raw frame;
- recipient 明文身份信息。

## Recovery checks

发布后维护工具应支持：

1. SQLite integrity check;
2. backup restore verification;
3. clean runtime validation;
4. failure injection tests.

## Protocol boundary

所有 recipient 语义继续遵守：

```
WebcastGroupLiveGiftRecipientRecommendMessage
live_verified=false
Issue #1 open
```

真实协议验证必须通过人工证据流程完成。
