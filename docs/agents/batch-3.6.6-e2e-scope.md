# Batch 3.6.6 — E2E Scope Freeze / Real Acceptance Contract

**Status:** `SCOPE FROZEN — P0 RECTIFIED — PENDING HUMAN RE-AUDIT`
**Implementation has NOT started.**
**Rectification round:** 1 (2026-08-07) — 4 P0 + 2 P1 fixes applied

---

## 1. Batch 3.6.6 Objective

Execute the final E2E gate for the Host Agent Orchestrator / PPT Master integration against
real diagnosis records, a real LLM backend, the installed PPT Master 2.7.0 toolchain, and
real visual inspection.  Prove that the complete Host main link produces valid, safe,
content-traceable PPTX reports with and without a template, then collect the evidence
package.

This is **real acceptance**, not a second round of Host Orchestrator architecture redesign.

---

## 2. Authoritative Sources

| Priority | Source | Role |
|----------|--------|------|
| 1 | `CLAUDE.md` | Implementation, testing, safety, audit rules |
| 2 | `AGENTS.md` | Pointer; defers to CLAUDE.md |
| 3 | `CONTEXT.md` | Domain vocabulary |
| 4 | `batch-3.6.0-host-orchestrator-scope.md` | Frozen Host architecture baseline |
| 5 | `batch-3.6.5-host-ui-template-scope.md` | UI/template integration scope |
| 6 | `batch-3.6.5-audit-package.md` | Proven baseline (PASS, limited scope) |

---

## 3. Current Proven Baseline

Batch 3.6.5 audit verdict: **PASS.**

Delivered and confirmed:

1. PPT Master appears as a distinct Provider only for PPT output when the reviewed
   managed installation is plausibly present.
2. Three explicit confirmation actions: confirm outline, confirm design, confirm plan.
3. SHA-256 input fingerprint detects drift and invalidates stale confirmations.
4. Path-free preview rendered in the workbench right panel.
5. 16:9 prepared-template admission to an atomic, digest-addressed Template Style Workspace.
6. `HostAIPptMasterAuthoringAdapter` uses `AIClient` structured generation for one complete
   SVG per model call.
7. No automatic Provider fallback; all failure paths are hard failures.
8. Focused test: 4 passed, 0 failed, 0 skipped, 0 xfailed, 0 deselected in 0.53 s.

**Not yet proven (deferred to this batch):**
- Real model-backed no-template and template PPTX generation
- Rendered slide visual QA and template-style fidelity
- Required-figure coverage and closure
- Cancellation and failure recovery with real toolchain
- Full regression run (one non-duplicated pass)

---

## 4. Unproven Claims

The following claims are architectural assumptions that must be verified or falsified
by real E2E evidence:

| # | Claim | How to verify |
|---|-------|---------------|
| U1 | `deepseek-v4-pro` can produce valid structured planning output end-to-end | Real Scenario A/B outline→design→slides |
| U2 | `HostAIPptMasterAuthoringAdapter` produces PPT Master 2.7.0-compliant SVG at scale | Real authoring of all slides |
| U3 | PPT Master 2.7.0 Controlled Runner completes all five stages without regression | Real toolchain execution |
| U4 | Template style workspace meaningfully influences visual output | Scenario B vs A visual comparison |
| U5 | Required-figure contract can be closed from real diagnostic manifest | Figure ledger audit |
| U6 | Content traceability is achievable without a new knowledge system | Evidence mapping pass |
| U7 | `qwen3.5-9b` local model satisfies structured protocol minimum | Scenario C smoke test |
| U8 | Failure/cancel paths preserve existing files | Deterministic Scenario D tests |

Claims marked **falsified** by evidence become P0 if they block the core acceptance
objectives.

---

## 5. Scope

### 5.1 In Scope

- Real DeepSeek V4 Pro E2E: Scenario A (no template) + Scenario B (template)
- Qwen 3.5-9b minimum protocol smoke (Scenario C)
- Deterministic failure/cancel scenarios (Scenario D)
- Required-figure closure audit (figure ledger)
- Content/evidence traceability pass
- PPTX OOXML validation, security gates, relationship/media integrity
- Atomic publish / no-overwrite contract verification
- Per-slide visual QA for both Scenario A and B
- Template style fidelity assessment (Scenario B)
- No-template professional minimum assessment (Scenario A)
- Focused regression on changed/affected modules
- One full regression per CLAUDE.md command
- Pyright zero-error/zero-warning on changed files
- compileall pass
- git diff --check pass
- Final Batch 3.6.6 audit package

### 5.2 Out of Scope (Non-goals)

- Word report expansion or Word Builder modification
- Bridge-only asset evidence relaxation
- Legacy diagnostic schema auto-compatibility
- Plugin marketplace or new plugin installation
- PPT Master auto-upgrade, re-download, or dependency upgrade
- Arbitrary PPTX template object-level mirroring
- UI redesign unrelated to E2E evidence
- Large-scale Host architecture refactoring
- Unrelated code cleanup or performance optimization
- New feature development

---

## 6. Non-goals (Explicit Exclusion List)

1. **Word report** — No Word document generation, validation, or Builder changes.
2. **Bridge relaxation** — Bridge asset evidence requirements are not relaxed.
3. **Template object mirror** — Template = style scope only. No object-level PPTX conversion.
4. **Silent fallback** — Provider failure never triggers automatic built-in PPT replacement.
5. **Dependency upgrade** — No Node/Python/package upgrades. PPT Master 2.7.0 is pinned.
6. **Production implementation in this turn** — This turn is scope freeze only.

---

## 7. Frozen Architecture Boundaries

Re-stated from Batch 3.6.0 and 3.6.5.  These are **not** re-negotiable in 3.6.6:

