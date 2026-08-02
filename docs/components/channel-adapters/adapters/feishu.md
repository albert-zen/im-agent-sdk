# Feishu/Lark Channel adapter

## Native boundary

The adapter owns App credentials, the official Channel SDK connection,
Feishu-versus-Lark domain selection, native chat/topic identity, mention
targeting, resource downloads/uploads, and reconnect health. It does not own
Agent lifecycle or consumer product policy.

## Routing and state

Direct chats and topic Threads retain distinct native Conversation IDs.
Completed inbound resources are represented by private native references
until the bounded Channel spool materializes them. The optional SDK is created
with strict transport security and bounded inbound buffering.

## Failure and validation

Only the named Feishu and Lark domains are accepted. Enabled instances require
App credentials. SDK subscription/reconnect, token, resource, queue overflow,
and native delivery failures remain explicit and do not become Agent truth.

The optional diagnostic provider reports only redacted connection/reconnect
worker facts; App identity, tokens, endpoint, and native error text remain
private.

## Change checks

Changes to `native/feishu.py` require `test_channel_feishu.py`, common native
Channel tests, media/attachment tests, and the adapter contract suite.
