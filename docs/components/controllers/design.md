# Controllers component design

## Purpose

Controllers are optional inbound UX. They translate Slash commands, buttons,
cards, or product interactions into the same typed Application and Gateway
operations used by non-text callers.

## Ownership

Controllers owns:

- the `InboundController` and `ControllerActions` UX seam;
- the `InboundContentAdapter` type for consumer-owned content-only adaptation;
- the `InboundFailurePresenter` type for consumer-owned terminal error rendering;
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

The common Slash grammar is line-oriented: only the first non-empty input line
is the command and its arguments. Trailing Channel-supplied or user-supplied
context is not interpreted as control syntax. Channels therefore remain
unaware of the optional Slash product behavior.

After a Controller passes an input through, Gateway may call one configured
`InboundContentAdapter`. The adapter receives the verified envelope but returns
only replacement `Content`; it cannot rewrite message, sender, Conversation,
reply, timestamp, admission, binding, or derived client-message identity. This
is a consumer deployment seam for product-specific mappings such as converting
a staged generic file into an explicit text manifest. It is not a second
Channel normalizer or a source of common attachment semantics. Consumed
Controller inputs never invoke it. The default is identity.

The official Markdown Request Presenter renders typed approval/user-input
requests without inventing policy. The optional Slash Controller maps an
explicit Application instance plus native request ID and choice/answers to the
typed Conversation response operation. Channel-native buttons or cards invoke
that same operation through the Channel operation callback; Core does not
define a universal card format.

All native prompt fields are untrusted presentation input. Approval facts are
rendered in an indented code block, while labels, descriptions, headers, and
questions are escaped before Markdown interpolation. A `secret` question is
not rendered as an `/answer` command: the plain presenter sends an explicit
unsupported notice and returns `response_supported=False`, so projection
creates no response correlation. A future Channel-native presenter may accept
the same typed request only after it proves a secure-input capability.

Gateway may offer a terminal inbound failure to one configured
`InboundFailurePresenter`. The presenter receives the failure phase and fixed
Conversation, reply, and stable delivery identities, and returns only the
`OutboundMessage` rendering. It cannot select another destination or make
retry decisions. The default remains absent, preserving exception propagation
for consumers that own retry or reporting elsewhere.

## Dependencies and state

Controllers depend on Contracts, not Gateway implementation. Gateway supplies
a locked `ControllerActions` implementation.

Controller view caches and content-adapter behavior are ephemeral UX state.
They are not resource,
transcript, or binding authority and may be discarded.

## Failure

Unsupported capability, missing binding, stale reference, invalid arguments,
and native failures are rendered from typed outcomes. A Controller must not
silently approximate destructive operations or turn arbitrary chat text into
unreviewed Core semantics.

An adapter failure, empty result, non-tuple result, or unsupported content item
fails before the normal pass-through binding/thread creation and Application
dispatch, and releases the pre-side-effect inbound claim for an explicit retry.

Failure presentation is terminal for that inbound identity. `pre_acceptance`
means no native Application acceptance was observed; `outcome_unknown` keeps
the existing sticky side-effect fence; `post_acceptance` keeps the completed
claim. Gateway validates presenter routing and uses normal outbound delivery
coordination. If presentation itself fails, inbound safety wins: the terminal
or sticky claim is not reopened to Application dispatch.

## Change obligations

Changes require Slash tests, typed operation validation, presenter snapshots or
focused assertions, and Channel rendering checks when output shape changes.
