from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DIAGNOSTICS_FACADE_CHECK = (
    "import subprocess, sys; "
    "import imagent.interaction.diagnostics as common_owner; "
    "import imagent.interaction.channels.diagnostics as channel_owner; "
    "import imagent.diagnostics as transition_facade; "
    "common_names = ('ConnectionDiagnosticState', 'DiagnosticFailureCode', "
    "'QueueDiagnosticName', 'QueueDiagnosticFacts', 'ConnectionDiagnosticFacts'); "
    "channel_names = ('ChannelDiagnosticFacts', 'ChannelDiagnosticsProvider'); "
    "assert all(getattr(transition_facade, name) is getattr(common_owner, name) "
    "for name in common_names); "
    "assert all(getattr(transition_facade, name) is getattr(channel_owner, name) "
    "for name in channel_names); "
    "subprocess.run([sys.executable, '-c', "
    '"import imagent.diagnostics as f; '
    "import imagent.interaction.diagnostics as c; "
    "import imagent.interaction.channels.diagnostics as ch; "
    "assert f.ConnectionDiagnosticState is c.ConnectionDiagnosticState; "
    "assert f.QueueDiagnosticFacts is c.QueueDiagnosticFacts; "
    "assert f.ChannelDiagnosticFacts is ch.ChannelDiagnosticFacts; "
    'assert f.ChannelDiagnosticsProvider is ch.ChannelDiagnosticsProvider"], '
    "check=True); "
)
CHANNEL_FACADE_CHECK = (
    "import imagent.interaction.channels as channel_facade; "
    "import imagent.interaction.channels.contract as channel_owner; "
    "import imagent.channels as adapter_facade; "
    "import imagent.interaction.channels.adapters.runtime as runtime_owner; "
    "assert all(getattr(channel_facade, name) is getattr(channel_owner, name) "
    "for name in channel_facade.__all__); "
    "assert adapter_facade.NativeTransportChannelAdapter is "
    "runtime_owner.NativeTransportChannelAdapter; "
    "assert adapter_facade.channel_from_config is runtime_owner.channel_from_config; "
    "retired_adapter = ('ChannelAdapter', 'ChannelStartupConfigurationValidator', "
    "'MessageHandler', 'InboundAdmission', 'InboundAdmissionHandler'); "
    "retired_contract = ('ChannelCapabilities', 'DeliveryProfile', "
    "'DeliverySupportLevel', 'ReplyReferenceScope', 'DeliveryReceipt', "
    "'DeliveryItemReceipt', 'DeliverySegmentReceipt', 'DeliveryItemStatus', "
    "'DeliveryReceiptStatus', 'DeliverySegmentStatus', "
    "'validate_delivery_receipt', 'validate_delivery_receipt_for_content'); "
    "import imagent.adapters as adapters_facade; "
    "import imagent.contracts as contracts_facade; "
    "assert all(not hasattr(adapters_facade, name) and name not in "
    "getattr(adapters_facade, '__all__', ()) "
    "for name in retired_adapter); "
    "assert all(not hasattr(contracts_facade, name) and name not in contracts_facade.__all__ "
    "for name in retired_contract); "
)
BINDING_IMPORT_ORDER_CHECK = r'''
import inspect
import subprocess
import sys
import typing

check_code = r"""
import inspect
import typing

import imagent.contracts as contracts_facade
import imagent.contracts.operations as historical_operations
import imagent.contracts.validators as validators_owner
import imagent.gateway as gateway_facade
import imagent.gateway.routing as routing_facade
from imagent.gateway.persistence import ConversationBinding
from imagent.gateway.routing import bindings as binding_owner

binding_names = (
    "BindConversationToProject",
    "BindConversationToThread",
    "ClearConversationThread",
    "ConversationBound",
)
for name in binding_names:
    owner = getattr(binding_owner, name)
    assert getattr(routing_facade, name) is owner
    assert getattr(contracts_facade, name) is owner
    assert getattr(gateway_facade, name) is owner
    assert inspect.signature(getattr(contracts_facade, name)) == inspect.signature(owner)
    assert owner.__module__ == "imagent.gateway.routing.bindings"
    assert not hasattr(historical_operations, name)
    assert not hasattr(validators_owner, name)

binding_hints = typing.get_type_hints(binding_owner.ConversationBound)
facade_hints = typing.get_type_hints(contracts_facade.ConversationBound)
assert binding_hints["binding"] is ConversationBinding
assert facade_hints["binding"] is ConversationBinding
assert binding_hints["type"] is historical_operations.GatewayOperationType
assert facade_hints["type"] is historical_operations.GatewayOperationType
assert typing.get_type_hints(contracts_facade.__getattr__)["return"] is object
assert typing.get_args(contracts_facade.GatewayOperation)
assert inspect.signature(contracts_facade.validate_gateway_operation) == inspect.signature(
    validators_owner.validate_gateway_operation
)
assert inspect.signature(contracts_facade.validate_gateway_operation_result) == inspect.signature(
    validators_owner.validate_gateway_operation_result
)
assert typing.get_type_hints(contracts_facade.validate_gateway_operation) == typing.get_type_hints(
    validators_owner.validate_gateway_operation
)
assert (
    typing.get_type_hints(contracts_facade.validate_gateway_operation_result)
    == typing.get_type_hints(validators_owner.validate_gateway_operation_result)
)
"""
for first_import in (
    "import imagent.gateway.routing.bindings\n",
    "import imagent.contracts.operations\n",
    "import imagent.contracts.validators\n",
    "import imagent.contracts\n",
    "import imagent.gateway.routing\n",
):
    completed = subprocess.run(
        [sys.executable, "-c", first_import + check_code],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(
            f"binding import-order smoke failed: {first_import!r}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
'''
CASES = {
    "base": (
        "",
        BINDING_IMPORT_ORDER_CHECK + "import asyncio, importlib, importlib.util, typing, imagent; "
        "import imagent.events as event_facade; "
        "import imagent.applications.events as event_owner; "
        "from imagent.applications import "
        "ApplicationInputOutcomeUnknown as outcome_facade; "
        "from imagent.applications.contract import "
        "ApplicationInputOutcomeUnknown as outcome_owner; "
        "import imagent.contracts as contracts_facade; "
        "event_names = ['AgentEvent', 'AgentEventType', 'CursorExpired', "
        "'EventBroadcaster', 'EventBufferOverflow', 'EventStreamGap', "
        "'EventStreamOverflow', 'EventStreamReset', 'FanoutSubscription', "
        "'validate_agent_event']; "
        "assert event_facade.__all__ == event_names; "
        "assert all(getattr(event_facade, name) is getattr(event_owner, name) "
        "for name in event_names); "
        "assert all(getattr(contracts_facade, name) is getattr(event_owner, name) "
        "for name in ('AgentEvent', 'AgentEventType', 'validate_agent_event')); "
        "assert outcome_facade is outcome_owner; "
        "assert contracts_facade.ApplicationInputOutcomeUnknown is outcome_owner; "
        "assert outcome_owner.__module__ == 'imagent.applications.contract'; "
        "assert importlib.util.find_spec('imagent.contracts.errors') is None; "
        "assert imagent.events is event_facade; "
        "assert not hasattr(event_facade, '__getattr__'); "
        "import imagent.interaction.controllers as controller_facade; "
        "import imagent.interaction.controllers.common as common_owner; "
        "import imagent.interaction.controllers.contract as contract_owner; "
        "import imagent.interaction.controllers.registry as registry_owner; "
        "import imagent.interaction.controllers.request_presentation as request_owner; "
        "from imagent.contracts import (ApplicationOperation, ApplicationOperationResult, "
        "ConversationRef, GatewayOperation, GatewayOperationResult, "
        "InboundMessage, OutboundMessage); "
        "from imagent.gateway.persistence import ConversationBinding; "
        "import imagent.gateway.persistence as persistence_facade; "
        "import imagent.contracts as contracts_facade; "
        "import imagent.gateway.delivery as delivery_facade; "
        "import imagent.gateway.delivery.proactive as proactive_owner; "
        "import imagent.gateway.delivery.submissions as submissions_owner; "
        "retired = ('DeliveryTargetKind', 'ConversationDeliveryTarget', "
        "'ThreadRouteDeliveryTarget', 'DeliveryTarget', 'DeliveryIntent', "
        "'DestinationDeliveryResult', 'ProactiveDeliveryResult', "
        "'validate_delivery_intent', 'derive_delivery_target_fingerprint', "
        "'derive_delivery_payload_fingerprint', 'derive_delivery_submission_id', "
        "'derive_destination_delivery_id'); "
        "assert all(not hasattr(contracts_facade, name) and name not in contracts_facade.__all__ "
        "for name in retired); "
        "assert not hasattr(contracts_facade, 'DeliverySubmissionOrigin'); "
        "assert not hasattr(persistence_facade, 'DeliverySubmissionOrigin'); "
        "assert delivery_facade.DeliverySubmissionOrigin is "
        "submissions_owner.DeliverySubmissionOrigin; "
        "assert all(getattr(delivery_facade, name) is getattr(submissions_owner, name) "
        "for name in ('derive_delivery_target_fingerprint', 'derive_delivery_payload_fingerprint', "
        "'derive_delivery_submission_id', 'derive_destination_delivery_id')); "
        "assert all(getattr(delivery_facade, name) is getattr(proactive_owner, name) "
        "for name in ('DeliveryTargetKind', 'ConversationDeliveryTarget', "
        "'ThreadRouteDeliveryTarget', 'DeliveryTarget', 'DeliveryIntent', "
        "'DestinationDeliveryResult', 'ProactiveDeliveryResult', 'validate_delivery_intent')); "
        "assert all(getattr(controller_facade, name) is getattr(owner, name) "
        "for owner, names in ((contract_owner, ('CommandHandlerActions', 'CommandInvocationFacts', "
        "'ControllerActions', 'ControllerLifecycle', 'InboundController')), "
        "(registry_owner, ('CommandArgumentContract', 'CommandDefinition', "
        "'CommandExecutionSafety', "
        "'CommandHandler', 'CommandHandlerTimeout', 'CommandInvocation', 'CommandRegistry', "
        "'CommandRegistryDiagnostics', 'CommandRegistryError', 'CommandRegistryFailureCode', "
        "'CommandRegistryFrozenError', 'CommandRegistryLimits', 'CommandRegistryNotFrozenError', "
        "'CommandResult', 'CommandResultError', 'CommandResultStatus', "
        "'derive_command_invocation_id')), "
        "(common_owner, ('SlashController', 'register_common_commands')), "
        "(request_owner, ('MarkdownRequestPresenter', 'RequestPresentation', 'RequestPresenter'))) "
        "for name in names); "
        "assert typing.get_type_hints("
        "contract_owner.CommandHandlerActions.execute_application"
        ")['operation'] "
        "is ApplicationOperation; "
        "assert typing.get_type_hints("
        "contract_owner.CommandHandlerActions.execute_application"
        ")['return'] "
        "is ApplicationOperationResult; "
        "assert typing.get_type_hints("
        "contract_owner.ControllerActions.enter_effectful_command"
        ")['invocation'] "
        "is contract_owner.CommandInvocationFacts; "
        "assert typing.get_type_hints("
        "contract_owner.InboundController.handle"
        ")['message'] is InboundMessage; "
        "assert typing.get_type_hints("
        "contract_owner.InboundController.handle"
        ")['return'] "
        "== tuple[OutboundMessage, ...] | None; "
        "assert typing.get_type_hints(registry_owner.CommandHandler.__call__)['invocation'] "
        "is registry_owner.CommandInvocation; "
        "assert typing.get_type_hints("
        "request_owner.RequestPresenter.present_request"
        ")['conversation_ref'] "
        "is ConversationRef; "
        "assert importlib.util.find_spec('imagent.controllers') is None; "
        "\ntry: importlib.import_module('imagent.controllers')"
        "\nexcept ModuleNotFoundError as exc: assert exc.name == 'imagent.controllers'"
        "\nelse: raise AssertionError('historical Controller package is importable')"
        "\n"
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
        "from imagent.applications import "
        "AgentApplicationAdapter as top_adapter, "
        "ApplicationInputDispatchHandler as top_dispatch_handler; "
        "from imagent.applications import "
        "CodexApplicationAdapter as top_codex, "
        "ZenApplicationAdapter as top_zen; "
        "from imagent.applications.adapters.codex import "
        "CodexApplicationAdapter as codex_owner; "
        "from imagent.applications.adapters.zen import "
        "ZenApplicationAdapter as zen_owner; "
        "from imagent.applications import ("
        "T3ApplicationAdapter as top_t3, HttpT3Client as top_t3_client, "
        "T3ClientError as top_t3_error); "
        "from imagent.applications.adapters.t3 import ("
        "T3ApplicationAdapter as t3_owner, HttpT3Client as t3_client, "
        "T3ClientError as t3_error); "
        "assert top_t3 is t3_owner; "
        "assert top_t3_client is t3_client; "
        "assert top_t3_error is t3_error; "
        "from imagent.applications.contract import "
        "AgentApplicationAdapter as owner_adapter, "
        "ApplicationInputDispatchHandler as owner_dispatch_handler; "
        "from imagent.adapters import "
        "AgentApplicationAdapter as compat_adapter, "
        "ApplicationInputDispatchHandler as compat_dispatch_handler; "
        "assert top_adapter is owner_adapter is compat_adapter; "
        "assert top_dispatch_handler is owner_dispatch_handler is compat_dispatch_handler; "
        "assert owner_adapter.send_input.__module__ == "
        "'imagent.applications.contract'; "
        "assert top_codex is codex_owner; "
        "assert top_zen is zen_owner; "
        "assert importlib.util.find_spec('imagent.applications.appserver') is None; "
        "assert importlib.util.find_spec('imagent.applications.appserver_artifacts') is None; "
        "assert importlib.util.find_spec('imagent.applications.appserver_mapping') is None; "
        "assert importlib.util.find_spec('imagent.applications.t3') is None; "
        "assert importlib.util.find_spec('imagent.applications.t3_client') is None; "
        "assert importlib.util.find_spec('websockets') is None; "
        "assert importlib.util.find_spec('imcodex') is None; "
        "import imagent.applications.adapters.appserver.client as client_facade; "
        "from imagent.applications.adapters.appserver.client import ("
        "AppServerClient as facade_client, "
        "AppServerDispatchPosition as facade_position, "
        "AppServerError as facade_error, "
        "AppServerResponse as facade_response, "
        "AppServerSupervisor as facade_supervisor, "
        "APP_SERVER_DISPATCH_POSITION_KEY as facade_key, "
        "codex_app_server_client as facade_factory); "
        "from imagent.applications.adapters.appserver.client.client import "
        "AppServerClient as owner_client; "
        "from imagent.applications.adapters.appserver.client.handoff import ("
        "APP_SERVER_DISPATCH_POSITION_KEY as owner_key, "
        "AppServerDispatchPosition as owner_position, "
        "AppServerResponse as owner_response); "
        "from imagent.applications.adapters.appserver.client.supervisor import "
        "AppServerSupervisor as owner_supervisor; "
        "from imagent.applications.adapters.appserver.transport import "
        "AppServerError as transport_error; "
        "from imagent.applications.adapters.appserver.diagnostics import "
        "AppServerDiagnosticState as diagnostics_owner; "
        "import imagent.applications.adapters.appserver.requests as requests_facade; "
        "from imagent.applications.adapters.appserver.requests import "
        "AppServerRequestRuntime as runtime_owner, "
        "PendingAppServerRequest as pending_owner; "
        "assert client_facade.__all__ == ["
        "'AppServerClient', 'AppServerDispatchPosition', 'AppServerError', "
        "'AppServerResponse', 'AppServerSupervisor', "
        "'APP_SERVER_DISPATCH_POSITION_KEY', 'codex_app_server_client']; "
        "assert facade_client is owner_client; "
        "assert facade_position is owner_position; "
        "assert facade_error is transport_error; "
        "assert facade_response is owner_response; "
        "assert facade_supervisor is owner_supervisor; "
        "assert facade_key is owner_key; "
        "assert facade_factory is client_facade.codex_app_server_client; "
        "assert type(codex_app_server_client(endpoint='stdio://')._diagnostics) "
        "is diagnostics_owner; "
        "assert requests_facade.AppServerRequestRuntime is runtime_owner; "
        "assert requests_facade.PendingAppServerRequest is pending_owner; "
        "assert importlib.util.find_spec('imagent.applications.appserver_client') is None; "
        "assert importlib.util.find_spec('imagent.applications.appserver_requests') is None; "
        "assert importlib.util.find_spec('imagent.applications.appserver_request_runtime') "
        "is None; "
        "supervisor=facade_supervisor(app_server_url='ws://127.0.0.1:9'); "
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
    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)

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
                DIAGNOSTICS_FACADE_CHECK + CHANNEL_FACADE_CHECK + code,
            ],
            cwd=ROOT,
            env=clean_environment,
            check=True,
        )
        print(f"PASS clean-wheel {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
