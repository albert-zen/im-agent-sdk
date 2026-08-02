# Controllers testing

Required scenarios:

- Controller consumption bypasses later inbound-content transformation, while
  an unconsumed message reaches that separate typed position exactly once;
- Controller output and errors do not grant access to Gateway extension state
  or change prefer-active-Turn input semantics;
- Gateway without a Controller treats Slash-looking text as normal input;
- the default Controller consumes supported commands;
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
