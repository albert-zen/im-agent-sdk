# Weixin iLink Channel adapter

## Native boundary

The adapter owns consumer-enrolled iLink credentials, direct-message polling,
context tokens needed for native replies, bounded reconnect state, media
crypto/transport, and credential file protection. Enrollment UI and policy
remain consumer responsibilities.

## Routing and state

Only official direct user identities are normalized. Group and bot messages
are rejected because the transferred integration does not prove those
capabilities. Context tokens and `get_updates` cursors are minimal
Channel-owned delivery/reconnect state, not transcript or Turn truth.

## Failure and validation

The transport accepts only the official HTTPS Weixin origin. Corrupt,
world-readable, wildcard-owner, or wrong-shape credential state fails closed.
Stale credentials require the consumer enrollment flow; the SDK does not ship
product login commands.

## Diagnostics

The native adapter publishes only bounded local polling lifecycle/worker
facts. Weixin polling does not own an SDK inbound queue, so diagnostics do not
synthesize one and never expose credentials, context tokens, cursors,
endpoints, native user/message identity, media paths, or exception text.

## Change checks

Changes to `native/weixin.py`, `native/weixin_ilink.py`, or
`native/weixin_state.py` require `test_channel_weixin.py`, common native
Channel tests, media/attachment tests, and the adapter contract suite.