1. Host Provider failure → no silent fallback. Fallback is user-explicit only.
2. Planning input change → old confirmation invalidated.
3. UI thread: no long model calls, toolchain prep, or PPT build.
4. Template workspace = **style scope**, not object mirror.
5. Provider option probe = low-cost hint only.
6. Controlled Runner = runtime authoritative verifier.
7. Final file: temporary generation → validation → publish.
8. Failure/cancel: never overwrite existing deliverables.
9. E2E problems must not bypass frozen safety boundaries.

### 7.1 Architecture Diagram (Current Implementation)

```
User selects Host Provider
  → Workbench collects: requirements, diagnosis, charts, optional template
  → PlanningRequest + SHA-256 input fingerprint
  → [confirm_outline] → [confirm_design] → [confirm_plan]
  → PLAN_CONFIRMED snapshot
  → HostAIPptMasterAuthoringAdapter (one SVG per model call)
  → PptMasterReportRenderProvider
      → ControlledToolRunner:
          1. project_init
          2. quality_first_page
          3. quality_final
          4. finalize_svg
          5. export_pptx
  → Temporary PPTX
  → Host OOXML validation
  → Atomic publish (no-overwrite)
  → Structured final report
```

---

## 8. Real Acceptance Assets

All assets verified present on disk at scope-freeze time:

### 8.1 Requirements
- **File:** `项目资料库/三组标定/数据/需求01.txt`
- **Status:** EXISTS

### 8.2 Project Description
- **File:** `项目资料库/三组标定/方案/12个应变计光纤实验方案.txt`
- **Status:** EXISTS
- **Note:** If a same-topic Word file exists, do not duplicate-read; text version is primary.

### 8.3 Diagnosis Record
- **File:** `项目资料库/三组标定/数据/诊断记录/诊断记录_20260720_172143.json`
- **Schema:** `1.2`
- **Record ID:** `20260720_172143`
- **Timestamp:** `2026-07-20 17:21:42`
- **Backend:** `online`
- **Model:** `deepseek-v4-pro`
- **Chart manifest entries:** 34
- **Charts produced:** 30
- **Selected sources:** 数据文件, 数据清洗, 数据分析, 多源对比, 传感器标定

### 8.4 Chart Directory
- **Path:** `项目资料库/三组标定/数据/诊断记录/20260720_172143/charts`
- **File count at freeze:** 30 PNG files

**Critical rule:** Chart directory file count ≠ figure-inclusion count.
Figure closure must be determined from:
- `chart_manifest` entries with `report_include: true`
- `ReportRenderRequest.required_figure_ids`
- Authoring slide asset assignments
- Final PPTX media/relationships
- Per-slide visual verification

### 8.5 Template (Scenario B)
- **File:** `项目资料库/三组标定/模板/蓝色简约商务汇报PPT模板.pptx`
- **Status:** EXISTS
- **Meaning:** Style scope only — color palette, font hierarchy, margins, whitespace,
  decorative language, layout rhythm.  NOT object-level mirroring.

### 8.6 PPT Master Toolchain
- **Version:** 2.7.0
- **SHA-256:** `ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af`
- **Install path:** `C:\Users\Administrator\AppData\Local\DataProcessorPro\toolchains\ppt-master\installed\2.7.0\ac2599b467fff4166ea2c34b62d877b12683391feb95de7a8ffcc7892effd7af`
- **Rule:** Read-only integrity/capability probe before use. No re-download, no upgrade,
  no re-install without explicit user authorization.

---

## 9. Model Strategy

### 9.1 Model Connectivity Gate (pre-E2E)

Before any Scenario A/B/C execution, verify live connectivity:

| Model | Provider | Verification |
|-------|----------|--------------|
| `deepseek-v4-pro` | DeepSeek API | Minimal structured generation roundtrip |
| `qwen3.5-9b` | Local llama.cpp | Minimal structured generation roundtrip |

**Security rule:** Never read, print, log, or record API keys from `ai_models_config.json`
or any other source.  On failure, log only: provider, model, stage, error category,
sanitized message.

### 9.2 Scenario Model Assignments

| Scenario | Model | Purpose |
|----------|-------|---------|
| A | `deepseek-v4-pro` | Full no-template E2E |
| B | `deepseek-v4-pro` | Full template E2E |
| C | `qwen3.5-9b` | Minimum protocol smoke |
| D | Deterministic fixtures | Failure/cancel paths |

### 9.3 Model Call Efficiency

- One model call per planning phase (outline, design, slides) — not one monster prompt.
- One model call per slide for SVG authoring — not one call for all slides.
- Page-level retry only on failure; never re-generate entire report for one bad page.

---

## 10. Scenario A — DeepSeek / No Template

### 10.1 Inputs
- Real `deepseek-v4-pro`
- `需求01.txt`
- `12个应变计光纤实验方案.txt`
- `诊断记录_20260720_172143.json`
- The 30 produced PNGs in `20260720_172143/charts` form the available **candidate asset
  pool** (30 produced files ≠ 30 required report figures).  Actual inclusion is
  determined by `required_figure_ids`, supplementary IDs, manifest, and authoring
  semantic assignment — see §18.
- No template PPTX

### 10.2 Success Criteria (all must pass)
1. Planning completes through all three confirmation phases to `PLAN_CONFIRMED`.
2. Authoring produces one valid SVG per planned slide.
3. Controlled Runner completes all five stages: `project_init` → `quality_first_page` →
   `quality_final` → `finalize_svg` → `export_pptx`.
4. `ControlledToolResult.status == SUCCEEDED` for all stages.
5. Final PPTX is valid OOXML, opens in PowerPoint/LibreOffice, 16:9, editable.
6. No external image links, scripts, macros, or path leaks.
7. Required figures are embedded and visually present.
8. Content is traceable to diagnosis record evidence.
9. Per-slide visual QA passes (see §22).
10. No-template visual standard met (see §24).

