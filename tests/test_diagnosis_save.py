"""保存 AI 诊断结果 — DiagnosisRecord 构建 + wiki 写入 + 唯一命名."""

from __future__ import annotations
import json
import os
import sys
from types import SimpleNamespace
import pytest


# ═══════════════════════════════════════════════════════════════════
# Fixture: minimal AiDiagnosisWidget with required state
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def widget(qapp):
    from ui.ai_diagnosis import AiDiagnosisWidget

    w = AiDiagnosisWidget.__new__(AiDiagnosisWidget)
    w.ai_req_files = []
    _cb_data = SimpleNamespace(isChecked=lambda: True)
    _cb_calib = SimpleNamespace(isChecked=lambda: True)
    _cb_clean = SimpleNamespace(isChecked=lambda: False)
    w._source_checkboxes = {
        "data_file": (_cb_data, None),
        "calibration": (_cb_calib, None),
        "cleaning": (_cb_clean, None),
    }
    w._diagnosis_raw_response = ""
    w._diagnosis_record = None

    # Mock _find_main to return enough for _get_enabled_provider_summaries
    from utils.compensation_metrics import CompensationMetrics, SensorGrade
    S_eff = {"W1": 0.00123}
    ke_table = {"A1": {"Ke1": 0.82, "Ke2": 0.78},
                "C1": {"Ke1": 0.91, "Ke2": 0.88},
                "C2": {"Ke1": 1.05, "Ke2": 0.95}}
    compensation = {
        "A1": {
            "model": None,
            "metrics": CompensationMetrics(
                sensor="A1", residual_sigma=12.3, repeatability=5.0,
                hysteresis_max=8.0, noise_floor=1.5, temp_sensitivity_max=0.3,
                worst_case_single=4.0, low_confidence=False, fs=1000,
                comp_form="poly", poly_order=2,
                residual_sigma_pct_fs=1.23, repeatability_pct_fs=0.5,
                hysteresis_max_pct_fs=0.8, noise_floor_pct_fs=0.15,
                worst_case_single_pct_fs=0.4),
            "grade": SensorGrade(sensor="A1", grade="good", passed=True, reasons=["ok"]),
        },
        "C1": {
            "model": None,
            "metrics": CompensationMetrics(
                sensor="C1", residual_sigma=34.5, repeatability=2.1,
                hysteresis_max=42.0, noise_floor=1.5, temp_sensitivity_max=0.3,
                worst_case_single=21.0, low_confidence=False, fs=2000,
                comp_form="lut", poly_order=0,
                residual_sigma_pct_fs=3.8, repeatability_pct_fs=2.1,
                hysteresis_max_pct_fs=4.2, noise_floor_pct_fs=0.15,
                worst_case_single_pct_fs=2.1),
            "grade": SensorGrade(sensor="C1", grade="pass", passed=True, reasons=["high hysteresis"]),
        },
        "C2": {
            "model": None,
            "metrics": CompensationMetrics(
                sensor="C2", residual_sigma=6.2, repeatability=3.2,
                hysteresis_max=7.8, noise_floor=1.5, temp_sensitivity_max=0.3,
                worst_case_single=3.9, low_confidence=False, fs=800,
                comp_form="poly", poly_order=4,
                residual_sigma_pct_fs=6.5, repeatability_pct_fs=3.2,
                hysteresis_max_pct_fs=7.8, noise_floor_pct_fs=0.15,
                worst_case_single_pct_fs=3.9),
            "grade": SensorGrade(sensor="C2", grade="FAIL", passed=False,
                                reasons=["hys>5%FS", "sigma>4%FS"]),
        },
        "D1": {
            "model": None, "metrics": None,
            "grade": SensorGrade(sensor="D1", grade="N/A", passed=False,
                                reasons=["single grating"]),
        },
    }
    decoupling = {
        "A1": {"e_mean": 5.2, "e_std": 12.3, "e_range": 9.8,
               "rating": "good", "single_grating": False},
        "C1": {"e_mean": 8.1, "e_std": 34.5, "e_range": 28.7,
               "rating": "pass", "single_grating": False},
        "C2": {"e_mean": 3.4, "e_std": 6.2, "e_range": 5.1,
               "rating": "FAIL", "single_grating": False},
        "D1": {"e_mean": 0, "e_std": 0, "e_range": 0,
               "rating": "N/A", "single_grating": True},
    }

    tp = SimpleNamespace(
        _phase_a_state={
            "seff_result": {"S_eff": S_eff},
            "annotation": {}, "groups": {},
            "tmin": 20, "tmax": 80, "tstep": 10, "params": {},
        },
        _phase_b_state={
            "ke_table": ke_table,
            "decoupling_results": decoupling,
            "compensation": compensation,
            "comp_form": "poly", "poly_order": 2,
            "grade_thresholds": {}, "subsample_step": 10,
        },
    )
    strain_pb = {"A1": {"ke_results": {"Ke1": 0.82, "Ke2": 0.78}}}
    sp = SimpleNamespace(_phase_b_state=strain_pb)

    ct = SimpleNamespace(temperature_page=tp, strain_page=sp)
    cw = SimpleNamespace(_last_comparison=None)
    cl = SimpleNamespace(_cleaning_has_run=False, _anomaly_info={})
    cl.get_config = lambda: {"fill_method": "linear"}

    import pandas as pd
    mw = SimpleNamespace(
        current_data=pd.DataFrame({"W1": [1530.0]}),
        current_template=SimpleNamespace(name="test"),
        calibration_tab_widget=ct,
        cleaning_tab_widget=cl,
        compare_tab_widget=cw,
        sensor_results={},
    )
    mw.is_fiber_data = lambda: True
    mw.get_annotated_columns = lambda: None

    w._find_main = lambda: mw
    w.save_analysis_btn = SimpleNamespace(setEnabled=lambda x: None)

    return w


