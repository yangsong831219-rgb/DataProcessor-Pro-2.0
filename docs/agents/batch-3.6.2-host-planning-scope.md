# Batch 3.6.2 — Host Planning State Machine

## Outcome

Create a Host-owned Planning State Machine for PPT Master report planning.  It
produces Pydantic-validated outline, design contract, and slide-intent stages;
requires explicit user confirmation; supports immutable serialization,
cooperative cancellation, and resume; and calls the configured model only
through a Host `AIClient` Adapter.

This batch does not execute PPT Master, write project artifacts, generate SVG,
or change the current report UI/pipeline.

## Frozen target files

- `CONTEXT.md` — add Planning Snapshot and Planning State Machine terms only
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/ppt_master_host/planning.py`
- `tests/test_ppt_master_host_planning.py`
- this scope document
- the final Batch 3.6.2 audit document

## Forbidden changes

- no changes to `core/ai_client.py`, UI, `main.py`, Report Provider,
  report Bridge/Builder, Skill Runtime/Registry, requirements, or AppData;
- no bundled-tool import or execution, subprocess, network client, filesystem
  write, template processing, SVG/PPTX creation, or dependency installation;
- no direct API-key access or propagation beyond the existing Host `AIClient`;
- no `json.loads` fallback for model output;
- no implicit confirmation, auto-confirmation, or confirmation inferred from
  silence/model output;
- no skip, xfail, deselection, deletion, or weakening of existing tests.

## Stable phases

```text
NEW
  -> OUTLINE_READY -> OUTLINE_CONFIRMED
  -> DESIGN_READY  -> DESIGN_CONFIRMED
  -> SLIDES_READY  -> PLAN_CONFIRMED
```

Any non-final stable phase may transition to `CANCELLED`; resume restores the
exact prior stable phase.  A failed/cancelled model call never partially
updates a snapshot.

## Interface decisions

- `HostPlanningStateMachine.start(request)` creates revision 0.
- `HostPlanningStateMachine.apply(snapshot, command)` is the only transition
  Interface.
- `serialize()` and `restore()` round-trip through `PlanningSnapshot` schema
  validation.
- generation commands are valid only after the preceding user confirmation;
  confirmation commands may carry an edited structured artifact.
- the `HostAIClientPlanningAdapter` delegates exclusively to
  `AIClient.generate_structured()` and re-validates the returned model.

## Cross-stage invariants

1. Requested total slides equals both outline allocation and slide-intent count.
2. Section IDs, slide IDs, and slide sequence numbers are unique; sequence is
   contiguous and each section receives its allocated slide count.
3. Every referenced asset ID exists in the curated request.
4. Every required asset is placed exactly once in the final slide plan; no
   asset is duplicated across slides.
5. Confirmation fingerprints match the exact confirmed structured artifact.
6. Restored snapshots re-run phase, fingerprint, asset, and count validation.
7. Only `actor="user"` can satisfy a confirmation gate.
8. Cancellation is checked before and after each synchronous model call; an
   in-flight non-streaming HTTP request remains the existing `AIClient`'s
   responsibility and is not represented as preemptively interrupted.

## Acceptance gates

1. All model-facing and persisted contracts are strict frozen Pydantic models.
2. Legal lifecycle and every illegal transition are deterministic.
3. Model/schema failure raises and preserves the caller's prior snapshot.
4. User edits are revalidated before confirmation and fingerprinted.
5. Cancel/resume and JSON serialize/restore preserve revision and phase.
6. Host Adapter tests prove the Pydantic schema is passed to
   `AIClient.generate_structured()` without exposing credentials.
7. Focused lifecycle tests have zero failures/skips/deselections; changed-file
   Pyright has zero errors/warnings; compileall and forbidden-capability/scope
   audits pass.
