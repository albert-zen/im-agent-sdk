from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "plugins").rglob("*.md"))
    return sorted({path for path in files if path.is_file()})


def local_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#") or target.startswith(EXTERNAL_PREFIXES):
        return None
    path_text = unquote(target.split("#", 1)[0])
    if not path_text:
        return None
    return (source.parent / path_text).resolve()


def main() -> int:
    missing: list[str] = []
    for source in markdown_files():
        text = source.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = local_target(source, match.group(1))
            if target is not None and not target.exists():
                missing.append(f"{source.relative_to(ROOT).as_posix()}: {match.group(1)}")
    if missing:
        print("Broken local Markdown links:")
        for item in missing:
            print(f"- {item}")
        return 1
    print(f"Validated local Markdown links in {len(markdown_files())} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
