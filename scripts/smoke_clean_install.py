from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

PUBLIC_GOLDEN_CHECK = r"""
import importlib
import importlib.util
import sys
import typing

import imagent
import imagent.gateway as gateway_facade

expected_root = [
    "ActionResult", "ActionValue", "ApplicationActions",
    "CommandArgumentContract", "CommandDefinition", "CommandExecutionSafety",
    "CommandHandler", "CommandLimits", "CommandRegistry", "CommandResult",
    "ConversationActions", "Failed", "Gateway", "GatewayExtensions",
    "GatewayLimits", "GatewayStore", "MemoryGatewayStore", "OutcomeUnknown",
    "Partial", "ProjectionPolicy", "ReadOutcome", "SQLiteGatewayStore",
    "Succeeded", "include_common_commands",
]
expected_gateway = [
    "ActionResult", "ActionValue", "ApplicationActions", "ConversationActions",
    "Gateway", "GatewayExtensions", "GatewayLimits", "MissingBindingError",
    "ReadOutcome", "StaleBindingError",
]
assert imagent.__all__ == expected_root
assert gateway_facade.__all__ == expected_gateway
assert typing.get_type_hints(imagent.__getattr__) == {"name": str, "return": object}
assert typing.get_type_hints(gateway_facade.__getattr__) == {"name": str, "return": object}

retired_modules = (
    "imagent.adapters", "imagent.contracts", "imagent.delivery_coordination",
    "imagent.delivery_planning", "imagent.diagnostics", "imagent.events",
)
assert all(importlib.util.find_spec(name) is None for name in retired_modules)
assert importlib.util.find_spec("imcodex") is None

value_exports = {
    "ActionResult": "imagent.gateway.actions",
    "ActionValue": "imagent.gateway.actions",
    "ApplicationActions": "imagent.gateway.actions",
    "ConversationActions": "imagent.gateway.actions",
    "ReadOutcome": "imagent.gateway.actions",
    "Gateway": "imagent.gateway.runtime",
    "GatewayExtensions": "imagent.gateway.composition",
    "GatewayLimits": "imagent.gateway.composition",
    "GatewayStore": "imagent.gateway.persistence",
    "MemoryGatewayStore": "imagent.gateway.persistence",
    "SQLiteGatewayStore": "imagent.gateway.persistence",
    "ProjectionPolicy": "imagent.gateway.routing",
}
for name, module_name in value_exports.items():
    owner = getattr(importlib.import_module(module_name), name)
    assert getattr(imagent, name) is owner
    if name in expected_gateway:
        assert getattr(gateway_facade, name) is owner

from imagent.gateway import GatewayExtensions, GatewayLimits
from imagent.gateway.composition import _GatewayRuntimeDependencies
assert GatewayLimits().startup_buffer_max_pending == 256
assert GatewayExtensions().controller is None
assert _GatewayRuntimeDependencies(bindings=object()).bindings is not None
assert not hasattr(gateway_facade, "GatewayRepositories")
assert not hasattr(gateway_facade, "ImAgentGateway")
assert not hasattr(gateway_facade, "GatewayOperation")
assert not hasattr(gateway_facade, "ProactiveDeliveryService")

import imagent.applications as applications
import imagent.applications.events as event_owner
import imagent.gateway.diagnostics as gateway_diagnostics
import imagent.gateway.routing as routing
import imagent.gateway.routing.operations as routing_owner
import imagent.interaction.channels as channel_contract
import imagent.interaction.channels.contract as channel_owner
assert applications.AgentApplicationAdapter.__module__ == "imagent.applications.contract"
assert event_owner.AgentEvent.__module__ == "imagent.applications.events"
assert routing.GatewayOperation is routing_owner.GatewayOperation
assert all(getattr(channel_contract, name) is getattr(channel_owner, name)
           for name in channel_contract.__all__)
assert gateway_diagnostics.DiagnosticsSnapshot.__module__ == "imagent.gateway.diagnostics"
"""

