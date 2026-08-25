from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, ValidationError

from dp_engine.ppt_master_host.bundle_store import (
    PptMasterBundleStore,
    PptMasterBundleStoreError,
    PptMasterInstalledBundle,
)
from dp_engine.ppt_master_host.controlled_runner import (
    ControlledRunStatus,
    ControlledToolError,
    ControlledToolLimits,
    ControlledToolRequest,
    ControlledToolRunner,
    FinalizeSvgArguments,
    ProcessExecution,
    ProcessLaunch,
    ProcessTermination,
    ProjectInitArguments,
    PptMasterInstalledToolchainVerifier,
    PptxStructure,
    PythonRuntimeAttestation,
    QualityStage,
    SvgQualityArguments,
    SvgToPptxArguments,
    _DEFAULT_REQUIRED_DISTRIBUTIONS,
    _capture_distribution,
)
from dp_engine.ppt_master_host.controlled_worker import (
    _ApprovedDependencyFinder,
    _check_read_path,
    _normalized,
)
from dp_engine.ppt_master_host.planning import (
    ColorPalette,
    CommunicationContract,
    ConfirmDesign,
    ConfirmOutline,
    ConfirmPlan,
    DesignContract,
    GenerateDesign,
    GenerateOutline,
    GenerateSlides,
    HostPlanningStateMachine,
    OutlinePlan,
    OutlineSection,
    PlanningPhase,
    PlanningRequest,
    PlanningSnapshot,
    SlideIntent,
    SlideIntentPlan,
    SlideLayout,
    TemplateMode,
    TypographySpec,
)
from dp_engine.ppt_master_host.source_bundle import PPT_MASTER_2_7_0_CONTRACT


ModelT = TypeVar("ModelT", bound=BaseModel)
_TREE_DIGEST = "1" * 64
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_TEST_RUNTIME = PythonRuntimeAttestation.capture(distributions=())


class ScriptedPlanningModel:
    identity = "scripted:runner-test"

    def __init__(self, outputs: list[BaseModel]) -> None:
        self.outputs = list(outputs)

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[ModelT],
        system_prompt: str,
        max_tokens: int,
        max_schema_retries: int = 1,
    ) -> ModelT:
        del prompt, system_prompt, max_tokens, max_schema_retries
        return schema.model_validate(self.outputs.pop(0))


class FakeToolchainVerifier:
    def __init__(self, install_path: Path, *, tree_sha256: str = _TREE_DIGEST) -> None:
        self.install_path = install_path
        self.tree_sha256 = tree_sha256
        self.calls = 0

    def verify(self, *, cancel_check=None) -> PptMasterInstalledBundle:
        del cancel_check
        self.calls += 1
        return PptMasterInstalledBundle(
            install_path=self.install_path,
            version=PPT_MASTER_2_7_0_CONTRACT.version,
            archive_sha256=PPT_MASTER_2_7_0_CONTRACT.archive_sha256,
            tree_sha256=self.tree_sha256,
            file_count=4,
            total_bytes=4,
            created=False,
        )


class FakeProcessAdapter:
    def __init__(
        self,
        *,
        termination: ProcessTermination = ProcessTermination.COMPLETED,
        exit_code: int | None = 0,
    ) -> None:
        self.termination = termination
        self.exit_code = exit_code
        self.launches: list[ProcessLaunch] = []

    def execute(self, launch: ProcessLaunch, *, cancel_check=None) -> ProcessExecution:
        del cancel_check
        self.launches.append(launch)
        if self.exit_code == 0:
            worker_request = json.loads(
                Path(launch.argv[-1]).read_text(encoding="utf-8")
            )
            tool_argv = worker_request["tool_argv"]
            if "--json-output" in tool_argv:
                report = Path(tool_argv[tool_argv.index("--json-output") + 1])
                report.write_text('{"schema":"fixture"}', encoding="utf-8")
        return ProcessExecution(
            termination=self.termination,
            exit_code=self.exit_code,
            duration_ms=5,
            stdout=b"",
            stderr=b"",
            stdout_observed_bytes=0,
            stderr_observed_bytes=0,
            stdout_sha256=_EMPTY_SHA256,
            stderr_sha256=_EMPTY_SHA256,
            stdout_truncated=False,
            stderr_truncated=False,
        )


