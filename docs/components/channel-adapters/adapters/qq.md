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

## Failure and validation

Enabled instances require normalized `app_id` and `client_secret` values and
an HTTP(S) QQ API endpoint. Authentication, reconnect, upload, reply-window,
and unsupported group-file failures remain explicit. Media is staged inside
the Channel-owned bounded spool before it crosses the explicit attachment
source boundary.

## Change checks

Changes to `native/qq.py` or `native/qq_media.py` require
`test_channel_qq.py`, the common native Channel tests, attachment tests, and
the adapter contract suite.
