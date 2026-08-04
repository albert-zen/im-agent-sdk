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

## Install boundary

The base package must import without optional native integrations. Optional
extras may add native transport dependencies, but the base artifact must not
silently import them or require a consumer package. The wheel contains the
typed SDK package and its marker; it does not contain transcripts, credentials,
bridge state, delivery jobs, or consumer configuration.

Release checks consume the three runtime layers' public exports and the
language-neutral schemas. They do not redefine those contracts. Package
metadata, source provenance, and any compatibility claim remain explicit and
reviewable.

## Version and publication boundary

The package version is build metadata, not runtime state. A release candidate
must be validated from the repository's locked dependency graph and an
isolated wheel installation. Publishing, external announcements, and
consumer migration are approval and coordination activities outside this
engineering slice; they cannot be inferred from a passing local build.
