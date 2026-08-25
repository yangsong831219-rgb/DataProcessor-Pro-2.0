# Batch 3.6.3 - Controlled Tool Runner

## Outcome

Create the only execution Seam for the reviewed PPT Master 2.7.0 toolchain.
The Host compiles typed operations into exact arguments, re-verifies the
Managed Source Bundle and Python runtime, runs a Host-owned worker with a
scrubbed environment and deny-by-default audit hooks, applies Windows Job
limits, confines writes to a dedicated workspace, and attests logs and output.

This batch does not connect the Runner to the report Provider or UI and does
not execute the real installed PPT Master project pipeline.

## Frozen target files

- `CONTEXT.md` - deepen Controlled Tool Runner and add Controlled Run
- `dp_engine/ppt_master_host/__init__.py`
- `dp_engine/ppt_master_host/controlled_runner.py`
- `dp_engine/ppt_master_host/controlled_worker.py`
- `tests/test_ppt_master_controlled_runner.py`
- this scope document
- the final Batch 3.6.3 audit document

## Exact command allowlist

1. `project_manager.py init <safe-name> --format ppt169 --dir <workspace>/project`
2. `svg_quality_checker.py <project> --format ppt169 --stage first-page|final`
3. `finalize_svg.py <project> --quiet`
4. `svg_to_pptx.py <project> -o <workspace>/output/<safe>.pptx`
   `-f ppt169 -q --pptx-structure flat|structured`

No raw argument list crosses the public Interface.  `import-sources`, URLs,
template/server/update/image-search commands, SVG generators, animation,
narration, arbitrary source directories, Shell, and arbitrary executable or
script paths are not exposed.

## Safety invariants

1. Every run requires a revalidated `PLAN_CONFIRMED` Planning Snapshot.
2. The installed version, archive digest, tree digest, and inventory are
   verified before every launch; the script path must remain inside it.
3. The Python executable and required distribution files are re-attested
   against the Host-captured runtime contract.
4. Workspace and command paths are Host-derived from safe identifiers and
   relative paths; symlinks/reparse points are rejected before and after run.
5. The child environment contains no Host API keys or inherited secret-like
   variables.  Network, registry, ctypes loading, Shell, and nested process
   events are denied before bundled code is loaded.
6. A Windows Job is assigned while the child is suspended, then limits the
   job to one active process, bounded memory/CPU, and kill-on-close.
7. Wall timeout and cooperative cancellation kill the entire Job.  Stdout and
   stderr are drained with hard capture limits so a child cannot block pipes or
   grow Host memory without bound.
8. Third-party writes are command-specific and exclude `audit/`.  Host logs
   and result records are written after execution and include content digests.
9. Artifacts must remain within the command's clean output roots and respect
   count, per-file, and total-byte limits; every accepted artifact is hashed.
10. A failed, cancelled, timed-out, or resource-rejected run is never reported
    as success and never authorizes publication.

## Forbidden changes

- no changes to `core/ai_client.py`, UI, `main.py`, Report Provider,
  report Bridge/Builder, Skill Runtime/Registry, requirements, or AppData;
- no registration of PPT Master as a normal Skill and no credential transfer;
- no Shell string, `shell=True`, PATH executable lookup, arbitrary environment,
  raw subprocess arguments, or fallback execution path;
- no weakening of Managed Source Bundle verification or Planning confirmation;
- no real report/PPTX generation in this batch;
- no skip, xfail, deselection, deletion, or weakening of tests.

## Acceptance gates

1. Typed commands compile to the exact frozen allowlist and reject traversal,
   unconfirmed plans, duplicate run IDs, dirty outputs, and identity mismatch.
2. Fixture-worker tests prove successful execution, nested-process/network/
   outside-write denial, timeout, cancellation, bounded logs, and provenance.
3. Failed runs retain Host-written audit evidence and cannot return success.
4. Focused tests run once with zero failures/skips/deselections; changed-file
   Pyright has zero errors/warnings; compileall and Git scope audits pass.
