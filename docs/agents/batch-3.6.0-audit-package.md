# Batch 3.6.0 audit package

## Decision

**PASS — Batch 3.6.0 is complete.**

The reviewed PPT Master 2.7.0 archive is now representable as a qualified,
hash-pinned Source Bundle.  The new Module is read-only and does not install,
extract, import, register, or execute the package.  Execution and product UI
integration remain deliberately closed until later 3.6 sub-batches.

## Architecture review

The selected architecture is viable:

1. The Host Agent Orchestrator owns model access, staged interaction, report
   context, cancellation, and artifact publication.
2. The Source Bundle boundary owns upstream identity and archive safety only.
3. A future Controlled Tool Runner will own the executable boundary and expose
   a small allowlisted Interface; it must not weaken the existing Skill Runtime.
4. A future PPT Master Report Provider will be an Adapter behind the existing
   `ReportRenderProvider` Interface, preserving Provider identity attestation,
   Host validation, and atomic output commit.

This gives the integration Depth: model/state complexity stays behind the Host
Orchestrator; process/toolchain complexity stays behind the Controlled Tool
Runner; the report pipeline receives a narrow Provider Adapter.

## Package qualification evidence

Production qualification call against `D:\桌面文件\ppt-master-main.zip`:

- result: qualified
- version: 2.7.0
- SHA-256: `ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af`
- entries: 13,746
- archive bytes: 632,802,950
- uncompressed bytes: 705,145,737
- required identity/workflow/tool files: 10/10
- extraction/registration/execution: none

The Source Bundle limit envelope is intentionally close to the reviewed
release: 625 MiB archive, 700 MiB expanded, 40 MiB single file, 14,000 entries,
depth 12, path length 160, and compression ratio 20:1.  The candidate's observed
maxima are 35,755,135 bytes for one file, depth 8, path length 122, and ratio
12.65:1.

## Deterministic exporter feasibility evidence

One isolated temporary probe used only the required upstream scripts.  A
Host-authored one-slide SVG and valid `spec_lock.md` produced a native DrawingML
PPTX successfully.  LibreOffice rendered it to PDF and Poppler to PNG; visual
inspection found no clipping, overlap, missing glyph, or Chinese-font failure.

The upstream Kubernetes example did not pass the current 2.7.0 contract because
it uses unsupported `<use>` content and omits required `spec_lock` fields.  The
example mismatch is logged as an upstream-fixture risk, not a failure of the
deterministic exporter.  It was not rerun or used as a product acceptance gate.

## Verification evidence

- focused pytest: **5 passed**
- pytest code/test failures: **0**
- pytest environment warning: **1**, existing `.pytest_cache` creation issue;
  the selected nodes passed and the generated batch temp files were removed
- Pyright, new production Module and tests: **0 errors, 0 warnings**
- `compileall`, new production Module and tests: **PASS**
- changed-source trailing-whitespace scan: **PASS**
- forbidden capability scan: no subprocess, network, extraction, or filesystem
  mutation capability in the production Source Bundle Module
- real archive qualification through the new public Interface: **PASS**

No existing regression suite was repeated.  This batch adds an isolated
read-only Module and leaves all existing runtime/UI/report paths untouched; the
single full regression and real-report visual gate remain scheduled for 3.6.6.

## Scope audit

Baseline before Batch 3.6.0:

- worktree status entries: 458
- tracked changed paths: 50
- staged paths: 0

Batch-owned paths:

- `CONTEXT.md`
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/ppt_master_host/source_bundle.py`
- `tests/test_ppt_master_source_bundle.py`
- `docs/agents/batch-3.6.0-host-orchestrator-scope.md`
- this audit package

The 50 pre-existing tracked changes were not modified by this batch.  No file
was staged or committed.

## Open risks carried to later sub-batches

- The upstream toolchain imports a broad dependency surface.  3.6.1/3.6.3 must
  derive and attest a minimal dependency set instead of installing the full
  upstream requirements blindly.
- The exporter contains optional nested process behavior (`icacls`, narration
  probing).  3.6.3 must explicitly deny, replace, or narrowly allow each path.
- The upstream workflow expects staged confirmation, SVG-by-SVG authoring,
  preview, validation, finalization, and export.  3.6.2/3.6.5 must model those
  states; a one-click opaque subprocess would not satisfy the workflow.
- GitHub Issues synchronization was not performed: the configured connector
  returned bad credentials and the `gh` CLI is unavailable.  No issue status
  change is claimed.
- Codegraph tooling was unavailable in this environment; architecture discovery
  used repository `rg`/read-only inspection as the documented fallback.