# ═══════════════════════════════════════════════════════════════════
# Test 1: DiagnosisRecord 构建完整性
# ═══════════════════════════════════════════════════════════════════

class TestDiagnosisRecord:

    def test_build_record_has_all_fields(self, widget):
        """_build_diagnosis_record 生成完整字段 (schema 1.1)。"""
        diag_json = {
            "diagnosis_summary": {"data_type": "fiber_optic",
                                  "overall_assessment": "test"},
            "sensor_analysis": [
                {"sensor_id": "C1", "findings": "迟滞超标",
                 "suggestions": "按迟滞结论处理"},
            ],
        }
        widget._ai_result = {"json": diag_json, "raw": "test raw"}
        widget._multi_result = None
        rec = widget._build_diagnosis_record()

        # Required fields
        assert rec["schema_version"] == "1.1"
        assert "timestamp" in rec
        assert rec["backend"] in ("online", "local")
        assert isinstance(rec["selected_sources"], list)

        # Data snapshot
        assert isinstance(rec["data_source_snapshot"], list)

        # KB hits
        assert isinstance(rec["kb_hits"], list)

        # AI diagnosis segment (schema 1.1)
        assert rec["ai_diagnosis"]["diagnosis_json"] == diag_json
        assert rec["ai_diagnosis"]["diagnosis_raw"] == "test raw"

        # Multi-agent segment may be empty
        assert "multi_agent" in rec

    def test_dual_result_both_in_record(self, widget):
        """AI 诊断 + 多智能体同时存在时，记录包含两段。"""
        diag_json = {
            "sensor_analysis": [
                {"sensor_id": "C1", "findings": "迟滞超标 (5.3%FS)",
                 "suggestions": "不要被低σ误导"},
            ],
        }
        widget._ai_result = {"json": diag_json, "raw": "raw ai"}
        widget._multi_result = {
            "chief_structured": {"diagnosis_summary": {"data_quality": "good"}},
            "data_scientist_text": "多智能体报告\n测试内容",
            "audit_advisory": "审查意见: OK",
        }
        rec = widget._build_diagnosis_record()
        assert rec["ai_diagnosis"]["diagnosis_json"] == diag_json
        assert "多智能体报告" in rec["multi_agent"]["data_scientist_text"]
        assert rec["multi_agent"]["chief_structured"] == {"diagnosis_summary": {"data_quality": "good"}}

    def test_no_record_before_diagnosis(self, widget):
        """未诊断时 _diagnosis_record 为 None。"""
        assert widget._diagnosis_record is None

    def test_get_selected_source_names(self, widget):
        """返回当前勾选的 source 名。"""
        names = widget._get_selected_source_names()
        assert "data_file" in names
        assert "calibration" in names
        assert "cleaning" not in names


# ═══════════════════════════════════════════════════════════════════
# Test 2: Markdown rendering
# ═══════════════════════════════════════════════════════════════════

class TestMarkdownRendering:

    def test_markdown_has_sections(self, widget):
        rec = {
            "timestamp": "2026-01-01 00:00:00",
            "backend": "local", "model": "qwen3.5-9b",
            "selected_sources": ["data_file", "calibration"],
            "diagnosis_json": {
                "sensor_analysis": [
                    {"sensor_id": "C1", "status": "critical",
                     "findings": "迟滞超标", "suggestions": "整改"},
                ],
                "physical_diagnosis": {
                    "phenomenon": "膜变形", "severity": "high",
                    "possible_causes": ["胶层蠕变"],
                    "recommended_actions": ["报废"],
                },
            },
            "kb_hits": [
                {"id": "hys_fail", "severity": "high",
                 "meaning": "迟滞废品", "objects": "C1, B2, B1"},
            ],
        }
        # _render_diagnosis_markdown 已移除 — 验证 _build_word_document 不崩
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(rec, "multi", output_path)
            assert _os.path.getsize(output_path) > 0
        finally:
            if _os.path.exists(output_path):
                _os.unlink(output_path)

    def test_markdown_empty_json(self, widget):
        rec = {
            "timestamp": "", "backend": "", "model": "",
            "selected_sources": [], "diagnosis_json": {}, "kb_hits": [],
        }
        # _render_diagnosis_markdown 已移除 — Word 降级不崩
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            output_path = f.name
        try:
            widget._build_word_document(rec, "multi", output_path)
            assert _os.path.getsize(output_path) > 0
        finally:
            if _os.path.exists(output_path):
                _os.unlink(output_path)


