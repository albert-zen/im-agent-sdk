# Common commands testing

Required evidence:

- common and product commands share one explicit local frozen registry;
- selected common names may be omitted but never shadowed;
- the exact `ConversationActions` instance reaches handlers;
- unbound reads do not implicitly select the only Application;
- `/new` makes one workflow call, then an explicit observation call, with no
  primitive create/bind sequence;
- delete never calls `clear_thread`;
- request response is Conversation-authorized;
- stable IDs remain distinct across Conversations that reuse a native message
  ID;
- read/list/history/output/view/handler limits remain finite;
- no `SlashController` or `register_common_commands` import succeeds.

Focused owner mirror:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.controllers.test_common_commands \
  tests.interaction.controllers.test_registry -v
```
