# Controllers component design

## Purpose

Controllers are optional inbound UX. They translate Slash commands, buttons,
cards, or product interactions into the same typed Application and Gateway
operations used by non-text callers.

## Ownership

Controllers owns:

- the `InboundController` and `ControllerActions` UX seam;
- the SDK-provided common Slash grammar;
- selection and command-flow presentation state;
- default Markdown help, lists, history, catch-up, and error rendering.

It does not own:

- Message/Operation semantics;
- Gateway binding or route mutation;
- native Application resources or execution;
- Channel-specific Markdown escaping, segmentation, or native cards;
- product-only commands, model/provider selection, or permission policy.

## Flow

Gateway offers an inbound message to the configured Controller. The Controller
returns `None` when it does not consume the message. If consumed, it calls only
typed actions and returns one or more `OutboundMessage` requests.

The default Slash Controller may explicitly compose operations. For example,
selecting a Thread can bind input and observe output, while native activation
remains a separate optional action. The composition never changes the meaning
of the underlying operations.

Current common commands cover help, Application/Project/Thread navigation,
creation/deletion/status, catch-up, and history. Natural-language intent or
native buttons can replace the parser without changing Core.

## Dependencies and state

Controllers depend on Contracts, not Gateway implementation. Gateway supplies
a locked `ControllerActions` implementation.

Controller view caches are ephemeral UX state. They are not resource,
transcript, or binding authority and may be discarded.

## Failure

Unsupported capability, missing binding, stale reference, invalid arguments,
and native failures are rendered from typed outcomes. A Controller must not
silently approximate destructive operations or turn arbitrary chat text into
unreviewed Core semantics.

## Change obligations

Changes require Slash tests, typed operation validation, presenter snapshots or
focused assertions, and Channel rendering checks when output shape changes.
