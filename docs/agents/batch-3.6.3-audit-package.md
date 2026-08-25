# Batch 3.6.3 Audit Package - Controlled Tool Runner

## Decision

**PASS.** Batch 3.6.3 may close and Batch 3.6.4 may begin.

The Host now has one Controlled Tool Runner Seam for the reviewed PPT Master
toolchain.  It accepts typed operations only, requires a revalidated confirmed
Planning Snapshot, re-attests the toolchain and Python runtime, launches a
Host-owned isolated worker, and records bounded logs plus output provenance.

The real AppData installation was not executed or modified in this batch.

## Delivered architecture

- **Module:** `controlled_runner.py` owns confirmation authorization, exact
  argument compilation, workspace policy, dependency/toolchain attestation,
  process limits, cancellation, logs, output limits, and provenance.
- **Interface:** callers provide a confirmed Planning Snapshot and one strict
  `ControlledToolRequest`; they never provide an executable, script path,
  environment, working directory, or raw argument list.
- **Implementation:** four Pydantic-discriminated command contracts compile to
  four reviewed PPT Master script paths and fixed arguments.
- **Seam:** `ControlledProcessAdapter` separates policy/orchestration from the
  Windows process implementation.  Production uses
  `SubprocessControlledProcessAdapter`; deterministic tests use a fake Adapter.
- **Depth:** the caller learns one `run()` Interface while installation
  integrity, runtime file digests, path safety, Job Objects, audit hooks, log
  draining, termination, and artifact hashing remain internal.
- **Leverage:** the future Report Provider can invoke the same safe operations
  without duplicating execution or provenance rules.
- **Locality:** all new execution knowledge is contained in the PPT Master Host
  package and its worker; UI, Provider, Builder, Skill Runtime, and model code
  remain unchanged.

## Exact allowlist

1. `project_manager.py init <safe-name> --format ppt169 --dir <workspace>/project`
2. `svg_quality_checker.py <project> --format ppt169 --stage first-page|final`
3. `finalize_svg.py <project> --quiet`
4. `svg_to_pptx.py <project> -o <workspace>/output/<safe>.pptx`
   `-f ppt169 -q --pptx-structure flat|structured`

There is no `import-sources`, URL, server, update, image-search, narration,
animation, arbitrary source/output directory, Shell, or raw-command Interface.

## Verified security behavior

1. `PlanningSnapshot` is reconstructed through Pydantic and must be in
   `PLAN_CONFIRMED`; rejection occurs before toolchain verification or launch.
2. Managed Source Bundle version, archive SHA-256, tree SHA-256, and inventory
   are re-verified before every launch; scripts are Host-selected children of
   that installation.
3. Python executable and every declared dependency file are content-hashed.
   Execution uses `-I -S -B`: global site initialization is disabled and only
   attested dependency roots are placed on the import path.
4. The worker environment is rebuilt from a small safe set; secret-like
   variables and Host model credentials do not cross the Seam.
5. The child starts suspended, is assigned to a Windows Job with one active
   process, process CPU and memory limits, and kill-on-close, then is resumed.
6. Before third-party code loads, the worker installs audit hooks that reject
   nested process, Shell, socket, registry, ctypes loading, symlink, and
   out-of-policy filesystem events.
7. Fixture tests demonstrated nested-process, network, and outside-write
   attempts fail and retain Host-written `result.json` plus stderr evidence.
8. Wall timeout and cooperative cancellation terminate the Job.  Pipe-draining
   threads bound Host memory; exceeding a log limit rejects apparent success.
9. Output roots are clean and command-specific.  Accepted artifacts are
   regular non-reparse files within the workspace, bounded by count and bytes,
   and attested with relative path, size, and SHA-256.
10. Third-party code receives read-only access to the audit request and cannot
    write the Host-owned `audit/` directory.

## Verification commands and evidence

Focused pytest was executed once:

```powershell
& 'C:\Python314\python.exe' -m pytest '.\tests\test_ppt_master_controlled_runner.py' -q --basetemp '.\build_temp\pytest-363-focused'
```

Result: **13 passed**, **0 failed**, **0 skipped**, **0 deselected**, in
**2.95 seconds**.

Final static and compile gates:

```powershell
& 'C:\Python314\Scripts\pyright.exe' '.\dp_engine\ppt_master_host' '.\tests\test_ppt_master_source_bundle.py' '.\tests\test_ppt_master_bundle_store.py' '.\tests\test_ppt_master_host_planning.py' '.\tests\test_ppt_master_controlled_runner.py'
& 'C:\Python314\python.exe' -m compileall -q '.\dp_engine\ppt_master_host' '.\tests\test_ppt_master_controlled_runner.py'
```

Result: Pyright **0 errors / 0 warnings**; compileall **passed**.

Capability and scope audit:

- dangerous call matches (`shell=True`, free subprocess helpers, `os.system`,
  `os.popen`, `eval`, `exec`): **0**;
- network client import matches: **0**;
- direct Host/client credential attribute access matches: **0**;
- controlled `subprocess.Popen` calls: **1**;
- matching explicit `shell=False` calls: **1**;
- allowlist definition lines: **4**;
- focused pytest base temp exists after cleanup: **False**;
- tracked diff-name count: **50**, unchanged from incoming baseline;
- cached diff-name count: **0**.

Pytest emitted one non-functional warning because the repository's pre-existing
`.pytest_cache` path is inaccessible/conflicting.  Collection and all thirteen
test results were unaffected; this batch did not modify that unrelated cache.

## Frozen-scope audit

Batch-owned files are limited to:

- `CONTEXT.md`;
- `dp_engine/ppt_master_host/controlled_runner.py`;
- `dp_engine/ppt_master_host/controlled_worker.py`;
- `dp_engine/ppt_master_host/__init__.py`;
- `tests/test_ppt_master_controlled_runner.py`;
- `docs/agents/batch-3.6.3-controlled-runner-scope.md`;
- this audit package.

No change was made to `core/ai_client.py`, `main.py`, UI code, Report Provider,
report Bridge/Builder, Skill Runtime/Registry, requirements, or the managed
AppData installation.

## Honest security boundary

The Windows Job Object is an OS-enforced process-count/resource/termination
boundary.  Python audit hooks and restricted import roots are defense in depth,
not a universal sandbox against intentionally malicious native extensions that
bypass Python audit events.  This Runner is therefore authorized only for the
reviewed, hash-pinned PPT Master Source Bundle and its attested dependencies;
it must not be generalized to arbitrary plugins or unreviewed native code.

## Deferred by design

- The real PPT Master scripts and real report data were not executed.
- No report Provider or UI calls the Runner yet.
- No PPTX was rendered or visually accepted.
- No live DeepSeek or local Qwen request was repeated.

## Next gate

Batch 3.6.4 should implement the **PPT Master Report Provider Adapter**.  It
must translate an attested `ReportRenderRequest` and confirmed Planning
Snapshot into Controlled Runs, author only Host-owned project inputs, require
successful quality/finalization/export results, and return the final PPTX to
the existing report Orchestrator without bypassing its validation or atomic
publication rules.
