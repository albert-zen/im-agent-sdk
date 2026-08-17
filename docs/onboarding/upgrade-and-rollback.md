# Upgrade and rollback

Treat a deployment as one provenance packet: source tag and commit, package
version, wheel URL and filename, wheel SHA-256, and installed `direct_url.json`
must agree. Keep the prior packet and a stopped, consistent Gateway-store
backup until the new deployment passes its vertical checks.

## Released alpha installation

`v0.1.0a1` is distributed from
[GitHub Releases](https://github.com/albert-zen/im-agent-sdk/releases/tag/v0.1.0a1).
The repository is private: GitHub authentication with read access to the
repository is required, and the browser asset URL is not an anonymously
pip-installable URL. This guide does not claim that the package is published
on PyPI.

The exact released packet is:

| Fact | Expected value |
|---|---|
| Git tag | `v0.1.0a1` |
| Annotated tag object | `82bab131ad292f4e5e036704c3a9e26de074d18d` |
| Peeled tag commit | `a72b24a2558c8b2aa48b05214588da4fc1a434db` |
| Package version | `0.1.0a1` |
| Wheel | `im_agent_sdk-0.1.0a1-py3-none-any.whl` |
| Wheel SHA-256 | `123ebe9c7b086c9187bc05ce961f4942a6f8664de6a752f4e289d761c4e98d76` |
| Wheel URL | `https://github.com/albert-zen/im-agent-sdk/releases/download/v0.1.0a1/im_agent_sdk-0.1.0a1-py3-none-any.whl` |

Download the GitHub asset with authenticated GitHub CLI into a temporary
directory, verify it, and then install it into a fresh Python 3.13+
environment:

```sh
gh auth status
download_dir="$(mktemp -d)"
gh release download v0.1.0a1 \
  --repo albert-zen/im-agent-sdk \
  --pattern 'im_agent_sdk-0.1.0a1-py3-none-any.whl' \
  --pattern 'SHA256SUMS' \
  --dir "$download_dir"
(cd "$download_dir" && sha256sum --check SHA256SUMS)

python -m venv .venv-imagent-0.1.0a1
.venv-imagent-0.1.0a1/bin/python -m pip install --upgrade pip
.venv-imagent-0.1.0a1/bin/python -m pip install \
  "$download_dir/im_agent_sdk-0.1.0a1-py3-none-any.whl"
```

On Windows PowerShell, create a temporary directory with
`$downloadDir = New-Item -ItemType Directory -Path ([IO.Path]::Combine([IO.Path]::GetTempPath(), [IO.Path]::GetRandomFileName()))`,
pass `$downloadDir.FullName` to `gh release download --dir`, verify with
`Get-FileHash -Algorithm SHA256`, and use
`.venv-imagent-0.1.0a1\Scripts\python.exe` for pip. Install only the optional
dependencies required by the deployment; for example, install the verified
wheel and then install its declared `appserver` dependencies from the
deployment's locked dependency set. Do not install every integration by
default.

## Verify the installed packet

Record both the annotated tag object and its peeled commit independently from
the wheel. The unpeeled ref identifies the tag object, not the source commit:

```sh
# Remote repository evidence (requires GitHub repository read access).
git ls-remote --tags https://github.com/albert-zen/im-agent-sdk.git \
  refs/tags/v0.1.0a1 'refs/tags/v0.1.0a1^{}'

# Local checkout evidence after fetching the exact tag.
git fetch --no-tags https://github.com/albert-zen/im-agent-sdk.git \
  'refs/tags/v0.1.0a1:refs/tags/v0.1.0a1'
test "$(git rev-parse 'refs/tags/v0.1.0a1')" = \
  '82bab131ad292f4e5e036704c3a9e26de074d18d'
test "$(git rev-parse 'refs/tags/v0.1.0a1^{}')" = \
  'a72b24a2558c8b2aa48b05214588da4fc1a434db'
```

Require the remote command to return both exact object/ref pairs:

```text
82bab131ad292f4e5e036704c3a9e26de074d18d refs/tags/v0.1.0a1
a72b24a2558c8b2aa48b05214588da4fc1a434db refs/tags/v0.1.0a1^{}
```

The two local assertions verify the same hashes in role order: first the
annotated tag object `82bab131ad292f4e5e036704c3a9e26de074d18d`, then the
peeled source commit `a72b24a2558c8b2aa48b05214588da4fc1a434db`. Keep the
peeled ref single-quoted so shells do not interpret its punctuation. If the
local tag already exists, first verify it rather than force-moving it; use a
disposable checkout for provenance verification when its identity is
uncertain.

Then run this inside the target environment with `WHEEL_PATH` set to the exact
downloaded wheel path. It checks package metadata, runtime facade, the local
artifact URL, and the archive hash recorded by pip. The deployment manifest
must retain the GitHub release asset URL from the table above alongside this
local install record:

```python
import json
import os
from importlib import metadata
from pathlib import Path

import imagent

EXPECTED_VERSION = "0.1.0a1"
EXPECTED_HASH = "sha256=123ebe9c7b086c9187bc05ce961f4942a6f8664de6a752f4e289d761c4e98d76"
wheel_path = Path(os.environ["WHEEL_PATH"]).resolve(strict=True)
assert wheel_path.name == "im_agent_sdk-0.1.0a1-py3-none-any.whl"

distribution = metadata.distribution("im-agent-sdk")
direct_url = json.loads(distribution.read_text("direct_url.json"))

assert metadata.version("im-agent-sdk") == EXPECTED_VERSION
assert imagent.__version__ == EXPECTED_VERSION
assert direct_url["url"] == wheel_path.as_uri()
assert direct_url["archive_info"]["hash"] == EXPECTED_HASH
```

If `direct_url.json` is absent, the environment does not prove installation
from the approved direct artifact. If any value disagrees, quarantine the
environment; do not relabel it or infer equivalence from import success.

For an already downloaded wheel, verify it before installation if the local
platform lacks `sha256sum --check`:

```sh
python -c "import hashlib,pathlib; p=pathlib.Path('im_agent_sdk-0.1.0a1-py3-none-any.whl'); print(hashlib.sha256(p.read_bytes()).hexdigest())"
```

Compare the output with both the value above and the authenticated release's
`SHA256SUMS` asset. Install that exact file so `direct_url.json` records its
resolved local `file:` URL and archive hash. Retain the GitHub asset URL, tag
commit, authenticated download command, and checksum in the deployment
manifest. Do not substitute the browser asset URL as an anonymous pip source;
it returns 404 for callers without repository access.

## Upgrade procedure

1. Resolve and record the candidate tag commit, version, wheel URL/name,
   checksum, and expected `direct_url.json` before touching production.
2. Build a fresh environment; never layer retired pre-v1 modules or import
   shims over the candidate.
3. Run the installed reference executable and the consumer's real Channel →
   Gateway → Application vertical scenario from outside the source checkout.
4. Verify configured workspace IDs and canonical roots, Channel/Application
   capabilities, store path, `gateway_id`, limits, projection policy, and
   consumer-owned credentials/trust roots.
5. Stop the old Gateway cleanly and confirm its store lease is closed. Quiesce
   consumer writers and take a consistent backup with SQLite-aware backup
   tooling that includes committed WAL state. Do not raw-copy only the main
   database while sidecars may contain committed changes.
6. Start exactly one candidate Gateway over the intended store. Check typed
   startup results and the bounded diagnostics snapshot, then test binding,
   ordinary input/output, request/media/proactive paths that the deployment
   uses, and a fresh-object restart.
7. Keep the old environment, provenance packet, configuration, and pre-upgrade
   store backup until the acceptance window closes.

The SDK does not own credential migration, product commands, permissions,
artifact storage, monitoring, or deployment orchestration. Validate those in
the consumer without moving them into SDK Core.

## Rollback procedure

1. Stop the candidate and confirm its lease is closed or has expired according
   to store-authoritative time. Never start the prior runtime concurrently.
2. Preserve the failed candidate's environment, store, and fixed diagnostic
   evidence for investigation; do not edit receipts or convert unknown work to
   retryable.
3. Determine whether the candidate changed persistent bridge schema/state. If
   the prior release has not explicitly documented forward compatibility,
   restore the stopped pre-upgrade store backup together with the prior
   environment. Do not point older code at a newer store on assumption.
4. Reinstall or reactivate the exact prior provenance packet and rerun its
   version/URL/hash checks. Restore the exact prior stable workspace IDs,
   roots, `gateway_id`, capabilities, and consumer configuration.
5. Start one fresh Gateway object graph and verify authoritative Application
   history reconciliation, bindings, checkpoints, duplicate suppression, and
   the deployment's used request/media/proactive paths.
6. Reconcile `Partial` and `OutcomeUnknown` actions by their original stable
   IDs. Never invent a new ID, blindly resend, compensate with native delete,
   or copy state into a compatibility runtime.

Rollback is a deployment/store operation, not a compatibility promise. A v1
deployment must not restore Project-less resources, aggregate executors,
historical import facades, implicit onboarding, or any other retired
architecture.

## Release evidence

Repository release authority and executable gates are documented in
[release design](../engineering/release/design.md) and
[release testing](../engineering/release/testing.md). The same installed
consumer path is specified by the
[v1 executable specification](../V1_EXECUTABLE_SPEC.md).
