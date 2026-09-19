## Context

参见 `proposal.md`。插件包含从独立技能源 vendoring 的内容，也包含只对插件运行时有意义的 harness；现有锁、工作流与测试在这些边界上不一致。

## Goals / Non-Goals

**Goals:**

- 让每次技能同步都可复现、可拒绝漂移、可审查。
- 让插件专属技能有明确边界并被测试覆盖。
- 让三端 manifest、市场条目、tag 与 Release 形成一致证据链。

**Non-Goals:**

- 不把插件 harness 迁入公共技能仓。
- 不在本变更中修改技能业务语义或第三方服务行为。
- 不重写已发布 tag。

## Decisions

1. 锁文件同时保存 tag、peeled commit SHA 和逐技能摘要。仅保存 tag 无法检测 tag 移动，仅保存 commit 又缺少人类可读的发布语义。
2. 使用 `plugin-local-skills.json` 作为本地技能唯一允许清单。目录推断容易把意外文件误当成受支持能力。
3. producer 仅在 `release.published` 后发送 dispatch；consumer 从 payload 获取 tag 与 SHA，并再次向远端校验。相比监听 `main`，这能确保升级只基于正式不可变发布。
4. vendor 工具对更新采用先校验后写入，失败不留下部分状态；CI 同时执行 mutation tests、离线检查和在线 ref 校验。

## Risks / Trade-offs

- [技能源缺少 dispatch secret] → 工作流明确失败，保留手工传入 tag/SHA 的安全更新路径，禁止退回移动分支。
- [历史测试硬编码旧版本或技能数量] → 更新为当前 manifest/清单驱动断言，并保留一致性检查。
- [上游技能存在断链] → 在技能源修复并发布新版本，再更新锁；不直接在插件受管目录永久打补丁。

## Migration Plan

1. 引入 vendor v2 与本地技能清单，并以当前正式技能源重新同步。
2. 修复分发测试与上游技能缺陷，完成在线/离线校验。
3. 更新三端版本，提交并等待 CI。
4. 创建不可变 tag 与 Release，再更新市场目录。
5. 如发布失败，删除尚未公开的候选 tag 或继续发布补丁版本；不得移动已公开 tag。
