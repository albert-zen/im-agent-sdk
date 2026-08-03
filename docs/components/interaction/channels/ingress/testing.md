# Channel ingress testing

Required scenarios:

- authentication and allow/deny policy precede media/network/filesystem work;
- all native identity fields are stable, bounded, and normalized without text
  or timestamp idempotency fallbacks;
- durable completed/in-flight duplicates stop before preparation;
- preparation failure releases only its owned lease, while stale ownership
  cannot hand off or release a replacement claim;
- cancellation before handoff is reclaimable and cancellation after callback
  entry remains Gateway-owned;
- media count/type/size/source/path/decoded-size and native metadata bounds are
  enforced before constructing the message;
- queue overflow and shutdown are explicit and joined; socket readers do not
  execute downstream consumer work;
- restart redelivery and native cursor/fast-path behavior preserve the durable
  admission authority; and
- QQ, Telegram, Feishu, and Weixin counterexamples all use the same ordering.

Focused policy evidence lives in
`tests/interaction/channels/test_ingress.py`: configuration parsing,
unrestricted and deny-all sentinels, `any`/`all` matching, and invalid mixed or
unknown modes. The four Channel suites prove provider configuration uses the
same owner and access still precedes admission/media work. Admission,
media/restart, queue, and shutdown evidence remains in the native and vertical
suites until those mechanics move in later focused slices.

Focused tests also lock the leaf-internal inbound attachment tuple/defaults;
provider and vertical suites continue proving normalization into the public
Interaction message without treating the mutable native DTO as a contract.

Windows staging-security tests prove the helper is a no-op off Windows, is
owned only by Interaction ingress, and has no historical native import path.
Windows CI and the existing media materialization suite remain responsible for
the unchanged current-user SID, protected DACL, file/directory flag, and
failure behavior.

Media ownership tests prove the image/file materializers and result/error
values have one Interaction ingress owner and that the historical native media
module/package are absent. Existing four-provider, native Channel, admission,
restart, cancellation, quota, and vertical suites remain the behavioral proof
for the mechanically moved transaction boundary.
