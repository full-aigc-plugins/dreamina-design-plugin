## Context

`skills.lock.json` 是通用多来源锁，`skills/.upstream-commit` 是 Dreamina Design 早期保留的单来源分发契约。两者必须在 vendor update 中原子更新。

## Decision

- 仅在 marker 已存在时保持兼容，不给其他插件引入新文件。
- marker 存在但 lock 有多个来源时失败关闭，避免选择不明确的 SHA。
- 先完成所有来源复制和摘要校验，再同时写 lock 与 marker。
