## Why

Dreamina MCP 绑定 `/usr/bin/python3`，在 Windows、Homebrew 和非标准 Python 环境中无法加载。

## What Changes

- Codex 与 ZCode MCP 使用 PATH 上的 `python3`。
- 增加禁止绝对解释器路径的回归测试。

## Capabilities

### New Capabilities

- `host-runtime-loading`: MCP 启动器不依赖固定操作系统路径。

### Modified Capabilities

None.

## Impact

影响 `.mcp.json`、ZCode manifest 与分发测试。