### 10.3 Failure Conditions (any = Scenario A FAIL)
- Any Controlled Runner stage returns non-SUCCEEDED status.
- Final PPTX fails OOXML validity.
- Any P0 security issue found.
- Any Required figure fails a closure validation check defined in §18.4.
- Model hallucinates key facts not in diagnosis evidence.
- Silent fallback to built-in PPT occurs.

---

## 11. Scenario B — DeepSeek / Template

### 11.1 Inputs
- Identical to Scenario A, plus:
- `蓝色简约商务汇报PPT模板.pptx` as template style workspace

### 11.2 Success Criteria (all must pass)
1. All Scenario A criteria.
2. Template style workspace is successfully admitted (16:9, digest-attested).
3. Final PPTX visibly inherits template: color palette, font hierarchy, margins,
   whitespace, decorative language, layout rhythm.
4. Template example text/slides are NOT leaked into the report.
5. No spurious object-level copying from template.
6. No unexplained aspect ratio or master slide conflicts.
7. Template visual acceptance standard met (see §23).

### 11.3 Template Admission Failure
If the prepared template fails admission (not 16:9, unprepared, etc.):
- Error must be explicit and corrective.
- Must NOT silently fall back to no-template mode.
- Record as Scenario B template-admission sub-result.

---

## 12. Scenario C — Qwen Minimum Smoke

### 12.1 Inputs
- `qwen3.5-9b` via local llama.cpp
- Minimal structured generation prompt (not full report)

### 12.2 Success Criteria
1. Model connection verified.
2. Structured output protocol produces parseable JSON.
3. Response satisfies the minimum schema required for Host authoring.
4. No crash, hang, or silent empty response.

### 12.3 Scope Limit
- Do NOT run full Scenario A/B with Qwen unless DeepSeek is unavailable or user
  explicitly requests it.
- Qwen smoke is a protocol gate, not a full report generator.

---

## 13. Deterministic Failure Scenarios (Scenario D)

Use deterministic fixtures, stubs, or reusable intermediate artifacts.  Do not re-run
real model E2E for each failure case.

### 13.1 Planned Cases

| D# | Scenario | Verification |
|----|----------|--------------|
| D1 | AI returns illegal/unsafe SVG (script tag, external href) | SVG rejected; stage fails with clear error |
| D2 | Controlled Runner stage fails (e.g., quality_final) | Error surfaced; no partial PPTX published |
| D3 | User cancels mid-authoring | Worker stops; no output file produced |
| D4 | Target PPTX file already exists | No overwrite; existing file preserved |
| D5 | Required figure file missing from disk | Explicit error; not silently dropped |
| D6 | Input changes after planning confirmation | Old confirmation invalidated; authoring blocked |
| D7 | Host Provider (PPT Master) fails | Error surfaced; NO automatic fallback to built-in PPT |

### 13.2 Implementation Approach
- D1, D2, D5: Use crafted invalid inputs to existing validation paths.
- D3: Use cancel_check callback with controlled timing.
- D4: Pre-create target file before render.
- D6: Modify input fingerprint after confirmation.
- D7: Simulate Provider construction failure or toolchain unavailability.

---

## 14. Planning / Confirmation Acceptance

### 14.1 Three-Phase Confirmation Flow (from code)

```
NEW → OUTLINE_READY → [confirm_outline] → OUTLINE_CONFIRMED
→ DESIGN_READY → [confirm_design] → DESIGN_CONFIRMED
→ SLIDES_READY → [confirm_plan] → PLAN_CONFIRMED
```

Actual `PlanningPhase` enum values (from `dp_engine/ppt_master_host/planning.py:31-38`):
`NEW`, `OUTLINE_READY`, `OUTLINE_CONFIRMED`, `DESIGN_READY`, `DESIGN_CONFIRMED`,
`SLIDES_READY`, `PLAN_CONFIRMED`, `CANCELLED`

### 14.2 Acceptance Criteria
- All three confirmations require explicit user action (button click).
- Each phase produces structured, parseable model output matching its Pydantic schema.
- Preview is path-free and reflects current phase state.
- `PptMasterWorkflowView.valid_for_current_inputs == True` only when fingerprint matches.
- Input fingerprint change → `valid_for_current_inputs == False` → authoring blocked.
- `PLAN_CONFIRMED` is the only phase accepted by `ControlledToolRunner.run()`.
- `PptMasterWorkflowView.ready_for_authoring == True` only at `PLAN_CONFIRMED` with
  valid fingerprint.

---

## 15. Host Authoring Acceptance

### 15.1 Structured Output Requirements
- Every AI planning call returns output parseable by its Pydantic schema
  (`OutlinePlan`, `DesignContract`, `SlideIntentPlan`).
- Every AI authoring call returns a `PptMasterAuthoredSlide` with valid SVG content.
- Field completeness: no required fields null or empty.
- Page/section relationships are internally consistent.
- No implicit fallback or empty success.

### 15.2 Authoring Adapter Contract (from code)

`HostAIPptMasterAuthoringAdapter` (`dp_engine/ppt_master_host/authoring.py`):
- Uses `AIClient.generate_structured()` with Pydantic schema validation.
- Produces one complete SVG per `PptMasterSlideAuthoringRequest`.
- Returns `PptMasterAuthoredSlide` with in-memory SVG content.
- Never receives credentials, source paths, or final output paths.
- The Provider revalidates SVG before staging.

### 15.3 Page-Level Failure Recovery

**Retry policy:**
- One slide authoring failure → retry that slide only, subject to a bounded retry
  budget.  No unbounded retry.  If an existing runtime/configuration already defines a
  concrete retry limit, use that value; otherwise the implementation must choose the
  minimum bounded policy and record the actual limit in the audit evidence.