REFERENCE_CONSUMER_CHECK = r"""
import os
import subprocess
import sys
import tempfile

reference_environment = os.environ.copy()
reference_environment.pop("PYTHONPATH", None)
with tempfile.TemporaryDirectory() as reference_cwd:
    reference_run = subprocess.run(
        [sys.executable, "-m", "examples.reference_consumer.main"],
        cwd=reference_cwd,
        env=reference_environment,
        capture_output=True,
        text=True,
        check=False,
    )
assert reference_run.returncode == 0, reference_run.stderr
assert reference_run.stdout == (
    "reference consumer OK: projects=1 threads=2 conversations=2 "
    "max_workers=1 diagnostics=bounded sqlite_recovery=true shutdown=true\n"
)
assert reference_run.stderr == ""
"""

CASES = {
    "base": (
        "",
        "import importlib.util\n"
        "assert importlib.util.find_spec('websockets') is None\n"
        "assert importlib.util.find_spec('Crypto') is None\n"
        "assert importlib.util.find_spec('lark_channel') is None\n",
    ),
    "qq": (
        "qq",
        "from imagent.channels import channel_from_config\n"
        "adapter = channel_from_config('qq', config={'enabled': False}, "
        "channel_instance_id='qq-clean')\n"
        "adapter.validate_startup_configuration()\n"
        "assert adapter.channel_instance_id == 'qq-clean'\n",
    ),
    "telegram": (
        "telegram",
        "from imagent.channels import channel_from_config\n"
        "adapter = channel_from_config('telegram', config={'enabled': False}, "
        "channel_instance_id='telegram-clean')\n"
        "adapter.validate_startup_configuration()\n"
        "assert adapter.channel_instance_id == 'telegram-clean'\n",
    ),
    "feishu": (
        "feishu",
        "import importlib.util\n"
        "from imagent.channels import channel_from_config\n"
        "adapter = channel_from_config('feishu', config={'enabled': False}, "
        "channel_instance_id='feishu-clean')\n"
        "adapter.validate_startup_configuration()\n"
        "assert adapter.channel_instance_id == 'feishu-clean'\n"
        "assert importlib.util.find_spec('lark_channel') is not None\n",
    ),
    "weixin": (
        "weixin",
        "import importlib.util\n"
        "import tempfile\n"
        "from imagent.channels import channel_from_config\n"
        "adapter = channel_from_config('weixin', "
        "config={'enabled': False, 'state_dir': tempfile.mkdtemp()}, "
        "channel_instance_id='weixin-clean')\n"
        "adapter.validate_startup_configuration()\n"
        "assert adapter.channel_instance_id == 'weixin-clean'\n"
        "assert importlib.util.find_spec('Crypto') is not None\n",
    ),
    "appserver": (
        "appserver",
        "import importlib.util\n"
        "from imagent.applications import codex_app_server_client\n"
        "client = codex_app_server_client(codex_bin='codex', endpoint='stdio://')\n"
        "assert type(client).__name__ == 'AppServerClient'\n"
        "assert importlib.util.find_spec('websockets') is not None\n",
    ),
}


def _case_source(code: str) -> str:
    """Build one executable source string for a clean-wheel profile."""
    return "\n".join(
        (
            PUBLIC_GOLDEN_CHECK,
            code,
            REFERENCE_CONSUMER_CHECK,
        )
    )


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required; install the repository-pinned uv toolchain first")
    wheels = sorted(DIST.glob("im_agent_sdk-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one SDK wheel in {DIST}, found {len(wheels)}")
    wheel = wheels[0].resolve()
    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)

    for name, (extra, code) in CASES.items():
        source = _case_source(code)
        compile(source, f"<clean-wheel {name}>", "exec")
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
                source,
            ],
            cwd=ROOT,
            env=clean_environment,
            check=True,
        )
        print(f"PASS clean-wheel {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
