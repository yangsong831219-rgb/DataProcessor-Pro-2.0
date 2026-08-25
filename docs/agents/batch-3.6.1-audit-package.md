# Batch 3.6.1 audit package

## Decision

**PASS — Batch 3.6.1 is complete.**

The reviewed PPT Master 2.7.0 Source Bundle is installed in the dedicated
Managed Source Bundle Store.  The installation is selectively extracted,
content-attested, atomically committed, independently reverified, and separate
from the Skill Registry.  The installed files have no execution Interface.

## Installed identity

- path:
  `C:\Users\Administrator\AppData\Local\DataProcessorPro\toolchains\ppt-master\installed\2.7.0\ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af`
- version: `2.7.0`
- source archive SHA-256:
  `ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af`
- selection policy: `ppt-master-host-toolchain-v1`
- installed files: 12,151
- installed upstream bytes: 18,250,613
- installed tree SHA-256:
  `a9e3b28cd98571a1d03e348d882dc9ffc8807e90a2cdb130dd4c79faf1167850`
- staging children after install: 0
- toolchain-local `registry.json`: absent
- Skill Registry registration: none
- bundled code import/execution: none

## Architecture review

`PptMasterBundleStore.install_archive()` is the deep Module Interface.  Behind
it, the Implementation owns Source Bundle requalification, selection limits,
streaming extraction, cancellation, archive-change detection, per-file hashes,
tree hashing, attestation, inventory validation, staging cleanup, idempotency,
conflict refusal, and atomic commit.  Callers do not need to coordinate these
steps, which provides Leverage and keeps transaction bugs local.

The Managed Source Bundle Store is a distinct Seam from the existing Skill
Runtime.  The future Controlled Tool Runner will consume an independently
verified installation; it will not inherit installation authority or modify the
store.

## Verification commands and exact results

Focused lifecycle test command:

```powershell
C:\Python314\python.exe -m pytest .\tests\test_ppt_master_bundle_store.py -q --basetemp .\build_temp\pytest-361-focused
```

Result:

- collected: 6
- passed: 6
- failed: 0
- skipped: 0
- deselected: 0
- environment warnings: 1 (`.pytest_cache` creation warning; batch temp was
  separately removed)

Final static command:

```powershell
C:\Python314\Scripts\pyright.exe .\dp_engine\ppt_master_host .\tests\test_ppt_master_source_bundle.py .\tests\test_ppt_master_bundle_store.py
```

Result: `0 errors, 0 warnings, 0 informations`.

Compilation command:

```powershell
C:\Python314\python.exe -m compileall -q .\dp_engine\ppt_master_host .\tests\test_ppt_master_source_bundle.py .\tests\test_ppt_master_bundle_store.py
```

Result: exit code 0.

Forbidden-capability search covered `subprocess`, network libraries, dynamic
imports, `extractall`, `eval`, `exec`, Skill Registry names, dependency install,
and process launch.  Result: `forbidden_capability_matches=0`.

Independent installed-tree verification result:

```text
verified=True
tree_sha256=a9e3b28cd98571a1d03e348d882dc9ffc8807e90a2cdb130dd4c79faf1167850
files=12151
bytes=18250613
staging_children=0
toolchain_registry=False
```

## Diagnosed installation-gate defect

The first real installation attempt was correctly rejected before commit with
`tree_digest_mismatch`.  The failed transaction left zero staging children and
zero installed attestations.

Three hypotheses were tested against a read-only selection/hash probe.  File
count and byte count matched production exactly.  The old constant matched a
one-off precomputation that framed fields with the literal characters `\0`;
the production Module correctly used actual NUL separators and newline.  The
fixed constant uses the production framing digest shown above.  The original
real-install feedback loop then passed and the committed tree passed a separate
full-file verification.

The full 12,151-file corpus is an external 603 MiB archive and is not suitable
as a portable repository fixture.  The focused suite covers the correct
enforcement behavior—an incorrect pinned tree must reject and roll back—while
the real-package CLI is retained as the release-specific regression gate.

No debug instrumentation or throwaway repository file remains.

## Scope audit

Baseline before Batch 3.6.1:

- worktree status entries: 464
- tracked changed paths: 50
- staged paths: 0

Before adding this audit file:

- worktree status entries: 467
- tracked changed paths: 50
- staged paths: 0

Batch-owned repository paths:

- `CONTEXT.md` — one glossary term added
- `dp_engine/ppt_master_host/__init__.py` — store exports added
- `dp_engine/ppt_master_host/bundle_store.py`
- `tests/test_ppt_master_bundle_store.py`
- `docs/agents/batch-3.6.1-managed-source-bundle-scope.md`
- this audit package

The pre-existing 50 tracked changes were not modified by this batch.  No file
was staged or committed.  The only external write was the explicitly scoped
toolchain installation path above; the source ZIP was not moved or modified.

## Deferred gates

- No existing regression suite was repeated.  This batch adds a new isolated
  store and does not connect existing runtime, UI, or report paths.  The single
  full regression remains reserved for Batch 3.6.6.
- No PPT Master command has been executed from the installed store.  Command
  allowlisting, dependency attestation, process confinement, and logs belong to
  the Controlled Tool Runner batch.
- GitHub Issues synchronization remains unavailable because connector
  credentials are invalid and the `gh` CLI is absent; no issue update is
  claimed.
- Codegraph tooling was unavailable; repository exploration used the approved
  `rg` fallback.

## Next batch

Batch 3.6.2: implement the Host planning state machine with Pydantic-validated
outline/spec/slide-intent states, explicit confirmation, cancellation, and a
Host-owned `AIClient` Adapter.  It will not execute the installed toolchain.
