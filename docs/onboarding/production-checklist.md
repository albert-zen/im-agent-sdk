# Production checklist

Use the reference consumer to validate composition and lifecycle semantics;
replace its local seams before deploying a real integration.

- [ ] Use a real Channel adapter for authentication, access policy, stable
      native message identity, media staging, native encoding, and receipts.
- [ ] Use a real Agent Application adapter for native resource identity,
      input acceptance, event fan-out, authoritative history, and recovery.
- [ ] Keep one `GatewayStore`, `GatewayLimits`, and frozen Controller explicit.
      Do not assemble private sessions/executors, repositories, a service
      locator, or a mutable context bag.
- [ ] Choose `foreground_only`, `remembered_last_recipient`, or
      `all_observers` deliberately for the consumer’s output policy.
- [ ] Persist Conversation bindings, projection routes, idempotency state, and
      checkpoints with the repository implementation appropriate to the
      deployment. Never persist an SDK transcript or native execution state.
- [ ] Configure finite startup, Application event, projection, delivery, and
      command capacities plus `lifecycle_owner_timeout_seconds`; treat
      overflow, cleanup timeout, and unsupported capabilities explicitly.
- [ ] Pin configured workspace IDs and canonical execution roots. Treat a
      changed root fingerprint as a deployment change requiring a new
      workspace ID; never infer identity from a path or display label.
- [ ] Configure media trust roots explicitly and keep artifact bytes, quotas,
      leases, cleanup ledgers, and startup sweeps consumer-owned. Verify store
      paths and permissions separately from Application workspaces.
- [ ] Ensure the selected store path has durable capacity, one active
      `gateway_id` lease owner, a tested lease-expiry/takeover procedure, and
      authoritative Application history for bounded recovery.
- [ ] Treat the reference Application’s bounded state as process-lifetime demo
      authority only. Production requires durable Application history plus
      durable Gateway repositories; do not treat stop/start of the same Python
      objects as crash recovery.
- [ ] Keep product commands in the consumer’s frozen local `CommandRegistry`
      or another typed Controller composition. Keep native policy in the
      adapter and deployment.
- [ ] Expose `diagnostics()` to the consumer’s own health/metrics
      layer without treating it as authoritative or adding an SDK exporter.
- [ ] Start and stop the Gateway in one owned lifecycle. Join all consumer
      tasks and verify callbacks cannot reach a stopping Application. Prefer
      `async with gateway`; async `gateway.run()` is available for a runner
      that translates its own process signals into cancellation.
- [ ] Run the vertical-slice tests with two Conversations on one Thread,
      switching and switch-back, duplicate identity, and shutdown races; run
      durable SQLite restart evidence separately.
- [ ] Run the repository’s full schema, documentation, static, typing, wheel,
      and clean-install gates before publishing a pull request.

The reference sample intentionally has no credentials, external network,
filesystem trust, attachments, approvals, or product-specific command policy.
Those are deployment and adapter decisions that require their own evidence.
