# Batch 3.6.5 — Host UI and template workflow

## Objective

Connect the confirmed Host planning and PPT Master Provider Modules to the
report workbench without weakening their security or publication boundaries.
The batch adds a concrete Host AI Authoring Adapter, explicit confirmation
gates, a 16:9 template-style workspace, plan preview, and visible fallback.

## Architecture

- **Module:** the Host planning workflow owns one immutable Planning Snapshot,
  input fingerprint, template-workspace attestation, preview, and deterministic
  render payload.
- **Interface:** the workbench emits intent and displays phase state; it never
  performs model calls, tool execution, or template file I/O.
- **Implementation:** the Host AI Authoring Adapter uses `AIClient` structured
  generation for one complete SVG at a time and deterministically creates the
  upstream project contracts.
- **Depth:** template intake validates a previously prepared PPTX, extracts its
  bounded style contract, attests a private copy, and commits the workspace
  atomically.
- **Seam:** PPT Master remains a distinct Provider choice.  Provider failure or
  invalid template intake never triggers an automatic Provider replacement.
- **Adapter:** Host planning, Host authoring, and Controlled Runs remain behind
  their existing typed Adapters; no third-party code receives model clients,
  credentials, arbitrary paths, or final publication authority.
- **Leverage:** Batch 3.6.6 can exercise real diagnosis/template exports and
  visual acceptance without changing the planning or Provider contracts.
- **Locality:** changes stay in the Host integration, report workbench/controller
  glue, one public report-context helper, focused tests, and audit documents.

## In scope

- `CONTEXT.md`
- `core/report_engine.py`
- `dp_engine/ppt_master_host/authoring.py`
- `dp_engine/ppt_master_host/template_workspace.py`
- `dp_engine/ppt_master_host/workflow.py`
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/report_provider/ppt_master.py`
- `dp_engine/report_provider/__init__.py`
- `ui/report_workbench.py`
- `main.py`
- `tests/test_ppt_master_host_ui_workflow.py`
- this scope and the final Batch 3.6.5 audit package

## Acceptance gates

1. PPT Master appears only for PPT output with a plausibly installed reviewed
   bundle; the Controlled Runner still performs the authoritative verification.
2. Outline, design, and slide plan require separate user confirmation actions.
3. Input changes invalidate the plan; full generation requires an unchanged,
   `PLAN_CONFIRMED` snapshot.
4. A prepared template is accepted only as a 16:9, digest-attested style
   workspace.  Unsupported input fails with a clear message.
5. Host authoring uses structured model output and the Provider revalidates the
   final SVG and asset roster.
6. Static review passes and one new focused pytest command passes with no
   failures, skips, xfails, or deselection.

## Deferred to Batch 3.6.6

- real model-backed no-template/template exports;
- rendered slide visual QA and template-style fidelity scoring;
- real required-image coverage, cancellation, and failure recovery;
- the one non-duplicated full regression run.
