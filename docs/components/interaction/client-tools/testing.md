# Interaction client-tools testing

## Focused owner evidence

The focused suite lives in `tests/interaction/client_tools/test_send.py`. It
proves the canonical owner and console behavior without importing a Gateway or
Applications implementation. The suite covers:

- exact console-owner metadata and physical absence/unimportability of
  `imagent.cli` in clean processes;
- HTTP/HTTPS numeric-loopback acceptance, remote/name/scheme/URL-credential
  rejection, disabled environment proxies, and exact timeout forwarding;
- exactly-one credential input, empty and unreadable credential failures, and
  credential secrecy on validation and transport failures;
- exclusive Thread-route versus Conversation target construction, required
  nested Application/Project/Thread identity, optional route shape, rejection
  of the former Project-less target, and Conversation route rejection;
- plain and Markdown text, explicitly enumerated regular-file encoding,
  basename/media type/size/base64/SHA-256 identity, empty content, missing
  files, and non-regular-file failures;
- accepted, other typed state, HTTP error status, transport error, and
  non-JSON response exit codes; and
- source/import inspection proving no Gateway or Applications implementation
  dependency and no second handler, server, retry, persistence, registry,
  spool, or outbox.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.interaction.client_tools.test_send -v
PYTHONPATH=src uv run python -m unittest discover -s tests/interaction/client_tools -v
```

## Cross-boundary integration evidence

`tests/gateway/delivery/test_proactive_ingress.py` remains the real Gateway
ingress integration suite. It exercises the transport-neutral JSON handler
through authorization, private decoded-byte staging, common delivery
coordination, typed result mapping, cancellation join, and cleanup. It does
not duplicate focused client argument, local file, endpoint, or response
presentation tests.

Release tests lock the installed `imagent-send` entry point to the canonical
owner. Each of the six isolated wheel cases imports that owner and proves the
historical `imagent.cli` package is absent, so optional extras cannot
accidentally restore the old path.

After focused tests, run every repository gate in `AGENTS.md` and the six
clean-wheel cases documented by the release leaf.
