# visual-quality-loop Specification

## Purpose
为图片、视频和后续 DCC 预览提供宿主无关、可恢复、可审计且费用受控的目标驱动视觉质量闭环：先锁定不可变目标，每轮产出绑定产物与评价证据的闭合回执，评审由宿主提供独立模型完成，付费重试受精确指纹与信用额度上限约束。
## Requirements
### Requirement: 视觉目标必须锁定并可验证
系统 SHALL 将用户授权的目标素材保存为带来源、内容哈希、媒体元数据和授权范围的 `VisualTargetReceipt`；进入循环后不得静默替换目标。

#### Scenario: 锁定目标
- **WHEN** 用户从授权目录提供目标图片或视频证据
- **THEN** 系统验证文件稳定性并返回不可变目标回执

#### Scenario: 替换目标
- **WHEN** 已开始的循环请求使用不同目标哈希
- **THEN** 系统拒绝该轮次并要求显式创建新目标版本

### Requirement: 每轮必须产生持久化回执
系统 SHALL 为每轮生成或评价保存 `VisualRoundReceipt`，绑定目标、请求指纹、`submit_id`、产物哈希、评价器身份、门禁结果、费用和下一动作。

#### Scenario: 客户端中断后恢复
- **WHEN** 客户端在一轮完成后断开并重新连接
- **THEN** 系统从持久化回执恢复，不重新提交已存在的付费请求

### Requirement: 单轮生成后只自动评价
系统 SHALL 在首轮产物可用后执行可信媒体检查和结构化语义评价，但 MUST NOT 因评价失败自动创建第二个付费请求。

#### Scenario: 首轮未达标
- **WHEN** 首轮评价未通过且没有激活的重试 allowance
- **THEN** 系统返回 `awaiting_approval` 或 `replan_required`，供应商提交次数保持不变

### Requirement: 图片循环重试受不可扩张 allowance 约束
图片视觉循环 SHALL 默认最多包含首轮和一次预报价重试；重试只能使用 allowance 中已绑定的请求指纹、费用上限和修复指令。

#### Scenario: 合法的一次重试
- **WHEN** 首轮失败且 allowance 明确包含一次重试请求
- **THEN** 系统可以消费该预留并提交一次重试

#### Scenario: 尝试第三轮
- **WHEN** 默认策略下客户端请求第三次付费提交
- **THEN** 系统拒绝并要求新的报价和审批

### Requirement: Judge 必须宿主无关且证据化
系统 SHALL 通过 `JudgePort` 接受结构化评审，不得在领域层写死 Codex 子代理；评价回执必须记录 provider、model、时间和提示规范版本。

#### Scenario: 不同客户端提供 Judge 结果
- **WHEN** Codex、Claude Code、ZCode、Kimi 或其他 MCP 客户端提交满足同一 Schema 的评价
- **THEN** 系统按同一门禁和回执规则处理

### Requirement: 视频评价必须包含可信关键帧证据
视频轮次 SHALL 复用可信媒体适配器产生的首尾帧、关键帧或联系表证据，并保持测量门禁与语义判断相互独立。

#### Scenario: 语义高分但媒体损坏
- **WHEN** Judge 给出通过结论但可信媒体门禁检测到不可读帧或错误编码
- **THEN** 系统不得接受该轮次

### Requirement: 停滞和终止不得扩大费用
系统 SHALL 在连续两轮无显著改善或同一缺陷重复出现时进入重新规划；终止 SHALL 阻止后续提交，但不得把不支持的远端取消报告为成功。

#### Scenario: 停滞
- **WHEN** 连续两轮得分改善不足或同一阻塞项重复
- **THEN** 系统进入 `replan_required` 且不自动提交新任务

#### Scenario: 终止已提交任务
- **WHEN** 用户终止循环而供应商不支持远端取消
- **THEN** 系统标记循环已停止、保留原 `submit_id` 供对账，并明确远端任务未被取消

