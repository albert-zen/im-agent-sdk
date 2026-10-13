# Startup preparation testing

`tests/gateway/test_startup_preparation.py` crosses the canonical public Gateway
and SQLite boundaries. It proves per-Conversation foreground isolation before
recovery, unaffected output/request recovery, finite capacity, ordered calls,
callback failure/timeout/cancellation, and retained/spawned-task scope rejection.
The lifecycle owner suite separately exercises shared shutdown and lease fences.
