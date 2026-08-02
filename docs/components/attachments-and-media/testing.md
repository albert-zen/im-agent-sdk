# Attachments and media testing

Required scenarios:

- A1 artifact candidates confer no path/URL trust, and O2 loss on process
  crash cannot make the SDK a durable spool or cleanup ledger;

- no `LocalPath` acceptance without explicit shared-root trust;
- relative and outside-root paths are rejected;
- a valid absolute path inside the configured root resolves;
- undeclared source kinds are rejected;
- `RemoteUrl` is not fetched by an unrestricted common downloader;
- Channel staging produces typed sources rather than Metadata;
- restart duplicates are rejected durably before Channel media preparation,
  and preparation failure releases only the untransferred owned admission;
- proactive ingress refuses unauthorized work before decoding/staging, checks
  declared size, confines paths, preserves content order, and removes staged
  bytes after delivery;
- public proactive `LocalPath` input requires a SHA-256 content identity, and
  native Channel loading rejects bytes that no longer match it;
- Codex/Zen/T3 materialization preserves size/type policy and native errors.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_attachments \
  tests.test_gateway_vertical_slice -v
```
