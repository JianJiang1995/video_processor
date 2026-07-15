"""Regression guard against media-specific runtime inference branches."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOTS = (
    ROOT / "backend",
    ROOT / "frontend" / "src",
    ROOT / "frontend" / "electron",
)
SKIP_PARTS = {"tests", "node_modules", "dist", "__pycache__"}

FORBIDDEN = {
    "numbered video identifier": re.compile(r"\bvideo\d{2,3}\b", re.IGNORECASE),
    "Cholec80 absolute data path": re.compile(r"/(?:home/user/)?data/cholec80", re.IGNORECASE),
    "literal window-id equality": re.compile(r"\bwindow_id\s*==\s*\d+"),
    "literal timestamp equality": re.compile(r"\btimestamp\s*==\s*\d+(?:\.\d+)?"),
}


def runtime_source_files() -> list[Path]:
    files: list[Path] = []
    for source_root in RUNTIME_ROOTS:
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".cjs", ".vue"}:
                continue
            if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
                continue
            files.append(path)
    return files


class RuntimeGeneralizationTests(unittest.TestCase):
    def test_no_video_or_timestamp_specific_runtime_branches(self) -> None:
        violations: list[str] = []
        for path in runtime_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in FORBIDDEN.items():
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line}: {label}: {match.group(0)!r}"
                    )
        self.assertEqual([], violations, "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
