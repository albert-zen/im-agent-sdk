from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CASES = {
    "base": (
        "",
        "import asyncio, importlib.util, imagent; "
        "from imagent.gateway import GatewayExtensions, GatewayLimits, GatewayRepositories; "
        "assert GatewayRepositories(bindings=object()).bindings is not None; "
        "assert GatewayLimits().startup_buffer_max_pending == 256; "
        "assert GatewayExtensions().controller is None; "
        "from imagent.applications import codex_app_server_client; "
        "from imagent.channels import channel_from_config; "
        "assert type(codex_app_server_client(endpoint='stdio://')).__name__ "
        "== 'AppServerClient'; "
        "assert callable(channel_from_config); "
        "from imagent.applications import AppServerArtifactCandidate as top_artifact; "
        "from imagent.applications.presentation import "
        "AppServerArtifactCandidate as presentation_artifact; "
        "from imagent.applications.presentation.artifact_materialization "
        "import AppServerArtifactCandidate as owner_artifact; "
        "assert top_artifact is presentation_artifact is owner_artifact; "
        "assert importlib.util.find_spec('imagent.applications.appserver_artifacts') is None; "
        "assert importlib.util.find_spec('websockets') is None; "
        "assert importlib.util.find_spec('imcodex') is None; "
        "from imagent.applications.appserver_client import AppServerSupervisor; "
        "supervisor=AppServerSupervisor(app_server_url='ws://127.0.0.1:9'); "
        "\ntry: asyncio.run(supervisor.connect_external())"
        "\nexcept RuntimeError as exc: "
        " assert \"'appserver' optional dependency\" in str(exc)"
        "\nelse: raise AssertionError('missing appserver extra did not fail fast')",
    ),
    "qq": (
        "qq",
        "import importlib.util; "
        "from imagent.channels import channel_from_config; "
        "adapter=channel_from_config('qq', config={'enabled':False}, "
        "channel_instance_id='qq-clean'); "
        "adapter.validate_startup_configuration(); "
        "assert adapter.channel_instance_id == 'qq-clean'; "
        "assert importlib.util.find_spec('imcodex') is None",
    ),
    "telegram": (
        "telegram",
        "import importlib.util; "
        "from imagent.channels import channel_from_config; "
        "adapter=channel_from_config('telegram', config={'enabled':False}, "
        "channel_instance_id='telegram-clean'); "
        "adapter.validate_startup_configuration(); "
        "assert adapter.channel_instance_id == 'telegram-clean'; "
        "assert importlib.util.find_spec('imcodex') is None",
    ),
    "feishu": (
        "feishu",
        "import importlib.util; "
        "from imagent.channels import channel_from_config; "
        "adapter=channel_from_config('feishu', config={'enabled':False}, "
        "channel_instance_id='feishu-clean'); "
        "adapter.validate_startup_configuration(); "
        "assert adapter.channel_instance_id == 'feishu-clean'; "
        "assert importlib.util.find_spec('lark_channel') is not None; "
        "assert importlib.util.find_spec('imcodex') is None",
    ),
    "weixin": (
        "weixin",
        "import importlib.util, tempfile; "
        "from imagent.channels import channel_from_config; "
        "adapter=channel_from_config('weixin', "
        "config={'enabled':False,'state_dir':tempfile.mkdtemp()}, "
        "channel_instance_id='weixin-clean'); "
        "adapter.validate_startup_configuration(); "
        "assert adapter.channel_instance_id == 'weixin-clean'; "
        "assert importlib.util.find_spec('Crypto') is not None; "
        "assert importlib.util.find_spec('imcodex') is None",
    ),
    "appserver": (
        "appserver",
        "import importlib.util; "
        "from imagent.applications import codex_app_server_client; "
        "assert type(codex_app_server_client(codex_bin='codex', endpoint='stdio://')).__name__ "
        "== 'AppServerClient'; "
        "assert importlib.util.find_spec('websockets') is not None; "
        "assert importlib.util.find_spec('imcodex') is None",
    ),
}


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required; install the repository-pinned uv toolchain first")
    wheels = sorted(DIST.glob("im_agent_sdk-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one SDK wheel in {DIST}, found {len(wheels)}")
    wheel = wheels[0].resolve()

    for name, (extra, code) in CASES.items():
        requirement = f"{wheel}[{extra}]" if extra else str(wheel)
        subprocess.run(
            [
                uv,
                "run",
                "--isolated",
                "--no-project",
                "--refresh",
                "--python",
                f"{sys.version_info.major}.{sys.version_info.minor}",
                "--with",
                requirement,
                "python",
                "-c",
                code,
            ],
            cwd=ROOT,
            check=True,
        )
        print(f"PASS clean-wheel {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
