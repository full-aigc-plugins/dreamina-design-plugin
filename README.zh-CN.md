# Codex Dreamina Design 插件

<img src="assets/logo.png" alt="Dreamina Design Logo" width="128">

> 面向 Dreamina CLI 图片与视频工作流的 Codex 兼容基础。

[English](README.md) | [简体中文](README.zh-CN.md)

## 状态与定位

**实现已完成；两项 runtime 门禁等待 owner 决定。**

```text
创作意图 -> Prompt 契约 -> 实时发现 CLI 能力
        -> 明确批准生成 -> 单次提交
        -> 按 submit_id 查询 -> 验证/下载产物
```

计划 7 个任务全部实现，由 192 个离线测试覆盖（无网络、无 `dreamina` 二进制、无付费调用）。
计划 13 行完成门禁中 11 行为有实测证据的 `PASS` —— 含 Skill snapshot parity 固定到上游
`full-aigc-skills/dreamina-skills@373bf7f`、严格 TRACE 13/13、plugin 校验、
凭证零命中，以及**公开 marketplace 安装后经 `codex debug prompt-input` 验证发现全部 14 个 Skill**。

最后两行门禁是析取：

```text
read_only_runtime_contract = observed or explicitly blocked
paid_canary = separately approved or NOT_RUN
```

两者当前都处于第二个析取项（`blocked` / `NOT_RUN`）——本机未安装 `dreamina` 二进制，
也未执行过付费生成。`--plan-gate` 对正是这个状态报 `plan_gate = SATISFIED`：

```text
$ python3 scripts/validate_distribution_v7.py --plan-gate     # exit 0
```

### 收尾这两项门禁 —— 二选一

**路径 (a) —— 接受 `explicitly blocked` / `NOT_RUN` 分支：**

```text
$ python3 scripts/record_gate_decision.py accept-blocked \
      --approver <您的名字> --reason "<为何延后 live 证据>"
$ python3 scripts/validate_distribution_v7.py --plan-gate     # 确认 exit 0
```

**路径 (b) —— 升级到 `observed` / `APPROVED`：**

```text
$ python3 scripts/unlock_runtime_gates.py probe     # 装好并授权 dreamina 后
# 然后填写 docs/verification/account-readiness.md
$ python3 scripts/unlock_runtime_gates.py record-canary \
      --submit-id <真实ID> --approver <您的名字> --observed "<观察描述>"
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates   # 确认 exit 0
```

两条路径都是一条命令，且都必须由**您**执行 —— 工具只负责记录决定，从不替您做决定。
`probe` 在 CLI 缺失时拒绝运行，`record-canary` 拒绝占位 submit ID，
`accept-blocked` 拒绝占位 approver。两条命令都不会伪造证据。

完整细节：[授权决定记录](docs/verification/authorization-decision.md) ·
[离线证据](docs/verification/offline.md) ·
[Skill 发现](docs/verification/skill-discovery.md) ·
[严格 TRACE](docs/verification/skill-trace.md) ·
[CLI runtime](docs/verification/dreamina-cli-runtime.md)

## 边界

- 已安装的 `dreamina` CLI 负责认证和远程 API。
- 模型、分辨率、比例、时长和必填参数来自当前 CLI help/schema。
- 生成会消耗会员权益或积分，必须明确批准。
- 网页端首次视频生成等前提只报告，不绕过。
- 结果不确定时按 `submit_id` 查询，不盲目重试提交。

## 文档

- [Architecture](docs/Codex-Dreamina-Design-Plugin-Architecture.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Architecture.zh_CN.md)
- [Technical solution](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.zh_CN.md)
- [设计规格](docs/superpowers/specs/2026-09-11-codex-dreamina-design-plugin-design.md)
- [实施计划](docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md)

## 上游技能迁移

事实源迁移已完成，当前仓库为 `full-aigc-skills/dreamina-skills`。插件打包必须固定经过验证的上游提交，不能复制后再独立修改 Skill 正文。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
