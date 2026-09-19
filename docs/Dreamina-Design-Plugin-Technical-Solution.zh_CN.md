# Dreamina Design 插件技术方案

> **文档信息**
>
> | 字段 | 值 |
> |---|---|
> | 状态 | 已实现为 `0.4.0`；参考视频运行门禁记为 `NOT_RUN` |
> | 范围 | 插件如何包装 CLI、契约是什么、如何验证 |
> | 读者 | 扩展或评审本插件的实现者 |
> | 运行证据 | `docs/verification/` |

## 1. 技术决策

构建以 Skill 为先的兼容插件，围绕已安装的 `dreamina` CLI。使用共享 subprocess 适配器、能力快照、批准守卫、操作台账与产物校验器。

### 备选方案

| 备选方案 | 被否的原因 |
|---|---|
| 直接调用远端 API | 会重复 CLI 已拥有的认证、权益与定价逻辑 |
| 用通用的"执行命令"工具暴露 CLI | 会让任意执行从模型编写的参数抵达 |
| 保留此前的 `jimeng-*` Skill 身份 | 上游规范名是 `dreamina-*`，两套身份必然漂移 |
| 只把批准当作 MCP 标志 | 标志只是元数据；付费动作需要失败即关闭的人工确认 |
| 自动重试长生成 | 该调用是付费的；重试可能重复扣费，且无法证明哪次成功 |

## 2. 已实现的布局

```text
.codex-plugin/plugin.json
.mcp.json
scripts/dreamina_mcp_server.py
scripts/video_project_mcp.py
scripts/dreamina_adapter.py
scripts/approval_guard.py
scripts/operation_ledger.py
scripts/trusted_cli.py
scripts/native_approval.py
scripts/video_project_store.py
scripts/reference_policy.py
skills/
tests/
```

| 路径 | 职责 |
|---|---|
| `scripts/dreamina_mcp_server.py` | stdio JSON-RPC 分发与错误信封 |
| `scripts/video_project_mcp.py` | 10 个参考视频工程工具 |
| `scripts/dreamina_adapter.py` | 仅用 argv 调用 CLI，并给出带类型的失败 |
| `scripts/image_service.py`、`scripts/video_service.py`、`scripts/task_service.py` | 请求构造、提交与查询 |
| `scripts/auth_service.py`、`scripts/session_service.py` | 封闭的 OAuth 与 Session 命令集 |
| `scripts/approval_guard.py`、`scripts/native_approval.py` | 一次性批准持久化与失败即关闭的弹窗 |
| `scripts/trusted_cli.py` | CLI 信任登记与受保护存储 |
| `scripts/video_project_store.py` | 带 compare-and-swap 迁移的版本化工程状态 |
| `scripts/reference_policy.py` | 本地输入的边界、类型与大小策略 |

## 3. 迁移映射

现有 13 个 Skill 目录从 `jimeng-*` 机械重命名为 `dreamina-*`；`dreamina-cli` 保持规范名。目录名、frontmatter name、链接、示例、README、GitHub 路径与安装命令同步变更。校验器会拒绝任何残留的 `jimeng-` 身份，但允许解释旧名的产品文案。

上游仍是事实源：`dreamina-skills` 通过 `skills/.upstream-commit` 按提交固定，打包的 Skill 树逐字节校验。

## 4. 契约

- `CapabilitySnapshot`：CLI 版本/提交加上当前 schema；当已安装 CLI 没有 `schema` 命令时，退化为命令帮助快照。
- `GenerationRequest`：模式、Prompt、参考、模型 token、分辨率、比例、时长、数量。
- `ApprovalReceipt`：确切的请求指纹与报价/额度确认。
- `OperationReceipt`：会话、提交 ID、状态、时间戳、所需动作。
- `ArtifactReceipt`：本地路径、校验和、媒体元数据、来源提交 ID。

| 契约 | 不变量 |
|---|---|
| `CapabilitySnapshot` | 实时读取；本轮对话结束后不再当作权威 |
| `GenerationRequest` | 未知字段或不受支持的参数会被拒绝 |
| `ApprovalReceipt` | 一次性、五分钟有效期、绑定请求指纹 |
| `OperationReceipt` | 以 `submit_id` 为键；不含任何形似凭据的字段 |
| `ArtifactReceipt` | 校验和不匹配是失败，不是告警 |

## 5. 配置与状态

| 设置 | 位置 | 说明 |
|---|---|---|
| MCP 服务器 | `.mcp.json` | stdio；启动超时 10 秒，工具超时 3600 秒 |
| 工具批准模式 | `.mcp.json` | 只读工具为 `approve`，付费与写类工具为 `prompt` |
| 信任记录 | `~/.config/dreamina-design/trusted-cli.json` | 文件 `0600`、目录 `0700`；保存路径与摘要 |
| 状态根目录 | `~/.local/share/dreamina-design/` | 内含 `operations/` 与 `approvals/` |
| 参考策略 | `scripts/reference_policy.py` | 单图 50 MiB、单媒体 512 MiB，含边界与类型校验 |

## 6. 错误模型

失败时返回结构化信封，包含 `error_type`、`message`、`retryable`、`requires_user_action` 与 `next_action`。`next_action` 取值为 `request_user_action`、`query_same_submit_id` 或 `correct_request`。

| 情形 | 信封行为 |
|---|---|
| 需要登录或会话过期 | `requires_user_action` 为真；`next_action` 为 `request_user_action` |
| 权限或权益被拒 | `requires_user_action` 为真 |
| 提交结果含糊 | 预留被消耗；操作进入人工复核 |
| 可重试的查询失败 | `retryable` 为真，且仅限查询工具 |
| 方法不存在 | JSON-RPC `-32601` |

## 7. 测试

使用来自已安装 CLI 的、带版本的 help/schema 快照，且不触发生成。合成 fixture 覆盖认证、权限、升级、校验、查询、失败、取消与下载。消耗额度的金丝雀永不属于常规 CI。

| 层次 | 证明 | 命令 |
|---|---|---|
| 单元与契约 | 服务、守卫、台账、信任登记、参考策略 | `python3 -m unittest discover -s tests` |
| 分发 | 必需文件、清单引用、秘密扫描 | `python3 scripts/validate_distribution.py .` |
| 运行门禁 | 计划门禁与运行门禁条目 | 先 `python3 scripts/validate_distribution_v7.py --plan-gate`，再 `--require-runtime-gates` |
| Skill 一致性 | 打包 Skill 逐字节一致 | `python3 scripts/verify_skill_snapshot.py --strict` |
| TRACE | 逐 Skill 的行为契约 | `python3 scripts/run_strict_trace.py` |
| 付费金丝雀 | 一次真实消耗额度的调用 | 独立授权门禁，记录于 `docs/verification/` |

## 8. 兼容性与证据映射

| 方面 | 立场 |
|---|---|
| Python | 3.13 |
| CLI | 从官方安装器安装，并通过原生信任登记 |
| 参考视频工具 | 已实现；所有运行门禁条目为 `NOT_RUN` |
| 付费金丝雀 | 单独批准或 `NOT_RUN` |
| 回滚 | 回退插件即可；回执格式可追加，因此仍可读取 |

| 断言 | 证据 |
|---|---|
| 工具清单与批准模式 | `.mcp.json` |
| 批准强制 | `scripts/approval_guard.py`、`scripts/native_approval.py` |
| 参考输入约束 | `scripts/reference_policy.py` |
| 运行门禁条目 | `docs/verification/reference-video-runtime-2026-09-14.md` |
| Skill 一致性 | `scripts/verify_skill_snapshot.py` |
