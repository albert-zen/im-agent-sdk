# Release design

## Purpose

Release support makes the Python package installable, typed, and reproducible
without changing the runtime ownership boundaries. It describes the public
package facade, build metadata, optional dependency groups, wheel contents, and
clean-install smoke.

## Ownership

This leaf owns:

- `pyproject.toml` package metadata and build configuration;
- the stable package/version facade and `py.typed` marker;
- optional dependency extras and their import boundary;
- wheel assembly and clean-install verification; and
- release-facing documentation for the installable artifact.

It does not own Interaction, Gateway, or Applications behavior, compatibility
implementations, a native runtime, a second package facade, or product
configuration. A public symbol has one implementation owner; a facade may
re-export it but must not retain a duplicate.

## Executable test owner

The complete package-independence and release-boundary test mirror lives in
`tests/engineering/test_release.py`. The historical root module
`tests/test_package_independence.py` is intentionally absent; it is not a
compatibility facade and must not be recreated as an import shim. The move
changes only the physical test owner and its repository-root calculation.

## Install boundary

The base package must import without optional native integrations. Optional
extras may add native transport dependencies, but the base artifact must not
silently import them or require a consumer package. The wheel contains the
typed SDK package and its marker; it does not contain transcripts, credentials,
bridge state, delivery jobs, or consumer configuration.

Release checks consume the three runtime layers' public exports and the
language-neutral schemas. They do not redefine those contracts. Package
metadata, source provenance, and any compatibility claim remain explicit and
reviewable. The formal `imagent.contracts` facade deliberately omits the
retired Channel capability/profile/support/reply-scope and delivery-receipt
names, as well as the retired proactive vocabulary and closed
submission-identity helper names. Its permanent finite
`ConversationBinding` delegate is the exact Gateway persistence-state object
needed by the Interaction Controller contract's runtime-resolvable return
hint; it is not a compatibility copy. `DeliverySubmissionOrigin` remains a
documented Gateway persistence/delivery export. The historical
`imagent.adapters` facade retains only its Gateway repository and
proactive-authorization aliases; it has no Applications exports. The focused Interaction
Channel facade and `imagent.channels` adapter facade re-export exact owner
objects and never hide a duplicate or lazy compatibility implementation. The
 Application event surface follows the same rule: `imagent.events`, the event
 aliases in `imagent.contracts`, and the package-root `events` module are
 formal explicit facades over `imagent.applications.events`, not implementation
 owners.
The finite top-level `imagent` lazy resolver is not a compatibility shim. Its
only lazy module names are `adapters`, `contracts`, `delivery_coordination`,
`delivery_planning`, `diagnostics`, `events`, and `projections`. It declares
those names under `TYPE_CHECKING`, resolves each to its exact canonical module
object, and caches that object on the package root, preserving identity for
later access. A cold `import imagent` loads neither Gateway nor optional native
dependencies; resolving a delivery module is an explicit later access, not an
import-time side effect. The separate finite `imagent.applications`
package-root lazy resolver follows the same non-compatibility rule for its own
bounded application exports. A cold `import imagent.applications` likewise
loads neither Gateway nor concrete optional dependencies.
The `imagent.diagnostics` transition facade likewise re-exports the exact
canonical `imagent.interaction.diagnostics`,
`imagent.interaction.channels.diagnostics`, and
`imagent.applications.diagnostics` objects plus the canonical
`imagent.gateway.diagnostics` objects; it retains no diagnostic definitions,
duplicate moved classes, or lazy `__getattr__`.

## Version and publication boundary

The package version is build metadata, not runtime state. A release candidate
must be validated from the repository's locked dependency graph and an
isolated wheel installation. Publishing, external announcements, and
consumer migration are approval and coordination activities outside this
engineering slice; they cannot be inferred from a passing local build.
