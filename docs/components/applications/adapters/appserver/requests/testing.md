# App Server requests testing

## Current evidence

`tests/test_appserver_requests.py` is the current authoritative suite. It
covers approval/question/permission mapping, bounded and adversarial fields,
Zen's evidenced command-approval-only surface, response-shape validation,
connection-epoch request identity, open/respond/resolve wire behavior,
duplicate and stale errors, races, terminal-cache capacity, and bounded
diagnostics. The Gateway vertical cases verify projection integration without
moving route correlation into this leaf.

The tests must preserve exact native response payload choices, scoped request
IDs, first-writer resolution, and explicit unsupported behavior. A reset must
not pretend that a pending snapshot exists.

## Target evidence and verification

The future mirrored path is
`tests/applications/adapters/appserver/test_requests.py`. Before physical
migration run:

```sh
uv run python -m unittest tests.test_appserver_requests -v
uv run python -m unittest tests.test_gateway_vertical_slice -v
```

Run the full AGENTS gates, component-map/AgentKit checks, and clean-wheel
smoke for the complete adapter block before committing a later move.

## Authority

- [Requests design](design.md)
- [Applications requests design](../../../requests/design.md)
- [Cross-adapter testing context](../../../../application-adapters/testing.md)
