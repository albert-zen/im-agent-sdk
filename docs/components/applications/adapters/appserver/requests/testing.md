# App Server requests testing

## Adapter-owned evidence

`tests/applications/adapters/appserver/test_requests.py` is the authoritative
leaf suite. It covers approval/question/permission mapping, bounded and
adversarial fields, Zen's evidenced command-approval-only surface,
response-shape validation, connection-epoch request identity,
open/respond/resolve wire behavior, duplicate and stale errors, races,
terminal-cache capacity, and bounded diagnostics. The retained
`tests/applications/adapters/appserver/test_gateway_request_integration.py` case verifies Gateway projection,
request-correlation, and presenter integration without moving that ownership
into this leaf.

The tests must preserve exact native response payload choices, scoped request
IDs, first-writer resolution, and explicit unsupported behavior. A reset must
not pretend that a pending snapshot exists.

They also prove the mapping handoff: a valid unknown server request receives
method-not-found without opening a request, while malformed supported requests
receive the fixed redacted invalid-params response before any request event or
pending state is created.

Fixed-workspace evidence verifies native Thread scope once before request
admission or response mutation, reports transient ingress verification failure
as an explicit observation gap, and proves that later publication or bounded
terminal-cache eviction cannot re-read scope and reverse an already successful
native response.

## Target evidence and verification

Run the leaf and retained integration evidence:

```sh
uv run python -m unittest tests.applications.adapters.appserver.test_requests -v
uv run python -m unittest tests.applications.adapters.appserver.test_gateway_request_integration -v
uv run python -m unittest tests.gateway.test_vertical_slice -v
```

Run the full AGENTS gates, component-map/AgentKit checks, and clean-wheel
smoke for the complete adapter block before committing a later move.

## Authority

- [Requests design](design.md)
- [Applications requests design](../../../requests/design.md)
- [Cross-adapter testing context](../../../../application-adapters/testing.md)