def _planning_request() -> PlanningRequest:
    return PlanningRequest(
        request_id="runner-plan",
        report_title="受控执行测试",
        objective="验证执行门禁",
        audience="审核人",
        source_context="确定性测试上下文",
        requested_slide_count=3,
    )


def _outline() -> OutlinePlan:
    return OutlinePlan(
        deck_title="受控执行测试",
        narrative_arc="结论、证据、行动",
        sections=(
            OutlineSection(
                section_id="main",
                title="主线",
                purpose="完成三页验证",
                key_messages=("结论", "证据", "行动"),
                allocated_slides=3,
            ),
        ),
    )


def _design() -> DesignContract:
    return DesignContract(
        communication=CommunicationContract(
            objective="形成审核决策",
            audience_success="能确认执行范围",
            tone="专业克制",
            content_divergence="faithful",
        ),
        template_strategy=TemplateMode.FREE_DESIGN,
        visual_style="工程审计风格",
        palette=ColorPalette(
            primary="#17365D",
            secondary="#5B9BD5",
            accent="#ED7D31",
            background="#F7F9FC",
            text="#17202A",
        ),
        typography=TypographySpec(
            title_font="Microsoft YaHei",
            body_font="Microsoft YaHei",
            monospace_font="Cascadia Mono",
            title_size_px=40,
            body_size_px=22,
            caption_size_px=14,
        ),
        density="balanced",
        image_usage="none",
        chart_style="统一图表语义",
        refine_spec="保持留白和层级。",
        layout_rules=("每页一个结论",),
        accessibility_rules=("高对比文本",),
    )


def _slides() -> SlideIntentPlan:
    return SlideIntentPlan(
        deck_title="受控执行测试",
        slides=tuple(
            SlideIntent(
                slide_id=f"slide-{index}",
                section_id="main",
                sequence=index,
                title=f"第 {index} 页",
                message=f"消息 {index}",
                layout=(
                    SlideLayout.TITLE
                    if index == 1
                    else SlideLayout.CONCLUSION
                ),
                content_points=(f"要点 {index}",),
                visual_brief=f"视觉说明 {index}",
            )
            for index in range(1, 4)
        ),
    )


def _confirmed_snapshot() -> PlanningSnapshot:
    model = ScriptedPlanningModel([_outline(), _design(), _slides()])
    machine = HostPlanningStateMachine(model)
    snapshot = machine.start(_planning_request())
    for command in (
        GenerateOutline(),
        ConfirmOutline(),
        GenerateDesign(),
        ConfirmDesign(),
        GenerateSlides(),
        ConfirmPlan(),
    ):
        snapshot = machine.apply(snapshot, command)
    return snapshot


def _unconfirmed_snapshot() -> PlanningSnapshot:
    machine = HostPlanningStateMachine(ScriptedPlanningModel([]))
    return machine.start(_planning_request())


def _toolchain(
    tmp_path: Path,
    *,
    scripts: dict[str, str] | None = None,
    tree_sha256: str = _TREE_DIGEST,
) -> FakeToolchainVerifier:
    install = tmp_path / "toolchain"
    script_dir = install / "skills" / "ppt-master" / "scripts"
    script_dir.mkdir(parents=True)
    defaults = {
        "project_manager.py": "raise SystemExit(0)\n",
        "svg_quality_checker.py": """
import sys
from pathlib import Path
report = Path(sys.argv[sys.argv.index('--json-output') + 1])
report.write_text('{"schema":"fixture"}', encoding='utf-8')
raise SystemExit(0)
""",
        "finalize_svg.py": "raise SystemExit(0)\n",
        "svg_to_pptx.py": "raise SystemExit(0)\n",
    }
    defaults.update(scripts or {})
    for name, source in defaults.items():
        (script_dir / name).write_text(source, encoding="utf-8")
    return FakeToolchainVerifier(install, tree_sha256=tree_sha256)


