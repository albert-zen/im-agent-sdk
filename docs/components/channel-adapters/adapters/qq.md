# QQ Channel adapter

## Native boundary

The adapter owns QQ bot authentication, Gateway WebSocket reconnect state,
stable C2C/group identities, passive-reply context, QQ media staging, and
native Markdown/file capabilities. It does not own Agent Threads, product
commands, configured allowlists, or deployment policy.

## Routing and state

C2C and group events retain their native message and sender IDs. Group
mentions are stripped only after native targeting succeeds. Passive reply
context is bounded and degrades to proactive delivery when the native reply
window cannot be proven.

QQ quote/reply snapshots are an optional adapter-specific input feature. The
native parser accepts only QQ quote evidence, bounds reference IDs, element
text, attachment count, filenames, voice transcripts, and the final rendered
text, and ignores raw provider envelopes, media URLs, file bytes, and nested
quote history. The QQ adapter appends a labelled untrusted block after the
current text before the common native boundary emits ordinary
`TextFormat.PLAIN` content. Its parsed shape never enters shared native models
or runtime code, and it does not add a generic quote Contract, capability, or
Metadata key.

The quote reference is descriptive provider text only. It is never reused as
an SDK message/delivery/idempotency ID, a Conversation binding, a native reply
target, or request/approval authority. Only the authenticated native inbound
path supplies the provider snapshot. A caller may mimic the label as ordinary
untrusted text, but cannot gain authority or forge a trusted snapshot because
none exists and Metadata is ignored for this feature.

## Failure and validation

Enabled instances require normalized `app_id` and `client_secret` values and
an HTTP(S) QQ API endpoint. Authentication, reconnect, upload, reply-window,
and unsupported group-file failures remain explicit. Media is staged inside
the Channel-owned bounded spool before it crosses the explicit attachment
source boundary.

## Diagnostics

The native adapter publishes only bounded local lifecycle/worker facts and its
fixed-capacity `channel_inbound` queue depth plus process-lifetime overflow
count. It never publishes the session ID, sequence, credentials, endpoint,
Conversation/message identity, media path, or exception text.

## Change checks

Changes to `native/qq.py`, `native/qq_media.py`, or the shared Interaction
adapter endpoint validator require
`test_channel_qq.py`, the common native Channel tests, attachment tests, and
the adapter contract suite. Quote changes additionally require direct/group,
missing, malformed, oversized, nested-payload, anti-forgery, and Gateway
vertical-slice coverage.