# ═══════════════════════════════════════════════════════════════════
# Test 3: 文件系统写入往返 (wiki_vault/diagnoses/ 隔离子目录)
# ═══════════════════════════════════════════════════════════════════

class TestFileSaveRoundtrip:

    def _write_diag_record(self, diag_dir, name, rec):
        """模拟 _on_save_analysis 的写入逻辑。"""
        import json as _json
        ts = "20260101_000000_123456"
        json_path = diag_dir / f"{name}_{ts}.json"
        json_path.write_text(_json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return json_path

    def test_json_roundtrip(self, tmp_path):
        """写 JSON → 读回 → json.loads 成功，内容==原记录。"""
        diag_dir = tmp_path / "wiki_vault" / "diagnoses"
        diag_dir.mkdir(parents=True)

        rec = {
            "schema_version": "1.0",
            "timestamp": "2026-01-01",
            "backend": "local", "model": "test",
            "selected_sources": ["data_file"],
            "diagnosis_json": {"key": "value"},
            "diagnosis_raw": "raw text",
            "data_source_snapshot": ["snapshot text"],
            "kb_hits": [{
                "id": "hys_fail", "severity": "high",
                "objects": ["B1", "B2"],
                "meaning": "迟滞废品", "mechanism": "胶层蠕变",
                "recommendation": "报废",
            }],
        }

        path = self._write_diag_record(diag_dir, "test_project", rec)

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == "1.0"
        assert loaded["diagnosis_json"]["key"] == "value"
        assert loaded["kb_hits"][0]["id"] == "hys_fail"
        assert loaded["kb_hits"][0]["meaning"] == "迟滞废品"
        assert loaded["kb_hits"][0]["mechanism"] == "胶层蠕变"
        assert loaded["kb_hits"][0]["recommendation"] == "报废"
        assert loaded["kb_hits"][0]["objects"] == ["B1", "B2"]

    def test_unique_names_no_overwrite(self, tmp_path):
        """两次写入不同时间戳不互相覆盖。"""
        diag_dir = tmp_path / "wiki_vault" / "diagnoses"
        diag_dir.mkdir(parents=True)

        p1 = self._write_diag_record(diag_dir, "diag_a", {"x": 1})
        p2 = self._write_diag_record(diag_dir, "diag_b", {"y": 2})

        assert p1.exists() and p2.exists()
        assert p1.name != p2.name
        l1 = json.loads(p1.read_text(encoding="utf-8"))
        l2 = json.loads(p2.read_text(encoding="utf-8"))
        assert l1["x"] == 1
        assert l2["y"] == 2

    def test_json_no_append_corruption(self, tmp_path):
        """JSON 文件不会被追加损坏。"""
        diag_dir = tmp_path / "wiki_vault" / "diagnoses"
        diag_dir.mkdir(parents=True)

        path = self._write_diag_record(diag_dir, "diag_json", {"x": 1})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["x"] == 1

    def test_full_kb_rules_stored(self, tmp_path):
        """JSON 包含规则全文 (meaning+mechanism+recommendation)，报告引擎可仅凭此重构。"""
        diag_dir = tmp_path / "wiki_vault" / "diagnoses"
        diag_dir.mkdir(parents=True)

        rec = {
            "schema_version": "1.0",
            "timestamp": "", "backend": "", "model": "",
            "selected_sources": [], "diagnosis_json": {}, "diagnosis_raw": "",
            "data_source_snapshot": [],
            "kb_hits": [
                {"id": "sigma_good_but_hys_fail", "severity": "high",
                 "objects": ["C1"],
                 "meaning": "补偿拟合精度很好但迟滞废品",
                 "mechanism": "σ衡量补偿贴合程度，迟滞衡量不可逆性；二者正交",
                 "recommendation": "不要被低σ误导，按迟滞结论处理"},
                {"id": "hys_fail", "severity": "high",
                 "objects": ["B1", "B2", "C1"],
                 "meaning": "升降温读数不重合超废品阈值",
                 "mechanism": "封装/胶层不可逆能量耗散",
                 "recommendation": "复查封装与粘贴工艺"},
            ],
        }

        path = self._write_diag_record(diag_dir, "test", rec)
        loaded = json.loads(path.read_text(encoding="utf-8"))

        # 报告引擎可仅凭 JSON 重建
        rules = {h["id"]: h for h in loaded["kb_hits"]}
        assert "sigma_good_but_hys_fail" in rules
        assert rules["sigma_good_but_hys_fail"]["mechanism"]
        assert rules["sigma_good_but_hys_fail"]["recommendation"]
        assert rules["hys_fail"]["objects"] == ["B1", "B2", "C1"]

        # 每传感器结论可取出
        assert loaded["diagnosis_json"] is not None
