# immutable-skill-supply-chain Specification

## Purpose
确保插件安装的外部技能来自不可变且可验证的发布，并允许少量明确声明的插件专属技能安全共存。
## Requirements
### Requirement: External skills are immutable and verifiable
插件 MUST 以 release tag、peeled commit SHA 和内容摘要锁定每个受管技能，检查命令 MUST 在 ref 移动、内容篡改、技能缺失或摘要不一致时失败。

#### Scenario: Managed skill content is unchanged
- **WHEN** 对已同步插件运行在线或离线完整性检查
- **THEN** 所有受管技能的来源、提交和内容摘要均与锁文件一致

#### Scenario: Managed content is tampered
- **WHEN** 已锁定的受管技能文件被修改、删除或其 tag 指向不同提交
- **THEN** 完整性检查以非零状态失败且不静默重写锁文件

### Requirement: Plugin-local skills are explicit
插件 MUST 通过声明式清单列出插件专属技能，vendor 更新 MUST 保留已声明的本地技能并拒绝未声明的额外技能目录。

#### Scenario: Declared plugin-local skill exists
- **WHEN** vendor 更新受管技能
- **THEN** 已声明的插件专属技能保持不变并继续被三端发现

#### Scenario: Undeclared skill appears
- **WHEN** `skills/` 中出现既不受锁管理也未列入本地清单的目录
- **THEN** 更新或检查命令失败并指出该目录

### Requirement: Upgrade events identify exact source state
技能升级事件 MUST 携带 release tag 和 peeled commit SHA，插件同步流程 MUST 校验二者与锁定来源匹配后才可更新并创建升级变更。

#### Scenario: Valid release event arrives
- **WHEN** 受信任的技能源发布新 release 并提供匹配的 tag 与 commit
- **THEN** 插件生成只包含预期技能、锁文件和版本更新的可审查升级变更

#### Scenario: Event commit does not match tag
- **WHEN** 事件中的 commit 与远端 tag 的 peeled SHA 不一致
- **THEN** 同步流程失败且不修改插件技能或锁文件

### Requirement: Release surfaces remain consistent
发布前验证 MUST 确认 Codex、ZCode、Kimi manifest、市场版本、插件 tag 和测试结果属于同一发布。

#### Scenario: Release candidate is consistent
- **WHEN** 发布候选通过分发检查
- **THEN** 三端 manifest 版本一致，受管技能检查通过，插件 tag 与 GitHub Release 可对应到同一 commit

