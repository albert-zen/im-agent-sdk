# Controller contract testing

Required scenarios:

- no configured Controller preserves ordinary input behavior, including text
  beginning with `/`;
- `None` continues to I1 exactly once, while any tuple consumes the input and
  bypasses I1 and Application input dispatch;
- an empty tuple is a consumed, intentionally silent result;
- outputs for another Conversation fail before native delivery;
- the Controller receives the immutable inbound envelope and only typed
  `ControllerActions`;
- Application and Gateway actions use their typed operation/result contracts;
- binding lookup is read-only and does not imply route mutation or native
  activation;
- one Conversation lane serializes Controller invocation with binding/input
  work;
- cancellation before any effect remains replay-safe;
- an effectful handler is not invoked until the durable effect fence succeeds,
  and the handler never receives claim/fence authority;
- the fence accepts only the complete current inbound/command identity, can be
  entered once, and has no release/complete/reopen operation;
- an unfrozen lifecycle-capable Controller fails before Gateway input
  acceptance, and shutdown closes its bounded work after Channel shutdown;
- known pre-side-effect failure, unknown action outcome, and post-effect
  presentation failure do not collapse into the same retry decision;
- no Controller work runs on Channel or Application socket-read callbacks;
- mechanical moves preserve exact formal-facade object identity and runtime
  type hints without leaving a second implementation or indefinite internal
  import path;
- a clean process cannot discover or import `imagent.controllers`, regardless
  of whether the formal Interaction facade is imported first.

Focused validation currently includes:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.controllers.test_contract \
  tests.test_slash_controller \
  tests.test_gateway_operations \
  tests.test_gateway_vertical_slice \
  tests.test_projection_hardening -v
```

Exact formal-facade identity, owner placement, runtime type hints, and
clean-process/import-order absence are tested in
`tests/interaction/controllers/test_contract.py`. Architecture lint must
prove that the owning leaf imports no Gateway or Application implementation.
The existing Gateway/Slash suites remain behavior parity evidence until their
focused owners move.
