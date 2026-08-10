# Gateway input components

Gateway input begins only after durable Channel admission and Conversation
serialization. The leaves keep content transformation, native dispatch truth,
and optional classified failure presentation independent:

- [content transformation](content-transformation/design.md) and
  [testing](content-transformation/testing.md) — replay-safe I1 replacement of
  verified content only;
- [dispatch](dispatch/design.md) and [testing](dispatch/testing.md) — stable
  client identity, prefer-active-Turn behavior, the native side-effect fence,
  and truthful acceptance/correlation;
- [failure presentation](failure-presentation/design.md) and
  [testing](failure-presentation/testing.md) — I2 phase classification and one
  optional terminal error delivery without retry authority.

The ordered path is Controller handling, exact complete binding resolution,
optional I1 after Controller decline, route preparation, then canonical native
dispatch. A Controller may explicitly onboard through scoped actions and pass
the original Message through, but Gateway never selects or creates resources
and no second dispatch path exists. Missing Application/Project/Thread ancestry
is the typed `missing_binding` pre-acceptance failure. Dispatch owns only stable
input identity, the typed native side-effect pre-dispatch fence, the distinct
acceptance-ordering gate, and accepted-Turn correlation; it does not create another Conversation
registry. I2 wraps the owned claimed-input outcome and applies phase-specific
claim rules. These leaves do not create another Channel admission path,
Application subscription, transcript, runtime, spool, or outbox.
