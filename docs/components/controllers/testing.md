# Controllers testing

Required scenarios:

- Gateway without a Controller treats Slash-looking text as normal input;
- the default Controller consumes supported commands;
- non-Slash interactions invoke the same typed actions;
- listing never changes selection;
- Thread selection does not imply native activation;
- create/select/observe composition is explicit;
- catch-up/history preserve all completed messages in a Turn;
- unsupported and invalid operations produce user-safe errors;
- Markdown presentation does not take over Channel escaping or segmentation.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_slash_controller \
  tests.test_gateway_operations \
  tests.test_gateway_vertical_slice -v
```
