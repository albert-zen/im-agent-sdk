# Command registry testing

Required scenarios:

- two independent registry instances have no shared mutable registrations;
- imports have no registration side effects;
- explicit registration and an instance-bound decorator produce the same
  typed definition;
- duplicate canonical names, aliases, and normalized name/alias collisions
  fail at startup;
- freeze is irreversible and registration after freeze fails;
- invalid/empty names and every configured non-finite or non-positive bound
  fail before startup;
- entry, alias, input-line, argument-count, argument-length, result-item,
  result-text, concurrency, handler-lifetime, cancellation-join, and view-cache
  bounds are enforced at their documented boundary;
- parsing considers only the first non-empty bounded input line and never
  treats trailing context as arguments;
- one invocation selects exactly one handler and aliases resolve to the same
  canonical identity;
- malformed/unknown commands have bounded typed presentation while ordinary
  content stays unconsumed;
- SDK common and consumer product commands compose in one registry without
  replacement or shadowing;
- product handlers receive only their constructor-injected typed service plus
  the documented invocation/action surface;
- replay-safe/read-only and effectful definitions have a closed typed safety
  classification;
- an effectful handler/product service is not invoked before durable fence
  success, and fence failure remains known pre-side-effect;
- the registry passes the complete scoped invocation identity to the one-way
  fence and gives handlers only the fence-free action view;
- known pre-side-effect failure can be distinguished from an unknown outcome;
- the registry never retries a handler automatically;
- cancellation before the effect fence is replay-safe, while cancellation
  racing an effect is classified conservatively;
- timed-out/overrun work retains capacity until joined and shutdown bounds and
  joins admitted tasks;
- output validation rejects identity changes or unbounded results before
  delivery;
- presentation/delivery failure after an effect cannot invoke the handler a
  second time;
- omission of the registry/controller preserves existing ordinary input and
  common-command behavior counterexamples remain explicit.

The implementation slice adds
`tests/interaction/controllers/test_registry.py`, focused Gateway claim/replay
tests, static type checks for handler/result protocols, and architecture lint
coverage forbidding global registration and implementation-layer imports.
