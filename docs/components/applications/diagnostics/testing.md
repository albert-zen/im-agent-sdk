# Applications diagnostics testing

Focused evidence lives in `tests/applications/test_diagnostics.py`. It proves
that the canonical module has an explicit finite `__all__`, owns every
Application diagnostic class and enum exactly once, preserves constructor
signatures and validation messages, and is identical through the
`imagent.diagnostics` transition re-export. The owner-qualified
`ApplicationDiagnosticsProvider` and historical `DiagnosticsProvider` names
are one exact Protocol object, not two provider contracts.

The suite also checks both import orders in clean subprocesses, resolves the
owner's imports statically, and rejects any `imagent.gateway` or transition
facade dependency from the lower Applications owner. Application adapter and
presentation suites continue to prove the unchanged runtime behavior:

- App Server mutable diagnostic state remains
  `imagent.applications.adapters.appserver.diagnostics.AppServerDiagnosticState`;
- Codex, Zen, and T3 use the canonical Application diagnostic contracts;
- live-presentation and artifact-materialization failure/fact bounds,
  redaction, cancellation, capacity, and absence semantics remain unchanged;
  and
- the transition facade contains no duplicate moved definitions or lazy
  resolver.

Run the focused owner and affected adapter/presentation suites together:

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.applications.test_diagnostics \
  tests.applications.adapters.appserver.test_diagnostics \
  tests.applications.presentation.test_live_activity \
  tests.applications.presentation.test_artifact_materialization -v
```

The release smoke repeats the canonical/transition identity and clean base
wheel import checks. Full repository, architecture, component-map, and
six-case clean-install gates remain required for the slice.
