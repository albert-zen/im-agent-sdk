# DeepSeek Harness Application adapter testing

Component ID: `applications.adapters.deepseek-harness`

## Owner test suite

`tests/applications/adapters/test_deepseek_harness.py`

The suite uses an in-memory double for the DeepSeek Harness Web Host RPC client.
It proves:

- facade identity preservation and zero-network client construction;
- managed Project discovery/reading with native workspace identity;
- Thread create/list/read round-trip and unsupported initial-context failure;
- `send_input` truthfully returning `STARTED`/`CREATE_NEW` after a native
  `turn/start` is observed;
- fan-out parity for two Thread subscribers, with `MESSAGE_COMPLETED` and a
  terminal `TURN_COMPLETED` event;
- history and catch-up scoping through native event grouping.

## Integration evidence

A live DeepSeek Harness Web Host (`pnpm dsh web`, default
`http://127.0.0.1:3080`) can be probed with the same unary RPC envelope the
adapter sends. Real-Key e2e remains separate from this owner suite because the
Host is a pre-release external process.

## Regression gates

Run from the repository root:

```sh
PYTHONPATH=src python -m unittest tests.applications.adapters.test_deepseek_harness -v
```
