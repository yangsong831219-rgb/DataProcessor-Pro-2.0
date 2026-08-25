# Batch 3.6.0 — Host Agent Orchestrator / Source Bundle qualification

## Outcome

Introduce the Host Agent Orchestrator vocabulary and a read-only qualification
Module for the reviewed `ppt-master-main.zip` release.  This batch proves that
the selected package can be identified safely; it does not install or execute
the package and does not change report generation behavior.

## Frozen target files

- `CONTEXT.md`
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/ppt_master_host/source_bundle.py`
- `tests/test_ppt_master_source_bundle.py`
- this scope document
- the final Batch 3.6.0 audit document

## Forbidden changes

- no changes to `main.py`, UI, `AIClient`, Skill Runtime, Report Provider,
  report Bridge/Builder, requirements, or AppData;
- no install, extraction registration, network access, API-key forwarding, or
  execution of archive content;
- no weakening/skipping/deselecting/deleting existing tests.

## Acceptance gates

1. The reviewed 2.7.0 archive is identified by exact SHA-256.
2. Existing production ZIP safety inspection is reused with a tighter,
   package-specific envelope.
3. Version, source URL, MIT license, plugin identity, and controlled-tool files
   are attested from bounded UTF-8 JSON metadata.
4. Special ZIP entries are rejected in addition to existing traversal,
   symlink, encryption, collision, size, depth, count, and ratio checks.
5. Qualification is demonstrably read-only.
6. Focused tests, changed-file static checks, compilation, and scope audit pass.

## Reviewed release

- archive: `D:\桌面文件\ppt-master-main.zip`
- upstream release metadata: `2.7.0`
- SHA-256: `AC2599B467FFF4166EA2C34B62D877B12683391FEB95DE7A8FFCC7892EFFD7AF`
- source: `https://github.com/hugohe3/ppt-master`
- license: MIT
- archive entries: 13,746
- archive bytes: 632,802,950
- uncompressed bytes: 705,145,737

## Feasibility probe

A selectively extracted temporary copy of the deterministic exporter produced
one visually inspected PPTX slide with native DrawingML and no network,
template, or external image.  The probe output was converted through
LibreOffice and inspected as a PNG; text, Chinese fonts, bounds, and layout
were correct.

The upstream bundled Kubernetes example is stale against the 2.7.0 exporter
contract (`<use>` handling and required `spec_lock` fields).  It is therefore
not an acceptance fixture.  Host-owned, contract-valid fixtures will be used.

## Planned sub-batches

- **3.6.1 — Managed Source Bundle store:** selective, transactional extraction
  into a dedicated toolchain store; no Skill Registry entry and no execution.
- **3.6.2 — Host planning state machine:** Pydantic-validated outline, spec,
  slide intent, confirmation, cancellation, and resumable state; model calls
  stay behind the Host `AIClient` Adapter.
- **3.6.3 — Controlled Tool Runner:** exact command/argument allowlist,
  workspace confinement, resource limits, dependency attestation, nested
  process policy, logs, cancellation, and artifact provenance.
- **3.6.4 — PPT Master Report Provider Adapter:** translate
  `ReportRenderRequest` into Host Orchestrator work and retain existing Host
  validation/atomic commit.
- **3.6.5 — UI and template workflow:** Provider selection, staged user
  confirmation, preview, template extraction/normalization, and clear fallback.
- **3.6.6 — final E2E gate:** real diagnosis record, no-template and template
  PPTX, full slide rendering/visual QA, required-image coverage, cancellation,
  failure recovery, and one non-duplicated full regression run.
