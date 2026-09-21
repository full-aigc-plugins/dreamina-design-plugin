#!/usr/bin/env python3
"""SessionStart hook: report Dreamina Design readiness for this plugin.

Advisory only — always exits 0.
"""
from __future__ import annotations

import json
import shutil
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    lines: list[str] = []
    lines.append(f"python3: {sys.version.split()[0]}")

    server = ROOT / "scripts" / "dreamina_mcp_server.py"
    lines.append("dreamina MCP server: 就绪" if server.is_file() else "dreamina MCP server: 脚本缺失")

    cli = shutil.which("dreamina") or shutil.which("dreamina-cli")
    if cli:
        lines.append(f"dreamina CLI: {cli}")
    else:
        lines.append("dreamina CLI: 不在 PATH——首次使用经 MCP 工具 dreamina_cli_status 检测/安装")

    vendor = ROOT / "scripts" / "vendor"
    if vendor.is_dir():
        lines.append("vendored CLI: 就绪")

    with suppress(ValueError, OSError):
        sys.stdin.read()

    print("即梦设计插件环境：" + "；".join(lines))
    return 0


if __name__ == "__main__":
    with suppress(ValueError, OSError):
        json.load(sys.stdin)
    sys.exit(main())
