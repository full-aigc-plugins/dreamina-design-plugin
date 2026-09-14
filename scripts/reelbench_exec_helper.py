"""Fixed descriptor-bound launcher for staged ReelBench Node execution."""
from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> None:
    if len(argv) < 4:
        raise SystemExit("usage: workspace-fd node script [args...]")
    workspace_fd = int(argv[1])
    node, script, rest = argv[2], argv[3], argv[4:]
    os.fchdir(workspace_fd)
    os.close(workspace_fd)
    os.execve(node, [node, script, *rest], {"PATH": os.environ["PATH"], "LANG": "C", "LC_ALL": "C"})


if __name__ == "__main__":
    main(sys.argv)