- Never re-run the entire planning → authoring pipeline for one bad slide.

**Scenario failure rule (P0):**
- Any planned slide that, after the bounded page-level retry budget is exhausted,
  still cannot produce a legal, acceptable SVG → the current Scenario is immediately
  marked **FAIL**.  The final PPTX must NOT be published as a successful deliverable.
- For diagnostic efficiency, processing MAY continue through remaining slides after a
  persistent failure so that more failure evidence can be collected in a single run.
  **Diagnostic continuation does NOT restore Scenario PASS.**  Once any slide has
  irrecoverably failed, the Scenario is already FAIL; subsequent slides are
  diagnostic-only evidence collection.

---

## 16. SVG Validation Contract

Every model-produced SVG must pass these checks before acceptance:

### 16.1 Pre-parse Checks
- Strip/reject Markdown code fences (```svg ... ```).
- Reject empty or whitespace-only content.
- Reject content exceeding `_MAX_SVG_BYTES` (4 MB).

### 16.2 XML & Security Checks
- XML must be well-formed and parseable.
- Root element must be `<svg>` with correct namespace `http://www.w3.org/2000/svg`.
- **Forbidden elements:** `<script>`, `<foreignObject>`, `<use>` with external href.
- **Forbidden attributes:** `onload`, `onclick`, any `on*` event handler.
- **Forbidden references:** `href` to external URLs (`http:`, `https:`, `ftp:`),
  arbitrary local files (`file:`, absolute paths).
- **Allowed references:** inline `data:` URIs, same-document fragment IDs (`#id`).

### 16.3 Geometry Checks
- `viewBox` must be present and parseable (or `width`/`height` with valid units).
- Canvas dimensions must be reasonable (not 0, not negative, not absurdly large).
- PPT Master target: 1280×720 (16:9 at 72 dpi equivalent).

### 16.4 Content Safety
- No path leaks (no filesystem paths in text content).
- No API keys, tokens, or credentials.
- Dangerous URI schemes rejected: `javascript:`, `vbscript:`, `data:text/html`.

### 16.5 Failure Handling
- Illegal SVG → stage fails with specific error code and sanitized message.
- Never write illegal SVG as if it were valid.
- Never produce a "success" result that contains an SVG that failed validation.

---

## 17. Controlled Runner Acceptance

### 17.1 Five-Stage Pipeline (from code)

Actual stage names and ordinals from
`dp_engine/report_provider/ppt_master.py:276-398`:

| Ordinal | Stage Name | Command | Description |
|---------|-----------|---------|-------------|
| 1 | `project_init` | `PROJECT_INIT` | Initialize PPT Master project |
| 2 | `quality_first_page` | `SVG_QUALITY_CHECK` | Quality-check first page SVG |
| 3 | `quality_final` | `SVG_QUALITY_CHECK` | Quality-check all SVGs |
| 4 | `finalize_svg` | `FINALIZE_SVG` | Finalize SVGs for export |
| 5 | `export_pptx` | `SVG_TO_PPTX` | Export to PPTX |