def _runner(
    tmp_path: Path,
    *,
    scripts: dict[str, str] | None = None,
    process_adapter=None,
    runtime: PythonRuntimeAttestation = _TEST_RUNTIME,
    tree_sha256: str = _TREE_DIGEST,
) -> tuple[ControlledToolRunner, FakeToolchainVerifier]:
    verifier = _toolchain(tmp_path, scripts=scripts, tree_sha256=tree_sha256)
    runner = ControlledToolRunner(
        tmp_path / "workspaces",
        runtime=runtime,
        toolchain_verifier=verifier,
        process_adapter=process_adapter,
        expected_tree_sha256=_TREE_DIGEST,
    )
    return runner, verifier


def _quality_request(
    run_id: str,
    *,
    limits: ControlledToolLimits | None = None,
) -> ControlledToolRequest:
    return ControlledToolRequest(
        run_id=run_id,
        workspace_id="workspace-1",
        arguments=SvgQualityArguments(
            project_dir="project/demo",
            stage=QualityStage.FINAL,
        ),
        limits=limits or ControlledToolLimits(),
    )


def _prepare_project(tmp_path: Path) -> Path:
    project = tmp_path / "workspaces" / "workspace-1" / "project" / "demo"
    (project / "svg_final").mkdir(parents=True, exist_ok=True)
    (project / "svg_output").mkdir(exist_ok=True)
    (project / "validation").mkdir(exist_ok=True)
    return project


def test_typed_arguments_reject_traversal_and_raw_argument_injection() -> None:
    with pytest.raises(ValidationError):
        FinalizeSvgArguments(project_dir="../outside")
    with pytest.raises(ValidationError):
        ControlledToolRequest.model_validate(
            {
                "run_id": "run-1",
                "workspace_id": "workspace-1",
                "arguments": {
                    "kind": "svg_quality_check",
                    "project_dir": "project/demo",
                    "raw_args": ["--all", "C:/"],
                },
            }
        )


def test_unconfirmed_planning_snapshot_blocks_before_toolchain_verification(
    tmp_path: Path,
) -> None:
    adapter = FakeProcessAdapter()
    runner, verifier = _runner(tmp_path, process_adapter=adapter)

    with pytest.raises(ControlledToolError) as caught:
        runner.run(
            _unconfirmed_snapshot(),
            ControlledToolRequest(
                run_id="run-unconfirmed",
                workspace_id="workspace-1",
                arguments=ProjectInitArguments(project_name="demo"),
            ),
        )

    assert caught.value.code == "planning_not_confirmed"
    assert verifier.calls == 0
    assert adapter.launches == []


def test_exact_allowlisted_arguments_and_scrubbed_worker_launch(tmp_path: Path) -> None:
    adapter = FakeProcessAdapter()
    runner, verifier = _runner(tmp_path, process_adapter=adapter)
    project = _prepare_project(tmp_path)

    result = runner.run(_confirmed_snapshot(), _quality_request("run-exact"))

    assert result.succeeded is True
    assert verifier.calls == 1
    assert len(adapter.launches) == 1
    launch = adapter.launches[0]
    assert launch.argv[1:4] == ("-I", "-S", "-B")
    assert "API_KEY" not in launch.environment
    worker_request = json.loads(
        Path(launch.argv[-1]).read_text(encoding="utf-8")
    )
    assert worker_request["tool_argv"] == [
        str(project),
        "--format",
        "ppt169",
        "--stage",
        "final",
        "--json-output",
        str(project / "validation" / "run-exact.quality.json"),
    ]
    assert worker_request["tool_path"].endswith("svg_quality_checker.py")


