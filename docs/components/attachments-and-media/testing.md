# Attachments and media testing

The focused product-neutral source/trust scenarios are owned by
[`interaction.media`](../interaction/media/testing.md). This broad page
remains migration evidence for tests still physically mixed with Gateway and
Application behavior.

Required scenarios:

- A1 artifact candidates confer no path/URL trust, and O2 loss on process
  crash cannot make the SDK a durable spool or cleanup ledger;
- App Server image-generation and dynamic-tool candidates have stable typed
  identity and finite count/locator budgets before consumer work;
- the async materializer receives no raw notification/client, runs in the
  existing ordered live/history path under finite concurrency/lifetime, and
  returns only bounded typed attachments;
- live final-answer association, artifact-only completed/interrupted/failed
  terminal fallback, duplicate live item suppression, and authoritative
  history reproduction preserve stable candidate and message identity;
- missing native Turn/item identity fails closed, while two native items that
  reuse one untrusted locator retain distinct candidate identity;
- materializer failure/timeout/cancellation/capacity is explicit before output
  emission, terminates the affected live Thread with the fixed recovery gap
  despite production dispatch exception containment, and retains only fixed
  redacted diagnostics; absence preserves exact Codex/Zen behavior;

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
- a deterministic post-open pathname-to-symlink swap cannot change the bytes
  hashed/submitted or escape the configured root;
- bool/float declared sizes fail before file access, and mutation of caller
  attachment metadata after admission cannot alter the snapshotted attempt;
- Codex/Zen/T3 materialization preserves size/type policy and native errors.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.test_media \
  tests.gateway.test_vertical_slice -v
```
