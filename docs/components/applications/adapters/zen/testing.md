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

`tests/gateway/test_creation_profiles.py` exercises public Gateway actions with
both concrete Zen and Codex adapters: configuration snapshot, concurrent
Conversation selection, default preservation, same-action replay, changed-profile
conflict, explicit unsupported selection, Python/schema bounds, and same-ID
changed-configuration conflict after SQLite restart without duplicate creation.

## Queued admission thin slice

`tests/applications/adapters/test_zen_queue.py` proves capability absence,
connection-epoch fencing, strict protocol-fixture receipt decoding, no overload
retry/fallback start, and bounded human-only canonical recovery.
`tests/integration/test_zen_queue_native_interop.py` uses the same authenticated
real-native fixture, synthetic model/channel and fresh SQLite as the steering
interop suite. On an older Host, it proves capability rejection before its
pre-dispatch callback and unchanged Items, then seeds a native human queue to
verify pending/original-Turn recovery with a fresh client. This native seed is
not the ordinary Gateway queue path. The complete positive Gateway/image/
restart/cancel/approval matrix remains blocked on independent native delivery
and coherent Gateway queue admission persistence.

The later Gateway integration must include same-Thread concurrent native read,
Codex/public resume, native subscription recovery, stop and process restart.
The staged read-only adapter test is not evidence that those lifecycle owners
already share a barrier. Rooted-byte tests verify missing image capability
before file access and that post-fence file replacement cannot change the sent
bytes, text boundaries/order or admission digest.

The actual client/scripted transport boundary must return typed unknown for
null, array, string, number and empty-object successful queue replies, with
exactly one native send in every case; a valid receipt remains accepted. An
invalid alternate client result must receive the same adapter classification.


## Native queue observation and cancellation

`tests/applications/adapters/test_zen_queue_lifecycle.py` covers the production
client resume owner and the adapter's optional typed lifecycle ports using
controlled boundary responses. `test_zen_queue_bulk.py` covers complete snapshot
cohort mapping. `test_zen_queue_native_lifecycle.py` is opt-in with
`IMAGENT_ZEN_ROOT`: it runs the real authenticated native WebSocket protocol,
synthetic model, temporary workspace and production SDK client. It proves
installed observation, canonical queued/edit/cancel notices, unchanged original
fingerprint across native edits, real admitted Turn identity, and cancellation
of already-admitted input without interruption. It does not substitute for the
separate Gateway persistence/restart/request-routing release gate.

The verified native source is Core commit
`b8d16bfed194e003bcf1238458f43c1cba365f95`, tree `71c1a6ff`; the fixture loads
source via the existing tsx loader with Node capped at 256 MiB.

### Coexistence with other native input surfaces

The bulk and observer lifecycle suites mix SDK receipts with unrelated native
`batch-next`, Host-prepared skill text, audio AttachmentRef, and 17-image
canonical-only envelopes. These are canonical snapshot/notification boundary
fixtures, not evidence of SDK skill/audio ingress. They prove identity-only
observation, redacted live/tail wakeups, reinstallation, unchanged original
fingerprints after edits, and strict requested-ID mode/content, scope, duplicate
identity, same-client lineage, cancellation and Host/Agent-source fences.

`tests/integration/test_queued_native_coexistence.py` reuses the authenticated
real Core fixture and public Gateway/adapter/client with a disposable SQLite
store. Real native `batch-next` admission and a 17-image canonical envelope
using actual Host-imported image bytes coexist with SDK input. Fresh Gateway
startup preserves pending reads, edit-before-restart original fingerprints,
scoped cancellation, and the original final-reply identities, with no duplicate
queue append or foreign queue notice. Native same-client mode conflicts still
reject. This test does not claim a configured native skill loader or real audio
execution; those shapes remain bounded snapshot evidence above.
