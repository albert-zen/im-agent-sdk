# Gateway delivery planning testing

Focused tests mirror the owner at
`tests/gateway/delivery/test_planning.py` and prove:

- deterministic golden plans for plain/Markdown and distinct Channel profiles;
- UTF-8/code-point measurement and stable segment/source indexes;
- text/attachment order, grouping family/count limits, and explicit reply scope;
- unsupported/unmeasurable media or invalid finite limits fail before send;
- planner values exported by `imagent.gateway.delivery` are the exact owner
  objects; and
- the historical `imagent.delivery_planning` implementation module is absent.

Coordinator cases live in the adjacent focused
`tests/gateway/delivery/test_coordination.py` suite. Cross-component tests prove
that planner output is consumed without changing ordering, backpressure,
retry, receipt, or Gateway behavior.