def test_fixture_worker_executes_and_attests_project_artifacts(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
base = Path(sys.argv[sys.argv.index('--dir') + 1])
project = base / sys.argv[2]
project.mkdir(parents=True)
(project / 'README.md').write_text('fixture', encoding='utf-8')
print('created')
"""
    runner, _ = _runner(
        tmp_path,
        scripts={"project_manager.py": script},
    )
    request = ControlledToolRequest(
        run_id="run-init",
        workspace_id="workspace-1",
        arguments=ProjectInitArguments(project_name="demo"),
    )

    result = runner.run(_confirmed_snapshot(), request)

    assert result.status == ControlledRunStatus.SUCCEEDED
    assert [item.relative_path for item in result.artifacts] == [
        "project/demo/README.md"
    ]
    assert result.stdout.observed_bytes == result.stdout.captured_bytes
    audit = Path(result.audit_dir)
    assert (audit / "launch.json").is_file()
    assert (audit / "result.json").is_file()
    assert (audit / "stdout.log").read_bytes().strip() == b"created"


@pytest.mark.parametrize(
    ("run_id", "source", "blocked_event"),
    [
        (
            "run-subprocess",
            "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'pass'])\n",
            "subprocess.Popen",
        ),
        (
            "run-network",
            "import socket\nsocket.socket()\n",
            "socket.__new__",
        ),
    ],
)
def test_fixture_worker_denies_nested_process_and_network(
    tmp_path: Path,
    run_id: str,
    source: str,
    blocked_event: str,
) -> None:
    runner, _ = _runner(
        tmp_path,
        scripts={"svg_quality_checker.py": source},
    )
    _prepare_project(tmp_path)

    result = runner.run(_confirmed_snapshot(), _quality_request(run_id))

    assert result.status == ControlledRunStatus.FAILED
    assert result.error_code == "tool_failed"
    stderr = (Path(result.audit_dir) / "stderr.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    assert f"blocked audit event {blocked_event}" in stderr
    assert (Path(result.audit_dir) / "result.json").is_file()


def test_fixture_worker_denies_write_outside_command_root(tmp_path: Path) -> None:
    source = """
import sys
from pathlib import Path
project = Path(sys.argv[1])
(project.parent.parent / 'outside.txt').write_text('blocked', encoding='utf-8')
"""
    runner, _ = _runner(tmp_path, scripts={"finalize_svg.py": source})
    _prepare_project(tmp_path)
    request = ControlledToolRequest(
        run_id="run-outside",
        workspace_id="workspace-1",
        arguments=FinalizeSvgArguments(project_dir="project/demo"),
    )

    result = runner.run(_confirmed_snapshot(), request)

    assert result.status == ControlledRunStatus.FAILED
    assert not (
        tmp_path / "workspaces" / "workspace-1" / "outside.txt"
    ).exists()
    assert b"outside allowed roots" in (Path(result.audit_dir) / "stderr.log").read_bytes()


def test_wall_timeout_kills_the_controlled_job(tmp_path: Path) -> None:
    runner, _ = _runner(
        tmp_path,
        scripts={"svg_quality_checker.py": "import time\ntime.sleep(5)\n"},
    )
    _prepare_project(tmp_path)
    limits = ControlledToolLimits(timeout_seconds=1.0)

    result = runner.run(
        _confirmed_snapshot(),
        _quality_request("run-timeout", limits=limits),
    )

    assert result.status == ControlledRunStatus.TIMED_OUT
    assert result.error_code == "timeout"
    assert result.duration_ms < 4_000


def test_cooperative_cancellation_kills_the_controlled_job(tmp_path: Path) -> None:
    runner, _ = _runner(
        tmp_path,
        scripts={"svg_quality_checker.py": "import time\ntime.sleep(5)\n"},
    )
    _prepare_project(tmp_path)
    calls = 0

    def cancel_after_launch() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    result = runner.run(
        _confirmed_snapshot(),
        _quality_request("run-cancel"),
        cancel_check=cancel_after_launch,
    )

    assert result.status == ControlledRunStatus.CANCELLED
    assert result.error_code == "cancelled"
    assert result.duration_ms < 4_000


def test_log_limit_rejects_success_and_keeps_bounded_audit_file(tmp_path: Path) -> None:
    runner, _ = _runner(
        tmp_path,
        scripts={"svg_quality_checker.py": "print('x' * 5000)\n"},
    )
    _prepare_project(tmp_path)
    limits = ControlledToolLimits(max_stdout_bytes=1_024)

    result = runner.run(
        _confirmed_snapshot(),
        _quality_request("run-log-limit", limits=limits),
    )

    assert result.status == ControlledRunStatus.RESOURCE_REJECTED
    assert result.error_code == "log_limit_exceeded"
    assert result.stdout.observed_bytes > 1_024
    assert result.stdout.captured_bytes == 1_024
    assert (Path(result.audit_dir) / "stdout.log").stat().st_size == 1_024


def test_pptx_output_has_exact_workspace_relative_provenance(tmp_path: Path) -> None:
    source = """
import sys
from pathlib import Path
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.write_bytes(b'fake-pptx')
report = Path(sys.argv[1]) / 'validation' / f'{output.stem}.report.json'
report.write_text('{"schema":"fixture"}', encoding='utf-8')
"""
    runner, _ = _runner(tmp_path, scripts={"svg_to_pptx.py": source})
    _prepare_project(tmp_path)
    request = ControlledToolRequest(
        run_id="run-export",
        workspace_id="workspace-1",
        arguments=SvgToPptxArguments(
            project_dir="project/demo",
            output_name="deck.pptx",
            structure=PptxStructure.FLAT,
        ),
    )

    result = runner.run(_confirmed_snapshot(), request)

    assert result.status == ControlledRunStatus.SUCCEEDED
    assert len(result.artifacts) == 2
    artifact = next(
        item for item in result.artifacts
        if item.relative_path == "output/deck.pptx"
    )
    assert artifact.relative_path == "output/deck.pptx"
    assert artifact.size_bytes == len(b"fake-pptx")
    assert artifact.sha256 == hashlib.sha256(b"fake-pptx").hexdigest()
    restored = json.loads(
        (Path(result.audit_dir) / "result.json").read_text(encoding="utf-8")
    )
    assert restored["planning_snapshot_sha256"] == result.planning_snapshot_sha256


def test_export_can_treat_denied_optional_acl_subprocess_as_warning(
    tmp_path: Path,
) -> None:
    source = """
import subprocess
import sys
from pathlib import Path

output = Path(sys.argv[sys.argv.index('-o') + 1])
output.write_bytes(b'fake-pptx')
report = Path(sys.argv[1]) / 'validation' / f'{output.stem}.report.json'
report.write_text('{"schema":"fixture"}', encoding='utf-8')
try:
    subprocess.run(
        ['icacls', str(output), '/grant', '*S-1-5-32-545:R'],
        capture_output=True,
        text=True,
        check=False,
    )
except OSError as error:
    raise AssertionError(error)
"""
    runner, _ = _runner(tmp_path, scripts={"svg_to_pptx.py": source})
    _prepare_project(tmp_path)
    request = ControlledToolRequest(
        run_id="run-export-optional-acl",
        workspace_id="workspace-1",
        arguments=SvgToPptxArguments(
            project_dir="project/demo",
            output_name="deck.pptx",
            structure=PptxStructure.FLAT,
        ),
    )

    result = runner.run(_confirmed_snapshot(), request)

    assert result.status == ControlledRunStatus.SUCCEEDED
    assert result.error_code == ""
    assert [artifact.relative_path for artifact in result.artifacts] == [
        "output/deck.pptx",
        "project/demo/validation/deck.report.json",
    ]
    assert b"controlled_optional_subprocess_suppressed: icacls" in (
        Path(result.audit_dir) / "stderr.log"
    ).read_bytes()


def test_runtime_and_toolchain_identity_mismatch_fail_closed(tmp_path: Path) -> None:
    bad_runtime = _TEST_RUNTIME.model_copy(
        update={"executable_sha256": "0" * 64}
    )
    runner, _ = _runner(tmp_path, runtime=bad_runtime)
    _prepare_project(tmp_path)
    with pytest.raises(ControlledToolError) as runtime_error:
        runner.run(_confirmed_snapshot(), _quality_request("run-bad-runtime"))

    mismatched, verifier = _runner(
        tmp_path / "other",
        tree_sha256="2" * 64,
    )
    _prepare_project(tmp_path / "other")
    with pytest.raises(ControlledToolError) as tree_error:
        mismatched.run(
            _confirmed_snapshot(),
            _quality_request("run-bad-tree"),
        )

    assert runtime_error.value.code == "runtime_attestation_mismatch"
    assert tree_error.value.code == "toolchain_identity_mismatch"
    assert verifier.calls == 1


def test_installed_toolchain_verifier_preserves_integrity_failure_code() -> None:
    class IntegrityFailingStore:
        def verify_installed(self, *args, **kwargs):
            del args, kwargs
            raise PptMasterBundleStoreError(
                "integrity_mismatch",
                "Installed PPT Master file inventory does not match attestation",
            )

    store = cast(PptMasterBundleStore, IntegrityFailingStore())
    verifier = PptMasterInstalledToolchainVerifier(store)

    with pytest.raises(ControlledToolError) as caught:
        verifier.verify(cancel_check=None)

    assert caught.value.code == "toolchain_integrity_mismatch"
    assert "integrity_mismatch" in str(caught.value)


def test_restricted_dependency_root_remains_denied(
    tmp_path: Path,
) -> None:
    site_packages = tmp_path / "site-packages"
    approved_package = site_packages / "PIL"
    unapproved_package = site_packages / "unapproved"
    approved_package.mkdir(parents=True)
    unapproved_package.mkdir()

    allowed_roots = (_normalized(approved_package),)
    restricted_roots = (_normalized(site_packages),)
    approved_roots = (_normalized(approved_package),)

    with pytest.raises(PermissionError):
        _check_read_path(
            site_packages,
            allowed_roots=allowed_roots,
            restricted_roots=restricted_roots,
            approved_dependency_roots=approved_roots,
        )
    _check_read_path(
        approved_package / "__init__.py",
        allowed_roots=allowed_roots,
        restricted_roots=restricted_roots,
        approved_dependency_roots=approved_roots,
    )
    with pytest.raises(PermissionError):
        _check_read_path(
            unapproved_package / "secret.py",
            allowed_roots=allowed_roots,
            restricted_roots=restricted_roots,
            approved_dependency_roots=approved_roots,
        )


def test_default_runtime_attests_quality_checker_eager_dependencies() -> None:
    names = {name.casefold() for name in _DEFAULT_REQUIRED_DISTRIBUTIONS}

    assert "xlsxwriter" in names


def test_approved_dependency_finder_resolves_only_attested_packages(
    tmp_path: Path,
) -> None:
    site_packages = tmp_path / "site-packages"
    approved_package = site_packages / "PIL"
    unapproved_package = site_packages / "openpyxl"
    approved_package.mkdir(parents=True)
    unapproved_package.mkdir()
    (approved_package / "__init__.py").write_text("", encoding="utf-8")
    (unapproved_package / "__init__.py").write_text("", encoding="utf-8")

    finder = _ApprovedDependencyFinder(
        approved_dependency_roots=(str(approved_package),),
        dependency_sys_paths=(str(site_packages),),
    )

    approved = finder.find_spec("PIL", None)
    assert approved is not None
    assert approved.origin == str((approved_package / "__init__.py").resolve())
    assert finder.find_spec("openpyxl", None) is None
    assert finder.find_spec("PIL.Image", (str(approved_package),)) is None


def test_dependency_attestation_excludes_scripts_outside_import_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "venv"
    import_root = environment / "Lib" / "site-packages"
    package_file = import_root / "xlsxwriter" / "__init__.py"
    script_file = environment / "Scripts" / "vba_extract.py"
    package_file.parent.mkdir(parents=True)
    script_file.parent.mkdir(parents=True)
    package_file.write_text("VERSION = 'test'\n", encoding="utf-8")
    script_file.write_text("raise SystemExit(0)\n", encoding="utf-8")

    class FakeDistribution:
        version = "1.0"
        files = (
            Path("xlsxwriter/__init__.py"),
            Path("../../Scripts/vba_extract.py"),
        )

        @staticmethod
        def locate_file(entry: str) -> Path:
            return import_root / entry

    monkeypatch.setattr(
        "dp_engine.ppt_master_host.controlled_runner.importlib.metadata.distribution",
        lambda _name: FakeDistribution(),
    )

    attestation = _capture_distribution("XlsxWriter")

    assert attestation.file_count == 1
    assert attestation.import_root == str(import_root.resolve())
    assert attestation.read_roots == (str(package_file.parent.resolve()),)
    assert str(script_file.resolve()) not in attestation.read_roots


def test_duplicate_run_id_is_rejected_without_second_launch(tmp_path: Path) -> None:
    adapter = FakeProcessAdapter()
    runner, _ = _runner(tmp_path, process_adapter=adapter)
    _prepare_project(tmp_path)
    request = _quality_request("run-once")

    first = runner.run(_confirmed_snapshot(), request)
    with pytest.raises(ControlledToolError) as caught:
        runner.run(_confirmed_snapshot(), request)

    assert first.succeeded is True
    assert caught.value.code == "duplicate_run_id"
    assert len(adapter.launches) == 1
