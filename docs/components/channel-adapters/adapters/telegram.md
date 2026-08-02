# Telegram Channel adapter

## Native boundary

The adapter owns Bot API polling offsets, bot identity, private/group/forum
Conversation normalization, mention targeting, native reply IDs, and Telegram
media downloads/uploads. It does not own product commands, Agent resources,
or consumer admission choices.

## Routing and state

Private chats, groups, and forum topics are distinct native routes. Polling
offsets are Channel reconnect state, not Agent event cursors. Group text and
media are admitted only when native mention/reply targeting succeeds.

## Failure and validation

Enabled instances require a direct token or a private token file and an
HTTP(S) API endpoint without embedded credentials. Corrupt offsets fail
closed; Bot API descriptions are surfaced without leaking tokens.

## Diagnostics

The native adapter publishes only bounded local polling lifecycle/worker
facts. Telegram polling does not own an SDK inbound queue, so diagnostics do
not synthesize one and never expose the token, offset, endpoint, native chat,
message identity, or exception text.

## Change checks

Changes to `native/telegram.py` require `test_channel_telegram.py`, common
native Channel tests, media/attachment tests, and the adapter contract suite.
