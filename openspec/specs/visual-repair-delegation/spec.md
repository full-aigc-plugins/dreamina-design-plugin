# visual-repair-delegation Specification

## Purpose
当修复工作被委托给 worker 子代理时，保证其上下文、指令粒度与验证职责都被明确约束，使独立评审的有效性不被削弱，也避免 worker 自行发起新一轮付费循环。
## Requirements
### Requirement: worker 必须使用全新空上下文
被委托修复工作的 worker SHALL 以全新空上下文启动，MUST NOT 继承编排者或此前轮次的历史。委托方 MUST NOT 通过复制当前会话历史的方式创建工作上下文。

#### Scenario: worker 以空上下文启动
- **WHEN** 编排者委托一个修复轮次
- **THEN** worker 在新上下文中启动，仅持有本轮显式提供的材料与指令

#### Scenario: 禁止继承历史
- **WHEN** 编排者准备委托内容
- **THEN** 委托材料不包含此前轮次的对话历史、历史评分或编排者的私密推理

### Requirement: 委托指令只描述高层目标
委托内容 SHALL 描述要达成的目标与可比对材料，MUST NOT 包含逐条修复任务清单或编排者的主观判断。编排者 SHALL 提供目标素材、当前状态与用户诉求，由 worker 自行判断如何收敛差距。

#### Scenario: 委托只给目标与材料
- **WHEN** 编排者构造委托指令
- **THEN** 指令包含目标素材、当前状态与用户诉求，且不包含逐条修复步骤或主观评价

#### Scenario: 不把评审清单当作任务单派发
- **WHEN** 上一轮评审产出了差距清单
- **THEN** 该清单不直接作为 worker 的任务单派发，以保证独立评审与实现判断相互独立

### Requirement: 验证职责属于编排者
worker SHALL 只负责实现，MUST NOT 承担自我验收。编排者 SHALL 在 worker 返回后自行执行可运行性验证与状态检查，并在需要时修正加载、方向或装配类问题后再进入下一轮。

#### Scenario: worker 不自我验收
- **WHEN** 委托内容被编写
- **THEN** 内容明确要求 worker 只实现、不执行验收，并说明验收由编排者负责

#### Scenario: 编排者修正非视觉问题
- **WHEN** worker 返回后发现资源加载、方向或装配类缺陷
- **THEN** 编排者修正这些缺陷，但不借该步骤调整视觉方向

### Requirement: worker 不得自行发起视觉循环
委托内容 SHALL 明确禁止 worker 自行启动或推进视觉循环、自行发起付费生成。循环的发起、轮次推进与付费决策 SHALL 只由编排者在既有门禁下进行。

#### Scenario: 禁止 worker 递归发起循环
- **WHEN** 委托内容被编写
- **THEN** 内容明确告知 worker 不得自行启用视觉循环或提交付费生成

#### Scenario: 同一轮内不重复付费
- **WHEN** worker 在一轮委托内完成实现
- **THEN** 该轮不因 worker 的判断而产生额外付费提交

### Requirement: 实现角色与评审角色必须分离
同一轮次内的实现与评审 MUST NOT 由同一上下文兼任。评审 SHALL 由独立的、未参与本轮实现的评审者执行，并 SHALL 保持与本轮实现无关的干净输入。

#### Scenario: 实现与评审不由同一上下文兼任
- **WHEN** 一轮实现完成并需要评审
- **THEN** 评审由独立评审者执行，其输入不包含本轮实现过程与编排者的主观意见

#### Scenario: 角色冲突被拒绝
- **WHEN** 试图让本轮实现者对自己的产物出具评审结论
- **THEN** 该评审结论不被接受为有效验收证据

