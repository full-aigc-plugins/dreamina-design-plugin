## Why

外部技能升级会更新 `skills.lock.json`，但不会同步仓库既有的 `skills/.upstream-commit` 包级来源 pin，导致分发测试拒绝正确的新快照。

## What Changes

- 当包级 marker 已存在且 lock 只有一个来源时，vendor update 同步写入 resolved SHA。
- 增加回归测试，确保 marker 与 lock 保持一致。

## Capabilities

### New Capabilities

None. This is an infrastructure compatibility fix and `skip_specs: true` is set.

### Modified Capabilities

None.

## Impact

影响 Dreamina Design 的 vendor 工具、测试和同步生成的 marker；不改变运行时 API。
