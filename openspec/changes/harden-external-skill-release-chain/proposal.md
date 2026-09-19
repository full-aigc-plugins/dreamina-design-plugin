## Why

插件当前的外部技能副本可能与锁文件漂移，升级工作流也可能忽略发布事件中的不可变版本信息，导致市场版本无法对应到可复现的安装内容。

## What Changes

- 使用不可变 release tag、peeled commit SHA 和逐技能摘要约束受管技能。
- 使用声明式清单区分受管技能与插件专属技能，拒绝未声明的额外目录。
- 让技能发布事件携带 tag 与 commit，并由插件同步工作流校验后创建升级变更。
- 在发布前验证三端 manifest、技能完整性、版本与测试结果。

## Capabilities

### New Capabilities

- `immutable-skill-supply-chain`: 定义外部技能锁定、插件本地技能边界和事件驱动升级的可观察契约。

### Modified Capabilities

无。

## Impact

影响 `skills.lock.json`、`plugin-local-skills.json`、技能 vendor 工具、同步与检查工作流、三端 manifest、分发测试和发布流程；不改变插件面向用户的业务能力。
