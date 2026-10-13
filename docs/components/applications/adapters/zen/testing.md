# Zen Application adapter testing

## Current evidence

Zen is covered by the shared App Server suites and vertical evidence:

- `tests/applications/adapters/test_zen.py` and the retained
  `tests/applications/adapters/appserver/test_input_integration.py` prove native start, profile/options,
  connection reset, and independently owned Zen steering;
- `tests/applications/adapters/appserver/test_requests.py` proves
  command-approval mapping and explicit unsupported request methods; the
  retained Gateway integration case remains in
  `tests/applications/adapters/appserver/test_gateway_request_integration.py`;
- `tests/applications/presentation/test_artifact_materialization.py` proves
  the optional shared artifact position without adding Codex live activity;
- `tests/gateway/test_vertical_slice.py` proves Zen and Codex remain distinct
  Application kinds and native outcome behavior.

The tests keep truthful `started/create_new` and `steered/preserve_existing`
results, native history
recovery, request cardinality/bounds, and explicit unsupported capability. A
shared transport fixture is not evidence for a Codex-only Zen feature.

They also prove one stable workspace Project/list/get surface across adapter
reconstruction, canonical-root fingerprint changes when the configured root
changes, mandatory Project ancestry throughout mapping, and explicit
unsupported native Project creation without a client call.
Shared App Server request evidence additionally proves that workspace scope is
verified before request state or native effects and is not re-read after a
successful native response.

## Target evidence and verification

The target owner suite is `tests/applications/adapters/test_zen.py`; the
retained input and vertical suites remain affected cross-component evidence.
Run:

```sh
uv run python -m unittest tests.applications.adapters.test_zen -v
uv run python -m unittest tests.applications.adapters.appserver.test_requests -v
uv run python -m unittest tests.applications.adapters.appserver.test_gateway_request_integration -v
uv run python -m unittest tests.gateway.test_vertical_slice -v
```

Later physical movement must run full discovery, every AGENTS gate, AgentKit,
and clean-wheel smoke.

## Authority

- [Zen design](design.md)

## Native interoperability

`tests/integration/test_zen_native_interop.py` uses a real public SDK Gateway,
`SQLiteGatewayStore`, Zen adapter, and production App Server client against
Zen's real protocol, runtime, and journal. Its only synthetic boundaries are
the model and IM transport. `zen_native_server.mjs` loads the exact checkout's
TypeScript source through its installed `tsx` loader, never an unverified
stale build. The tested Zen tree is `ee1ef5c9238d24601015baa0218eb9b5f97268a5`
(commit `1702ca4` published by Zen PR 301).

```sh
IMAGENT_ZEN_ROOT=/absolute/path/to/verified/zen PYTHONPATH=src python -m unittest tests.integration.test_zen_native_interop -v
```

Without that explicit fixture checkout the opt-in test skips; ordinary full
unit discovery cannot be reported as native interoperability. The fixture uses
only temporary synthetic files, a loopback socket, and a fresh temporary v1
SQLite store. It proves start, active text/image steer, one authoritative Turn,
original IM reply preservation, unknown-correlation rejection, native busy and
expected-Turn mismatch/ended rejection without append, binding restoration,
and post-restart input/output. It does not prove old production DB migration.

Image tests separately prove start and steer without a shared-filesystem epoch,
byte snapshots surviving pathname replacement, symlink/root/size/source/type
rejection before native I/O, and aggregate preflight before any acquisition.
