from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENTKIT_SOURCE = (
    "git+https://github.com/albert-zen/AgentKit.git@3fcb1220e48ede6cd17849fe4238362edcdb90c7"
)


def main() -> int:
    uvx = shutil.which("uvx")
    if uvx is None:
        print(
            "AgentKit launcher requires uv/uvx (CI pins uv 0.12.0). "
            "Install it from https://docs.astral.sh/uv/getting-started/installation/, "
            "verify `uvx --version`, then rerun this command.",
            file=sys.stderr,
        )
        return 127

    repo = Path(__file__).resolve().parents[1]
    command = [
        uvx,
        "--from",
        AGENTKIT_SOURCE,
        "agentkit",
        "--repo",
        str(repo),
        *sys.argv[1:],
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.call(command, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
