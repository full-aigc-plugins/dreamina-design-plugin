# Canvas Runtime — design 插件的第二条执行轨

> 版本：0.7.0 · 状态：已接线（`dreamina_submit_image` / `dreamina_submit_video` 默认走本轨；`runtime: "legacy"` 显式回退旧路径）

## 背景

`dreamina` 二进制 2026-11 后停止维护。本插件自 0.7.0 起双轨并存：

| 轨 | 二进制 | 状态 | 审批语义 |
|---|---|---|---|
| `canvas`（默认） | `dreamina-canvas` | 生产路径 | **报价绑定**：先对已保存草稿取实时报价，审批指纹含该金额 |
| `legacy`（显式） | `dreamina`（冻结） | 兼容路径 | 估值绑定：提交前按请求指纹审批（历史行为不变） |

## 模块

| 模块 | 职责 |
|---|---|
| `scripts/dreamina_canvas_adapter.py` | `dreamina-canvas` 的 argv 封装：复用 legacy 适配器的安全内核（绝对路径、SHA-256 钉定、最小子进程环境、字节限流、进程组终止），补 Canvas 语义——exit 10/20 是状态不是失败、每个写操作需要 projectId |
| `scripts/canvas_execution.py` | 请求 → Canvas 链：预检（version/schema/auth account）→ 发现 → 画布解析 → 素材上传 → 草稿 → 报价；模式映射 text2image→t2i、image2image→i2i、image2video/multiframe2video/multimodal2video→m2v、frames2video→first_last_frame（Canvas 无 i2v） |
| `scripts/canvas_submission_service.py` | 报价绑定审批：指纹 = 请求 + projectId + 实时 ceilings；同一套 ApprovalGuard / OperationLedger 记账 |
| `scripts/trusted_canvas_cli.py` | Canvas 二进制的独立信任注册（`~/.config/dreamina-design/trusted-canvas-cli.json`，0600 契约与 legacy 相同） |

## 身份模型

`session_id` 继续作为审批/账本关联键（贯穿 ApprovalGuard 与 OperationLedger），
**只在执行边界**解析为 Canvas 的 `projectId`：

1. 调用方传 `project_id` → 直接使用，无远端写入；
2. 否则传 `canvas_name` 并经 `approval_provider.confirm({"operation":
   "dreamina-canvas-create", ...})` 显式批准后 `canvas create --use`；
3. 绝不隐式切换画布（`canvas ls` 只读）。

## 付费链与恢复

```text
quote():  preflight → discovery → canvas → upload → node create（免费草稿）→ node quote
submit(): consume_approval(指纹含 ceilings) → node confirm(--credit-ceiling) → node run
wait():   operation wait <submitId> --timeout 10m --interval 5s   # exit 20 = 服务端仍在跑
```

- `submitId` 在 quote 阶段生成（uuid4 小写），confirm/run/复用同一 ID；
  换 ID 即重新计费。
- 提交意图写入 OperationLedger（`canvas-<mode>`），响应丢失时按原
  fingerprint 查询，不盲目重提。
- upscale 是独立定价分支，批准 token 与 `node confirm` 不通用（见
  `dreamina-canvas-cli-image2image`）。

## 测试

`tests/test_dreamina_canvas_adapter.py`（35 项，合成 `dreamina-canvas` stub：
退出码矩阵、模式拒绝矩阵、审批一次性消费、账本落盘）与
`tests/test_canvas_mcp.py`（6 项，MCP 双轨：默认 canvas、legacy 显式、
画布创建单独批准、web 前置确认、schema 可加性契约）。

## 边界

- `auth` 与真实付费 canary 未执行；发布前以 `NOT_RUN` 记录。
- 冻结的 `dreamina-cli*` 技能与 legacy 轨按原样保留，仅响应显式
  `runtime: "legacy"`。
