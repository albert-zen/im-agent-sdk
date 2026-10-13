# ADR 0018: Explicit adapter-owned Thread creation profiles

Status: Accepted

## Evidence and decision

IMZen's next-Thread permission choice and the existing Codex/App Server
`thread_start_options` demonstrate a native creation policy that must survive
the v1 scoped create-and-bind workflow. Mutating an adapter default or using a
task-local side channel hides the selected policy from durable action identity.
Accepting an arbitrary options dictionary in Gateway instead expands consumer
policy and native wire vocabulary into the common action contract.

The optional common capability is only an immutable, bounded creation-profile
identity: `ThreadCreationProfile(profile_id, fingerprint)`. Concrete App Server
adapters accept a bounded `thread_creation_profiles` mapping at construction,
copy/validate the native JSON options, and advertise their profile IDs and
SHA-256 content fingerprints. Native option names and permission meanings stay
inside that adapter configuration. T3 and other adapters advertise no profiles
unless they implement them; an explicit unsupported profile never falls back.

Scoped `create_thread` and `create_and_bind_thread` accept an optional
`creation_profile` ID. Before forming any durable effect, the action owner
resolves it from the selected Application's validated capability declaration.
The exact ID and resolved configuration fingerprint enter both the immutable
`CreateThread` intent and the action payload fingerprint. The concrete adapter
checks that exact declared identity before native creation. Changing native
configuration under the same ID across restart therefore conflicts with an old
action instead of silently replaying a different policy. Caller mutation of the
configured mapping cannot alter the captured profile. No native option content
is persisted in SDK effect receipts.

Unknown selections also enter the existing effect executor: their fingerprint
contains the requested ID with an unresolved (`null`) configuration marker,
and preflight writes a terminal unsupported receipt without native dispatch.
An exact unknown selection replays that failure. Registering the profile later
changes its configuration fingerprint, so the same action conflicts rather than
creating a Thread after a previously terminal rejection.

Schema validation enforces descriptor bounds and creation support. Profile-ID
uniqueness is additionally a required semantic validation step; JSON Schema's
`uniqueItems` alone only detects equal complete descriptors.

Omitting the profile preserves existing default behavior and existing action
fingerprints. A profile is a complete per-call native options selection, not a
mutable default or implicit merge. It cannot replace adapter-owned workspace
fields. Selection does not modify any existing Thread.

## Verification

Use public Gateway with production Zen/Codex adapters: distinct profiles map to
the correct exact native create options, same action/same profile replays once,
changed profile or same ID with changed content conflicts after SQLite restart,
unknown profiles durably reject before native effects and cannot become creates
after registration/restart, concurrent choices cannot bleed,
and caller-owned nested configuration mutation has no effect. Validate Python
and language-neutral capability/operation schemas plus unsupported adapters.