### 17.2 Per-Stage Verification
- Each stage returns `ControlledToolResult` with explicit `status`:
  `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `RESOURCE_REJECTED`.
- `ControlledRunStatus` enum (from `controlled_runner.py:79-84`).
- Stage `SUCCEEDED` → next stage proceeds.
- Stage non-`SUCCEEDED` → pipeline stops; error surfaced.
- `planning_snapshot_sha256` recorded in each result.
- `toolchain` attestation present in each result.
- `runtime_sha256` recorded.

### 17.3 Progress & UI
- Progress reported per stage (not per internal tool step).
- UI shows current stage name and ordinal.
- Cancel check honored between stages and before tool launch.

### 17.4 Workspace & Temp Files
- Workspace confined to `_workspace_parent / workspace_id`.
- Temp files under `workspace / temp / run_id`.
- Audit directory: `workspace / audit / run_id`.
- No temp files in project source directories or user directories.

---

## 18. Required Figure Closure Contract (P0)

### 18.1 Figure Ledger Schema

Every figure in scope must be tracked with:

| Field | Source | Description |
|-------|--------|-------------|
| `figure_id` | `chart_manifest[].chart_id` | Unique identifier |
| `in_required_ids` | `figure_id ∈ required_figure_ids` | **Sole Required authority** |
| `in_supplementary_ids` | `figure_id ∈ supplementary_figure_ids` | Marked supplementary |
| `manifest_exists` | `chart_manifest[]` lookup | Manifest entry found? |
| `manifest_produced` | `chart_manifest[].produced` | PNG was generated? |
| `manifest_include` | `chart_manifest[].report_include` | Manifest says "include"? |
| `png_path` | `chart_manifest[].rel_path` | Relative path in charts dir |
| `png_exists` | Filesystem check | Actual file presence |
| `png_usable` | Read + validate | File readable, valid PNG |
| `assigned_slide` | `SlideIntent.asset_ids` | Which slide uses it |
| `embedded_rId` | PPTX `ppt/slides/media` + rels | Actual embedding |
| `visual_verified` | Per-slide QA | Visible on target slide |
| `exclusion_reason` | Manual/automated | Why excluded (only for non-Required) |
| `status` | Computed | `ok`, `closure_fail`, `not_required`, `optional_used`, `optional_unused` |

### 18.2 Figure Status Classification

**Required — sole authority:**
`figure_id ∈ ReportRenderRequest.required_figure_ids`

No additional condition (`report_include`, `produced`, PNG existence) can remove a
figure from Required status.  Those conditions belong to closure validation, not
identity.

**Other classifications (non-Required figures only):**
- **Optional** — `report_include: true` AND `produced: true` but NOT in `required_figure_ids`.
- **Excluded** — NOT in `required_figure_ids` AND (`report_include: false` OR
  `produced: false` with valid `skip_reason`).
- **Duplicate** — Same `chart_id` assigned to multiple slides (warn; P1 if unplanned).
- **Missing source** — Required figure whose PNG file is not on disk.
- **Unusable source** — Required figure whose PNG exists but is corrupt/unreadable.

### 18.3 Closure Questions (must answer in audit)

1. Which figures are in `required_figure_ids`?
2. For each Required figure: does a manifest entry exist?
3. For each Required figure: is the source PNG on disk and usable?
4. For each Required figure: which slide is it assigned to?
5. For each Required figure: is it embedded in PPTX (media + relationship)?
6. For each Required figure: is it visually present on the rendered slide?
7. Are any figures duplicated across slides?
8. Are unrelated figures dumped at the end without semantic connection?
9. Does each figure's slide position match its semantic purpose?
10. Are there any contract conflicts (e.g., Required figure with `report_include: false`)?

### 18.4 Required Figure Closure Validation (P0)

For every figure where `figure_id ∈ ReportRenderRequest.required_figure_ids`:

| Check | Failure → |
|-------|-----------|
| Manifest entry exists for this `chart_id` | P0 — `contract_conflict: required figure has no manifest entry` |
| Manifest `produced` state recorded | Informational only; does not downgrade Required status |
| Manifest `report_include` state recorded | If `false` on a Required figure → P0 `contract_conflict`; does NOT downgrade to Excluded |
| Source PNG exists on disk | P0 — `missing_source: required figure file not found` |
| Source PNG is readable and valid | P0 — `unusable_source: required figure corrupt or unreadable` |
| Figure assigned to at least one planned slide | P0 — `unassigned: required figure has no slide` |
| Figure embedded in PPTX (media file + relationship rId) | P0 — `not_embedded: required figure not in PPTX` |
| Figure visually present on rendered target slide | P0 — `not_visible: required figure not rendered` |
| Figure not silently duplicated across slides | P1 — record in ledger, note semantic intent |
| Figure semantically located near corresponding discussion | P1 — record if misplaced |

**Any P0 check failure → required figure closure FAIL → Scenario FAIL.**

A manifest entry with `report_include: false` for a `required_figure_ids` member is a
**contract conflict** and does NOT downgrade the figure to Excluded.  It must be
recorded as a required-figure closure failure (P0).

`exclusion_reason` is only valid for figures NOT in `required_figure_ids`.
Non-Required figures may be excluded with documented reasons.

---

## 19. Content / Evidence Traceability

### 19.1 Traceability Requirements

Every key claim in the generated report must be traceable to source evidence:

| Claim Type | Must Trace To |
|------------|---------------|
| Channel/sensor count and names | Diagnosis `data_source_snapshot` |
| Calibration ratings (优/良/FAIL) | Diagnosis `data_source_snapshot[传感器标定]` |
| Hysteresis values | Diagnosis `data_source_snapshot[传感器标定]` |
| Cross-sensor correlation | Diagnosis `data_source_snapshot[多源对比]` |
| KB findings (ms_real_disagreement, etc.) | Diagnosis `kb_hits` |
| Action recommendations | Diagnosis `kb_hits[].recommendation` |
| Statistical summaries | Diagnosis `data_source_snapshot[数据分析]` |

### 19.2 Hallucination Detection

The following are **P0** if found in generated content without source evidence:
- Fictitious channels or sensors
- Made-up coefficients, thresholds, or values
- Invented experimental results or conclusions
- Fabricated fault causes not in KB hits
- Non-existent figures or data references

### 19.3 Evidence Format

For the audit package, produce a traceability map:
```
Claim: "A2 迟滞超标 (5.0%FS)"
  → Source: diagnosis.data_source_snapshot[传感器标定]
  → Evidence: "A2: ... 迟滞=5.030%FS ... 评级=FAIL(迟滞超标)"
  → Slide: 7 (异常分析)
  → Verdict: TRACEABLE
