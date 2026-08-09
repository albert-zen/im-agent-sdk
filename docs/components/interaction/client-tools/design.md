# Interaction client-tools design

## Purpose and boundary

`interaction.client-tools` owns the optional stateless tools that let a local
caller construct one request for a consumer-hosted SDK ingress. The reference
`imagent-send` command owns local argument parsing, numeric-loopback endpoint
validation, scoped-credential input, request-scoped local artifact encoding,
and presentation of the typed ingress response.

This leaf is a client of the public transport boundary. It does not import or
implement Gateway or Applications code and does not create another delivery
path. Gateway proactive delivery remains the sole owner of intent validation,
authentication and authorization, route resolution, planning, coordination,
idempotency, receipts, and recovery. The client owns no Channel admission,
Agent truth, transcript, credential store, durable content, spool, outbox,
retry scheduler, web server, generic hook, service locator, or global
registry.

## Local input and security rules

The reference command accepts exactly one credential source: an explicitly
named UTF-8 file or standard input. It trims surrounding whitespace, rejects
an empty credential, sends the value only in the Bearer header, and does not
include it in the request body or response presentation. HTTP environment
proxy configuration is disabled for the request.

The endpoint must use HTTP or HTTPS, must not contain URL credentials, and
must resolve syntactically to the numeric loopback host `127.0.0.1` or `::1`.
Names such as `localhost`, remote addresses, and other URL schemes are
rejected before any HTTP request. The caller-provided timeout is forwarded to
the one synchronous request; the client performs no retry.

Exactly one target shape is constructed:

- a Thread-route target requires Application, Project, and native Thread
  identity in one nested `ThreadRef`, with only the route selector optional; or
- an explicit Conversation target requires both Channel and native
  Conversation identity and rejects a route selector.

These JSON shapes are transport input to the consumer-hosted ingress, not a
second Python `DeliveryIntent` contract or routing implementation.

## Content encoding

Text preserves the caller's value and is marked plain unless `--markdown` is
present. Markdown without text is rejected. Each explicitly enumerated
artifact must resolve to an existing regular file. The client reads it for
this one request, records its basename, detected or fallback media type,
length, SHA-256-derived stable attachment identity, and inline base64 bytes.
It never sends a caller-controlled server path or retains the bytes after the
process request completes.

This is a request-scoped local encoding boundary, not the authoritative
admission bound. Gateway ingress remains responsible for authorization before
decode and for decoded byte/count limits, private staging, synchronous
lifetime, and cleanup.

## Response and exit contract

The client prints a decoded JSON response as formatted UTF-8 JSON. An
`accepted` state under a successful HTTP status exits 0. Local validation,
filesystem, HTTP/transport, non-JSON, and HTTP error-status failures exit 2.
A successful HTTP response carrying another typed state exits 3. Failure
messages report the local or transport error and never deliberately print the
credential.

## Public surface and recovery

The installed `imagent-send` console script resolves directly to
`imagent.interaction.client_tools.send:main`. The canonical implementation is
the only owner. The historical `imagent.cli` package is physically absent and
has no shim or alias.

The tool is stateless and one-shot. Stable delivery identity is supplied by
the caller and interpreted by Gateway; the client persists no replay or
recovery evidence.

## Authority

- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
- [ADR 0003](../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0009](../../../decisions/0009-proactive-delivery-routing.md)
- [Gateway proactive delivery](../../gateway/delivery/proactive-delivery/design.md)
