# Batch 3.6.1 — Managed Source Bundle Store

## Outcome

Install the reviewed PPT Master 2.7.0 Source Bundle into a dedicated,
Host-owned toolchain store using selective extraction, content attestation,
same-filesystem staging, cancellation, rollback, and atomic commit.  The store
is separate from the Skill Registry and does not execute installed code.

## Frozen target files

- `CONTEXT.md` — add the Managed Source Bundle Store term only
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/ppt_master_host/bundle_store.py`
- `tests/test_ppt_master_bundle_store.py`
- this scope document
- the final Batch 3.6.1 audit document

## Forbidden changes

- no changes to UI, `main.py`, `AIClient`, Skill Runtime, Skill Registry,
  Report Provider, report Bridge/Builder, requirements, or existing AppData
  skill directories;
- no bundled Python import, command execution, dependency installation,
  network access, preview server, or report generation;
- no deletion, skip, xfail, deselection, or weakening of existing tests;
- no movement or mutation of the user-provided source ZIP.

## Selection policy `ppt-master-host-toolchain-v1`

Include:

- upstream license and Claude plugin identity metadata;
- the complete `skills/ppt-master/` tree, including scripts, workflows,
  templates, icons, schemas, requirements declaration, and reference guidance.

Exclude:

- repository-wide examples and pre-generated reports;
- `skills/ppt-master/references/ai-image-comparison/`, which contains about
  45 MiB of prompt-comparison demonstration images and is not a runtime input.

Reviewed output:

- files: 12,151
- bytes: 18,250,613
- tree SHA-256:
  `a9e3b28cd98571a1d03e348d882dc9ffc8807e90a2cdb130dd4c79faf1167850`

## Acceptance gates

1. Public install Interface re-runs Source Bundle qualification.
2. Every selected entry is streamed into a unique same-filesystem staging
   directory; archive contents are never imported or executed.
3. Selected count/size limits, CRC, per-file SHA-256, and the pinned tree digest
   are checked before commit.
4. A bounded JSON attestation records immutable identity and file inventory.
5. Commit is a single `os.replace` from staging to the final version/hash path.
6. Cancellation or failure removes only the current staging transaction and
   leaves no final installation.
7. Reinstall of an intact bundle is idempotent; a corrupt/conflicting existing
   directory is never overwritten silently.
8. Installation creates no Skill Registry record and exposes no execution
   Interface.
9. Focused tests, changed-file Pyright, compileall, real-package install/verify,
   forbidden-capability search, and Git scope audit pass.
