# DataProcessor Pro domain language

This file defines the terms used for the PPT Master integration.  New code,
tests, issues, and review notes should use these names consistently.

## Host Agent Orchestrator

The Host-owned, stateful Module that coordinates report planning, user
confirmation, slide authoring, controlled tool execution, validation, and
artifact publication.  It owns model access and conversation state.  A
third-party package never receives API keys or a model client.

## Planning Snapshot

An immutable, Pydantic-validated record of one Host planning run.  It contains
the curated request, stable phase, structured outline, design contract, slide
intents, user confirmations, revision, and model identity.  Serialization and
restore cross the same validated Interface; editable Markdown is never the
source of truth.

## Planning State Machine

The Host Agent Orchestrator Module that applies explicit planning commands to a
Planning Snapshot.  It owns legal transitions, structured model calls, asset
placement invariants, confirmation fingerprints, cooperative cancellation, and
resume.  Only a user confirmation command advances a confirmation gate.

## Source Bundle

A versioned, hash-pinned upstream archive that has passed read-only package
qualification.  A Source Bundle is not an installed Skill and is never
registered in the existing Skill Runtime.

## Managed Source Bundle Store

The Host-owned, immutable toolchain store under application data.  It accepts
only a qualified Source Bundle, selects the reviewed runtime subset, verifies a
pinned content-tree digest, writes an installation attestation, and commits the
result atomically.  It has no Skill Registry entry and grants no execution
authority.

## Controlled Tool Runner

The only execution Seam for allowlisted PPT Master commands.  It accepts only
a confirmed Planning Snapshot and typed Host command arguments; it owns exact
argument compilation, verified-toolchain and Python-runtime attestation,
workspace confinement, process limits, nested-process denial, cancellation,
audit logs, and output attestation.  Qualification or installation of a Source
Bundle does not authorize execution.

## Controlled Run

One immutable, uniquely identified invocation of the Controlled Tool Runner.
Its Host-written audit record binds the confirmed Planning Snapshot digest,
typed command, verified toolchain and runtime identities, termination state,
capped log digests, and every produced artifact's workspace-relative path,
size, and SHA-256 digest.  Third-party code cannot write the audit directory.

## Report Provider

An Adapter implementing `ReportRenderProvider`.  The existing report
Orchestrator continues to attest Provider identity and output destination;
Host OOXML validation and the outer no-clobber commit remain outside every
Provider.

## PPT Master Report Provider

The Host-owned Report Provider Implementation that binds an attested report
request to a confirmed Planning Snapshot, stages approved project inputs,
enforces P01 and final quality gates through Controlled Runs, verifies the
export receipt, and writes only the outer report transaction's temporary PPTX.
It does not publish a final report or fall back to another Provider.

## Host Authoring Adapter

The replaceable Seam that creates PPT Master project contracts and one complete
SVG page at a time.  It receives immutable, path-free authoring context and no
model credentials; the PPT Master Report Provider alone writes its returned
content and approved assets into the controlled project workspace.

## Host AI Authoring Adapter

The concrete Host Authoring Adapter Implementation that uses the Host
`AIClient` structured-generation Interface to author one complete SVG at a
time.  It receives no credentials or source paths, creates deterministic PPT
Master project contracts, and returns in-memory content for Provider
revalidation and staging.

## Deck Contract Compiler

The Host-owned deep Module that compiles one confirmed Planning Snapshot into
the synchronized design specification, execution lock, model authoring
constraints, and deterministic SVG preflight rules.  Named typography roles
and every other recurring design token have one source of truth at this
Interface; the generated artifacts never evolve independently.

## Quality Receipt

A Pydantic-validated, path-free interpretation of one PPT Master quality
Controlled Run.  It preserves page identity, rule identity, severity, and
evidence so the PPT Master Report Provider can distinguish repairable owning-
source failures from infrastructure failures without parsing text in callers.

## Method Receipt

The accepted first-page authoring method distilled from its Quality Receipt.
It carries reusable rule outcomes into every remaining page request so the P01
gate changes the subsequent authoring method instead of acting as a passive
checkpoint.

## Slide Checkpoint Store

The Host-owned, content-addressed store for verified authored SVG pages and
speaker notes.  Its key binds the relevant Planning Snapshot design context,
Deck Contract, authoring model, current slide intent, current source content,
current assets, Method Receipt, and verified toolchain.  A new Controlled Run
may reuse an exact page checkpoint while still restaging inputs and repeating
every PPT Master quality and export gate; changes to another page do not
invalidate an otherwise identical page.

## PPT Master Planning Workflow

The report-workbench-facing Host Module that binds a Planning State Machine to
one report-input fingerprint.  It exposes separate outline, design, and
slide-plan confirmation actions, produces a path-free preview, invalidates on
input drift, and releases a deterministic render payload only from a
`PLAN_CONFIRMED` Planning Snapshot.

## Template Style Workspace

A digest-addressed, Host-owned workspace built from an already prepared 16:9
PPTX.  It attests a private template copy and a bounded style/profile contract
for planning and SVG authoring.  Its reuse scope is `style`: it does not imply
object mirroring, arbitrary layout reuse, or execution authority.  Admission
failure is explicit and never triggers silent Provider fallback.

## Qualification

A read-only decision that verifies ZIP safety, the pinned archive digest,
upstream identity, version, license, source URL, and required workflow/tool
files.  Qualification performs no extraction, installation, import, or code
execution.
