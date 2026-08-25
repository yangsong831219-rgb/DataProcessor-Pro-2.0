# Batch 3.6.4 — PPT Master Report Provider Adapter

## Outcome

Add one Host-owned `ReportRenderProvider` Implementation for PPT Master.  The
Adapter consumes a revalidated `PLAN_CONFIRMED` Planning Snapshot, converts the
approved report assets into path-free authoring context, stages only bounded
Host-authored project inputs, executes the reviewed Controlled Run sequence,
and copies one attested PPTX into the report transaction's temporary output.

The existing report Orchestrator continues to attest Provider identity and
destination.  The existing outer report transaction continues to validate the
OOXML package and remains the only final no-clobber publication authority.

## Frozen target files

- `CONTEXT.md`
- `dp_engine/ppt_master_host/controlled_runner.py`
- `dp_engine/report_provider/ppt_master.py`
- `dp_engine/report_provider/__init__.py`
- `tests/test_ppt_master_report_provider.py`
- this scope document
- the final Batch 3.6.4 audit document

## Architecture boundary

- **Module:** `ppt_master.py` owns request/snapshot compatibility, safe asset
  staging, serial SVG authoring cadence, Controlled Run chaining, artifact
  attestation, and atomic delivery to the Host transaction's temporary path.
- **Interface:** a Host authoring Adapter receives immutable, path-free
  Pydantic context and returns one project contract or one SVG page at a time.
- **Implementation:** the PPT Master Provider calls project init, first-page
  quality, final quality, SVG finalization, and PPTX export in exact order.
- **Seam:** `PptMasterControlledRunner` and `PptMasterAuthoringAdapter` keep
  execution policy and model-backed authoring replaceable in focused tests.
- **Depth:** callers select one Provider; workspace safety, content digests,
  SVG restrictions, cadence, receipts, rollback, and artifact copying remain
  internal.
- **Leverage:** Batch 3.6.5 can connect UI planning and a concrete Host AI
  authorer without changing the report transaction or Controlled Tool Runner.
- **Locality:** no Builder, report Bridge, Skill Runtime, model client, UI, or
  final-publication code is modified in this batch.

## Required sequence

1. Revalidate the confirmed Planning Snapshot and the report request.
2. Initialize one fresh controlled project.
3. Stage project specs and approved image assets as Host-owned inputs.
4. Author P01 only, then pass the first-page quality gate.
5. Author P02 through the final page serially without intermediate checks.
6. Pass the final quality gate.
7. Finalize the SVG preview.
8. Export one native PPTX.
9. Recheck the export receipt, file size, and SHA-256.
10. Atomically replace only the outer transaction's temporary output path.

## Fail-closed rules

- Word requests, unconfirmed snapshots, incompatible report/asset plans,
  current template-workspace mode, unsafe assets/SVG, failed Controlled Runs,
  receipt mismatches, cancellation, or non-empty target files do not publish.
- The Host authoring Adapter receives no model credentials and no source or
  output absolute paths.
- Required assets must be assigned by the confirmed plan and referenced by the
  authored SVG page that declares them.
- The real installed PPT Master bundle is not executed in this batch.

## Acceptance gates

1. The Provider satisfies the existing `ReportRenderProvider` Interface and
   passes through `ReportRenderOrchestrator` identity/path attestation.
2. P01 authoring precedes the first-page check; later pages cannot be authored
   if that gate fails.
3. Every later Controlled Run is blocked after the first failed stage.
4. Export receipt/file digest mismatch cannot reach the Host temporary output.
5. Failure and cancellation leave the pre-existing empty Host temp unchanged.
6. Focused tests use a fake runner and authorer only; no real PPTX visual gate,
   live model request, or full regression is repeated.
7. Changed-file Pyright, compilation, capability scan, and frozen-scope audit
   pass with zero warnings/errors attributable to this batch.

## Deferred to later batches

- Batch 3.6.5: concrete Host AI authoring Adapter, UI selection and staged
  confirmation, preview, validated template-workspace intake, and fallback UI.
- Batch 3.6.6: real diagnosis data, real no-template/template exports, render
  and visual QA, required-image coverage, recovery/cancellation, and one full
  non-duplicated regression run.
