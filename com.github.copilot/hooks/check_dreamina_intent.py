#!/usr/bin/env python3
"""UserPromptSubmit hook: point Dreamina-shaped requests at the plugin commands.

Advisory only — always exits 0; silent unless the prompt looks Dreamina-related.
"""
from __future__ import annotations

import json
import re
import sys

INTENT_RE = re.compile(
    r"即梦|dreamina|jimeng|seedance|文生图|文生视频|图生图|图生视频|即梦设计",
    re.IGNORECASE,
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        # JSONDecodeError ⊂ ValueError；stdin 流异常属 OSError。行为与原兜底一致。
        payload = {}

    prompt = ""
    if isinstance(payload, dict):
        prompt = str(payload.get("prompt") or "")

    if prompt.strip().startswith("/"):
        return 0

    if INTENT_RE.search(prompt):
        print(
            "提示：该请求疑似即梦（Dreamina）相关。可用 /dreamina 总入口或细分命令 "
            "(/dreamina-text2image /dreamina-image2image /dreamina-text2video "
            "/dreamina-image2video /dreamina-auto-seedance /dreamina-seedance-resume "
            "/dreamina-video-production)；MCP 工具经 dreamina_design 提供。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