```

---

## 20. PPTX OOXML / Security Gates

### 20.1 File Validity
- [ ] PPTX opens in PowerPoint and/or LibreOffice without repair prompt.
- [ ] 16:9 aspect ratio confirmed.
- [ ] Editable (not protected/read-only).
- [ ] ZIP/OOXML structure valid (`[Content_Types].xml`, `_rels/.rels`, `ppt/presentation.xml`).
- [ ] Package relationships valid.
- [ ] Slide relationships valid (no dangling rIds).
- [ ] Media relationships reference existing files.

### 20.2 Security Gates
- [ ] No external image links (all images embedded).
- [ ] No scripts (VBA, JavaScript, Python).
- [ ] No macros.
- [ ] Zero `TargetMode="External"` relationships.  Specifically forbidden: external
    image links, external media, local file relationships, OLE/external package
    references, filesystem paths in relationships, and remote resource references.
    All resources must be embedded within the PPTX package.
- [ ] No local filesystem paths in relationships.
- [ ] No temporary path leakage in visible content or XML attributes.
- [ ] No API keys, tokens, or credentials in any content.
- [ ] No workspace/private absolute paths in slide content or notes.

### 20.3 Validation Method
1. Unzip PPTX → verify directory structure.
2. Parse `[Content_Types].xml` → verify all parts declared.
3. Parse `_rels/.rels` → verify package relationships.
4. Parse each `ppt/slides/_rels/slideN.xml.rels` → verify zero `TargetMode="External"`
   relationships; all targets must be internal to the package.
5. Parse each `ppt/slides/slideN.xml` → search for dangerous content patterns.
6. Verify `ppt/media/` file count matches relationship references.

---

## 21. Atomic Publish / No-Overwrite Contract

### 21.1 Publication Flow (from code)
```
1. Generate → temp file (Host transaction temp path)
2. Validate → OOXML + security checks on temp file
3. Publish → atomic copy to final output path
4. Cleanup → remove temp file
```

### 21.2 No-Overwrite Verification
- If final target path already exists → verify:
  - File is NOT overwritten.
  - Error or warning emitted.
  - Original file content (SHA-256) unchanged after render attempt.
- If render fails mid-way → no file at target path.
- If render cancelled → no file at target path.
- No `.tmp`, `.partial`, or intermediate file left at final target location.

### 21.3 Temp File Hygiene
- Temp files only under designated temp directory.
- Temp files cleaned up on success and failure.
- No temp file that could be mistaken for a final deliverable.

---

## 22. Visual QA Contract

### 22.1 Rendering Method (Mandatory Gate)

Per-slide visual QA for Scenario A and Scenario B **MUST** use the
**Presentations / presentation skill** required by this project.

Before starting visual acceptance, read the skill's `SKILL.md` and follow its
rendering and visual inspection workflow.

If the Presentations skill is unavailable for any reason, the visual QA gate
conclusion is:

**`BLOCKED`** or **`WAITING FOR ACCEPTANCE`**

LibreOffice headless rendering is permitted as a supplemental compatibility check
or as an internal step within the Presentations skill's own workflow, but it
**must NOT** be used as a substitute acceptance gate when the Presentations skill
is unavailable.  Visual QA cannot be marked PASS through a LibreOffice-only path.

### 22.2 Per-Slide Checklist

For every slide, record:

| # | Check | Standard |
|---|-------|----------|
| 1 | Cropping | No content clipped at slide edges |
| 2 | Overflow | No text/shapes extending beyond slide |
| 3 | Overlap | No unintended element overlap |
| 4 | Font size | No text below ~8pt (readable at presentation scale) |
| 5 | Text density | Not a wall of text; reasonable whitespace |
| 6 | Image stretching | Images maintain aspect ratio |
| 7 | Image-text ratio | Balanced; not all-text or all-image |
| 8 | Title hierarchy | Clear title → subtitle → body progression |
| 9 | Body hierarchy | Bullet levels distinguishable |
| 10 | Captions | Figures have captions or contextual labels |
| 11 | Page uniqueness | Not a duplicate of another slide |
| 12 | Visual rhythm | Reasonable variation across slides |
| 13 | Placeholder artifacts | No empty "Click to add" or template residual |
| 14 | Information wall | No slide with >~60% text coverage |
| 15 | Table density | Tables readable, not microscopic |
| 16 | Chart position | Charts near related text, not orphaned |
| 17 | Professional appearance | Suitable for formal technical presentation |

### 22.3 Professional Minimum (No-Template)

Scenario A must NOT exhibit:
- python-pptx plain white default background on every slide.
- Sequential identical text boxes with no design variation.
- Every slide using the same blank layout.
- Title + bullet only; no visual hierarchy.
- All figures clustered at the end.
- Missing cover, TOC, or conclusion structure.

### 22.4 QA Record Format

Per slide:
```
Slide N: [purpose]
  Evidence/Source: [traceability ref]
  Required Figure IDs: [list or "none"]
  Visual Issues:
    - [description] | Severity: P0/P1/P2 | Fix: [action] | Retest: [result]
  Verdict: PASS / FAIL
```

---

## 23. Template Visual Acceptance (Scenario B)

### 23.1 Style Inheritance Required
- **Color palette:** Slide backgrounds, accents, text colors match template theme.
- **Font hierarchy:** Title/body fonts, sizes, weights consistent with template.
- **Margins:** Content stays within template margin boundaries.
- **Whitespace:** Spacing rhythm matches template conventions.
- **Decorative language:** Lines, shapes, background elements consistent.
- **Layout system:** Slide layouts reflect template's visual system.

### 23.2 Anti-Patterns (must NOT occur)
- Template example slides preserved verbatim ("单击此处添加标题").
- Template-specific content (company name, logo placeholder text) leaked into report.
- Multiple conflicting master slides applied.
- 4:3 template content forced into 16:9 or vice versa.
- Template objects mechanically copied without semantic meaning.

### 23.3 Assessment Method
- Side-by-side comparison: template sample slide vs generated report slide.
- Color sampling: extract dominant colors from both and compare palette.
- Font audit: list fonts used in report vs fonts in template theme.
- Margin measurement: compare content bounding boxes.

---

## 24. No-Template Visual Acceptance (Scenario A)

### 24.1 Required Slide Categories

At minimum, the report should include slides serving these purposes
(exact count determined by content, not prescribed):

- **Cover:** Report title, date, organization context
- **TOC/ framework:** Report structure overview
- **Background:** Project/experiment context
- **Method/ setup:** Measurement configuration, sensor layout
- **Key evidence:** Data quality, calibration results
- **Anomaly/ diagnosis:** KB findings, failure analysis
- **Recommendations:** Actionable next steps
- **Conclusion:** Summary and final assessment

### 24.2 Professional Minimum
- Consistent visual system even without external template.
- Readable typography with clear hierarchy.
- Appropriate use of color for emphasis/categorization.
- Figures placed near their discussion context.
- No "wall of bullet points" slides.

---

## 25. Page-Level QA Record Format

Standard record template for every slide in both Scenario A and B:

```markdown
### Slide N: [Slide Purpose / Title]

- **Evidence / Source:** [traceability reference or "N/A"]
- **Required Figure IDs:** [list]
- **Visual Issues:**

| # | Description | Severity | Fix Applied | Retest |
|---|-------------|----------|-------------|--------|
| 1 | ... | P0/P1/P2 | ... | PASS/FAIL |

