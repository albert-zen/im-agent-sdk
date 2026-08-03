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

Current evidence is in `tests/test_native_channels.py` and the four
Channel-specific suites, plus admission/media/restart vertical tests. Target
placement is `tests/interaction/channels/test_ingress.py`.
