# Request presentation testing

Required scenarios:

- approval and structured-input requests render stable
  Application/request/question/choice identities;
- fixed Conversation, delivery, and reply identities are preserved exactly;
- untrusted approval facts cannot escape the indented code block;
- labels, descriptions, headers, prompts, and questions cannot inject
  Markdown structure;
- cardinality and free-text rules are represented without inventing policy;
- secret input emits no plain-text answer command, reports
  `response_supported=False`, and creates no response correlation;
- response correlation is created only after accepted/already-completed
  delivery and only for the delivered destination;
- one route's rendering/delivery failure neither authorizes that route nor
  blocks another route;
- typed request, output item, and output text bounds fail explicitly;
- replay of identical facts is semantically stable and does not mutate native
  request state;
- reconnect without authoritative pending-request replay never reconstructs a
  request from Gateway correlation;
- mechanical moves preserve exact public object identity and leave no second
  implementation.

Focused validation currently includes:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.controllers.test_request_presentation \
  tests.test_appserver_requests \
  tests.test_gateway_operations -v
```

The physical extraction places focused rendering/security and exact-owner
identity coverage in
`tests/interaction/controllers/test_request_presentation.py`. Request routing,
delivery authorization, and reconnect cases remain with their
Gateway/Application owners. Both the owning Interaction import and the finite
`imagent.controllers` facade must resolve to the same public objects.
