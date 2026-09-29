"""Trusted enrollment for the ``dreamina-canvas`` binary.

Same contract as :class:`scripts.trusted_cli.TrustedCliStore` — absolute
regular file, safe owner/mode, SHA-256 pinned, private 0600 config — with
its own default config file so the two runtimes are enrolled independently.
"""

from __future__ import annotations

from pathlib import Path

from scripts.trusted_cli import TrustedCliStore


class TrustedCanvasCliStore(TrustedCliStore):
    def __init__(self, path: Path | None = None) -> None:
        super().__init__(
            path
            or (Path.home() / ".config" / "dreamina-design" / "trusted-canvas-cli.json")
        )
