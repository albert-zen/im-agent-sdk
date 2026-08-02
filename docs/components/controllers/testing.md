# Controllers testing

Required scenarios:

- Gateway without a Controller treats Slash-looking text as normal input;
- the default Controller consumes supported commands;
- unconsumed input may be content-adapted without changing envelope identity;
- consumed commands bypass content adaptation;
- invalid adaptation fails before normal pass-through dispatch and releases
  the inbound claim for redelivery;
- the optional inbound failure presenter preserves fixed destination, reply,
  and delivery identities for every failure phase;
- omitting the failure presenter preserves exception propagation;
- trailing input context is not parsed as Slash arguments;
- non-Slash interactions invoke the same typed actions;
- listing never changes selection;
- Thread selection does not imply native activation;
- create/select/observe composition is explicit;
- catch-up/history preserve all completed messages in a Turn;
- unsupported and invalid operations produce user-safe errors;
- Markdown presentation does not take over Channel segmentation;
- untrusted prompt fields cannot escape the approval code block or inject
  Markdown through choice labels/descriptions;
- secret input produces no plain-text command or response correlation;
- approval and structured-input Markdown include stable request/question IDs
  without exposing native transport IDs;
- Slash and simulated Channel-native actions submit identical typed response
  semantics.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_slash_controller \
  tests.test_gateway_operations \
  tests.test_gateway_vertical_slice -v
```