- **Verdict:** PASS / FAIL
```

Severity for visual issues:
- **P0:** Content invisible, unreadable, or dangerously misleading. Required figure
  missing from intended slide.
- **P1:** Readability impaired, poor layout, aesthetic issues affecting comprehension.
- **P2:** Minor polish, typographic tuning, spacing refinements.

---

## 26. Failure Localization / Retry Strategy

### 26.1 Diagnosis Protocol

On any E2E failure:
1. **Reproduce** — confirm the failure is not transient.
2. **Locate stage** — identify exact pipeline stage (planning phase, authoring slide N,
   Controlled Runner stage M).
3. **Minimize** — reduce to smallest reproducible surface.
4. **Hypothesize** — propose root cause.
5. **Instrument** — add targeted logging if needed.
6. **Fix** — minimal code change addressing only the root cause.
7. **Regression test** — re-run only the affected stage/slide.

### 26.2 Focused Retest Examples

| Failure | Retest Scope |
|---------|-------------|
| Model JSON parse error | Re-run authoring with same input + fixed parsing |
| Single SVG illegal | Re-author that slide only |
| PPT Master tool stage N fails | Resume from stage N-1 artifact if valid |
| Single slide visual layout bad | Fix layout logic; re-render affected slide |
| Required figure missing | Fix figure path/assignment; re-check figure ledger |

### 26.3 When to Re-Run Full E2E
- After all focused fixes are stable.
- Before final audit evidence collection.
- Exactly once for Scenario A, once for Scenario B.

---

## 27. Focused Test Strategy

### 27.1 Pre-E2E Focused Tests

Before running real model E2E, verify with focused tests:

1. **Planning contracts** — Structured model output schemas validate correctly with
   mock responses.
2. **Authoring contracts** — SVG validation rejects known-bad SVGs; accepts known-good.
3. **Controlled Runner** — Deterministic toolchain tests (existing
   `test_ppt_master_controlled_runner.py`).
4. **Provider integration** — `test_ppt_master_report_provider.py` focused tests.
5. **UI workflow** — `test_ppt_master_host_ui_workflow.py` (already PASS, 4/0/0/0/0).

### 27.2 Post-E2E Focused Tests

After E2E stabilization, run targeted tests on any modified modules.

### 27.3 Test Commands

Per CLAUDE.md:
```bash
python -m pytest tests/ -q --tb=short    # Full regression (once, at end)
```

Focused commands will be constructed based on actually modified files.

---

## 28. Final Regression Strategy

### 28.1 Execution Order (Frozen)

1. Focused tests on modules modified during E2E fixes
2. Scenario A complete real E2E (DeepSeek, no template)
3. Scenario B complete real E2E (DeepSeek, template)
4. Scenario C minimum smoke (Qwen)
5. Scenario D deterministic failure cases
6. Per-slide visual QA for Scenario A
7. Per-slide visual QA for Scenario B
8. Spot re-verification of any visual fixes
9. Focused automated tests on affected modules
10. Full regression: `python -m pytest tests/ -q --tb=short` (per CLAUDE.md)
11. Pyright on all changed files (zero errors, zero warnings)
12. `python -m compileall` on changed files
13. `git diff --check`
14. Final audit evidence aggregation

### 28.2 Anti-Patterns (Forbidden)
- Re-running full E2E after every minor fix.
- Re-running full pytest suite after every code change.
- "Just one more quick fix" loop without re-freezing.
- Adding new acceptance criteria after E2E has started.

---

## 29. Evidence Collection Requirements

### 29.1 Per-Scenario Evidence

| Item | Scenario A | Scenario B | Scenario C | Scenario D |
|------|-----------|-----------|-----------|-----------|
| Planning phase trace (all 3 confirmations) | Required | Required | N/A | N/A |
| Input fingerprint and validity log | Required | Required | N/A | As needed |
| Authoring manifest (slide→SVG→result) | Required | Required | N/A | N/A |
| Controlled Runner 5-stage results | Required | Required | N/A | As needed |
| Final PPTX SHA-256 | Required | Required | N/A | N/A |
| OOXML validation log | Required | Required | N/A | N/A |
| Security gate checklist (all items) | Required | Required | N/A | N/A |
| Figure ledger (all entries) | Required | Required | N/A | N/A |
| Content traceability map | Required | Required | N/A | N/A |
| Per-slide QA records | Required | Required | N/A | N/A |
| Model call log (sanitized) | Required | Required | Required | N/A |
| Failure/cancel test results | N/A | N/A | N/A | Required |
| No-overwrite verification | Required | Required | N/A | Required |

### 29.2 Sanitization Rules
- No API keys in any evidence file.
- No absolute paths containing usernames or sensitive directory names.
- No model response content that might contain prompt injection artifacts.
- Log model identity, stage, error category, sanitized message only.

---

## 30. P0 / P1 / P2 Severity Definition

### 30.1 P0 — Blocking

Only these can block Batch 3.6.6 PASS:

1. Security boundary breach (key leak, path leak, script injection).
2. Data or existing report corruption/overwrite risk.
3. Core Host main link cannot complete (planning → authoring → runner → PPTX).
4. Generated PPTX is not valid OOXML or cannot open.
5. Required figure contract cannot close — any figure in `required_figure_ids` fails a
   closure validation check per §18.4 (missing manifest, missing source, unusable,
   unassigned, not embedded, or not visible).
6. Model fabricates critical experimental/engineering facts without guard.
7. Scenario A or B core acceptance objectives cannot be met.
8. Test/regression results contradict frozen contract.
9. Insufficient evidence to prove PASS.
10. Silent fallback to built-in PPT occurs.

### 30.2 P1 — Important, Non-blocking

- Non-core slide design could be improved.
- Error messages are functional but not polished.
- Performance has optimization space but does not prevent correct execution.
- QA tooling can be enhanced.
- Non-critical boundary coverage gaps.
- Minor visual issues (spacing, font size edge cases) that don't impair comprehension.

### 30.3 P2 — Follow-up

- Code style / naming improvements.
- Documentation wording.
- Additional test coverage beyond core paths.
- Non-essential refactoring.
- Advanced template compatibility beyond style scope.
- UX polish not affecting E2E correctness.

**P1 and P2 do NOT block Batch 3.6.6 PASS.**

---

## 31. Stop Conditions

During planning (this turn) OR implementation (next turn), stop and report if:

| # | Condition | Action |
|---|-----------|--------|
| S1 | PPT Master download/upgrade/install required | BLOCKED — report; await authorization |
| S2 | Template = style scope definition must change | BLOCKED — architecture scope change |
| S3 | Word report inevitably required | BLOCKED — scope change |
| S4 | Bridge asset evidence must be relaxed | BLOCKED — report affected contract |
| S5 | Required-figure contract unrecoverable from code/diagnosis | BLOCKED — report evidence gap |
| S6 | Public data format/schema must change | BLOCKED — report schema dependency |
| S7 | User's current workspace changes must be overwritten | BLOCKED — report conflict |
| S8 | Large-scale file migration required | BLOCKED — report migration scope |
| S9 | 3.6.5 implementation contradicts frozen scope | BLOCKED — report contradiction |
| S10 | Entire Host architecture requires refactoring | BLOCKED — report architecture gap |

---

## 32. Exit Criteria / Definition of PASS

Batch 3.6.6 achieves PASS only when **all** of the following are true:

### 32.1 Core E2E
- [ ] Scenario A (DeepSeek, no template) — complete E2E PASS
- [ ] Scenario B (DeepSeek, template) — complete E2E PASS
- [ ] Scenario C (Qwen smoke) — protocol gate PASS, or documented BLOCKED with evidence
- [ ] Scenario D (failure cases) — all deterministic cases PASS

### 32.2 Planning & Confirmation
- [ ] Three-phase confirmation functional in real workflow
- [ ] Input change correctly invalidates old confirmation
- [ ] `PLAN_CONFIRMED` required by Controlled Runner (enforced)

### 32.3 Figures
- [ ] Required figure ledger complete — every `figure_id ∈ required_figure_ids` tracked
- [ ] All Required figure closure checks passed per §18.4: manifest exists, source
  exists, source usable, slide assigned, PPTX embedded, visually present
- [ ] Zero P0 closure failures
- [ ] Any contract conflicts (e.g., `report_include: false` on a Required figure)
  documented and treated as P0 closure failures

### 32.4 PPTX Quality
- [ ] OOXML structure valid
- [ ] All security gates passed
- [ ] All relationship/media references valid
- [ ] Atomic publish verified (no overwrite on existing file)

### 32.5 Visual
- [ ] Scenario A per-slide visual QA PASS (all P0 = 0)
- [ ] Scenario B per-slide visual QA PASS (all P0 = 0)
- [ ] Template style inheritance evidenced
- [ ] No-template professional minimum met

### 32.6 Content
- [ ] Content traceability map complete
- [ ] Zero fabricated facts detected (P0)

### 32.7 Failure & Safety
- [ ] Cancel does not produce partial output file
- [ ] Failure does not overwrite existing file
- [ ] No silent Provider fallback occurred

### 32.8 Regression
- [ ] Focused automated tests PASS
- [ ] Full regression per CLAUDE.md PASS (precise counts recorded)
- [ ] Pyright: zero errors, zero warnings on changed files
- [ ] compileall: PASS on changed files
- [ ] `git diff --check`: PASS

### 32.9 Audit
- [ ] Audit package contains sufficient evidence to independently verify above claims

### 32.10 P0 Count
- [ ] **P0 = 0**

**P1 and P2 may be non-zero.  Only P0 blocks.**

---

## 33. Expected Future Batch 3.6.6 Audit Package Contents

The final audit package (produced during implementation, not this turn) should include:

1. **Verdict** — PASS / FAIL / BLOCKED with explicit P0/P1/P2 counts
2. **Model connectivity proof** — sanitized connection log
3. **Scenario A evidence package:**
   - Planning phase trace
   - Authoring manifest
   - Controlled Runner 5-stage results
   - OOXML validation log
   - Security gate checklist
   - Figure ledger
   - Content traceability map
   - Per-slide QA records
   - Final PPTX SHA-256
4. **Scenario B evidence package:**
   - All Scenario A items
   - Template style assessment
   - Template anti-pattern check
5. **Scenario C evidence** — protocol smoke log
6. **Scenario D evidence** — per-case results
7. **Focused test results** — exact command and counts
8. **Full regression results** — exact collected/passed/failed/skipped/xfailed/deselected/duration
9. **Pyright output** — zero errors, zero warnings
10. **compileall output** — PASS
11. **git diff --check output** — PASS
12. **Changed files list** — all files modified in this batch
13. **Scope compliance check** — no non-goal violations

---

## 34. Scope Freeze Metadata

- **Frozen:** 2026-08-07
- **Rectified:** 2026-08-07 (Round 1 — 4 P0 + 2 P1 fixes)
- **Branch:** `llama-cpp`
- **HEAD at freeze:** `8a0105527857bbefd34426817ccc0cbbf23102b6`
- **Batch 3.6.5 verdict:** PASS (limited-scope focused fixtures only)
- **Implementation status:** NOT STARTED
- **Next action:** Human re-audit of this rectified scope document

---

**END OF SCOPE FREEZE DOCUMENT**

Batch 3.6.6 implementation has NOT started.  Execution is stopped pending human audit
of this scope document.
