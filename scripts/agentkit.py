from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENTKIT_SOURCE = (
    "git+https://github.com/albert-zen/AgentKit.git@3fcb1220e48ede6cd17849fe4238362edcdb90c7"
)
COMPONENT_MAP_PYYAML = "PyYAML==6.0.3"
COMPONENT_MAP_HTTPX = "httpx>=0.28,<1"
COMPONENT_MAP_GATED_COMMANDS = {"check", "lint-architecture"}


def _validate_component_map(
    *, uv: str, repo: Path, arguments: list[str], environment: dict[str, str]
) -> int:
    if not arguments or arguments[0] not in COMPONENT_MAP_GATED_COMMANDS:
        return 0
    validation_environment = environment.copy()
    source_path = str(repo / "src")
    existing_python_path = validation_environment.get("PYTHONPATH")
    validation_environment["PYTHONPATH"] = (
        source_path
        if not existing_python_path
        else f"{source_path}{os.pathsep}{existing_python_path}"
    )
    return subprocess.call(
        [
            uv,
            "run",
            "--isolated",
            "--no-project",
            "--with",
            COMPONENT_MAP_PYYAML,
            "--with",
            COMPONENT_MAP_HTTPX,
            "python",
            str(repo / "scripts" / "validate_component_map.py"),
        ],
        cwd=repo,
        env=validation_environment,
    )


def main() -> int:
    uv = shutil.which("uv")
    uvx = shutil.which("uvx")
    if uv is None or uvx is None:
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
    component_map_status = _validate_component_map(
        uv=uv,
        repo=repo,
        arguments=sys.argv[1:],
        environment=environment,
    )
    if component_map_status != 0:
        return component_map_status
    return subprocess.call(command, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
