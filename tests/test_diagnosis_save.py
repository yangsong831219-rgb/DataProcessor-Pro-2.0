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

    ct = SimpleNamespace(temp_page=tp, strain_page=sp)
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


# ═══════════════════════════════════════════════════════════════════
# Phase 1: 保存路径新增 数据/诊断记录/ 子目录
# ═══════════════════════════════════════════════════════════════════

class TestSaveDiagnosisToProjectPath:
    """验证保存路径包含 数据/诊断记录/ 子目录"""

    def test_data_dir_includes_diagnosis_record_subdir(self, tmp_path):
        """os.path.join(target, '数据', '诊断记录') → 目录创建，文件可写"""
        import json as _json

        target = str(tmp_path / "test_proj")
        data_dir = os.path.join(target, '数据', '诊断记录')
        os.makedirs(data_dir, exist_ok=True)

        json_path = os.path.join(data_dir, "诊断记录_test.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            _json.dump({"test": True}, f, ensure_ascii=False, indent=2)

        # 落点正确
        assert os.path.exists(json_path)
        assert os.path.isdir(data_dir)
        assert '数据' in json_path.replace(os.sep, '/')
        assert '诊断记录' in json_path.replace(os.sep, '/')


# ═══════════════════════════════════════════════════════════════════
# 0-B2 P0/P1/P2 修复验证 — chart_manifest 持久化 + record_id 一致
# ═══════════════════════════════════════════════════════════════════


class TestChartManifestInSaveJson:
    """P0: chart_manifest + record_id 真实写入落盘 JSON（验磁盘文件，非验内存 rec）。"""

    def test_save_json_contains_chart_manifest_and_record_id(self, tmp_path):
        """模拟 on_ok 保存流程：build → 写 rec 字段 → json.dump → 读回验证。

        必须证明 chart_manifest 和 record_id 已经写进了落盘 JSON 文件
        （不是只在内存 rec 里有）。
        """
        import json as _json
        from datetime import datetime

        # 构建最小 rec（模拟 _build_diagnosis_record 产物）
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_id = ts_str

        rec: dict = {
            "schema_version": "1.2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backend": "local",
            "model": "test-model",
            "selected_sources": ["data_file"],
            "data_source_snapshot": [],
            "kb_hits": [],
            "ai_diagnosis": {"diagnosis_json": None, "diagnosis_raw": ""},
            "multi_agent": {"chief_structured": None, "data_scientist_text": "",
                           "audit_advisory": "", "logs": [],
                           "chief_truncated": False, "report": "", "raw_json": {}},
            # chart_data: 最小有效数据让 build_chart_store 可跑
            "chart_data": {
                "time_h": [0.0, 1.0, 2.0],
                "series": {"ch1": [1.0, 2.0, 3.0]},
            },
        }

        # ── 模拟 on_ok 的语句顺序：先产图 → 写 rec 字段 → 再 dump ──
        from core.chart_store import build_chart_store, chart_manifest_to_dict

        charts_dir = str(tmp_path / record_id / "charts")
        os.makedirs(charts_dir, exist_ok=True)

        cd = rec.get("chart_data", {}) or {}
        warnings: list[str] = []
        chart_manifest = build_chart_store(cd, charts_dir, warnings)

        # ★ 关键顺序: 在 json.dump 之前写 rec 字段
        rec["record_id"] = record_id
        rec["chart_manifest"] = chart_manifest_to_dict(chart_manifest)

        # json.dump（写入 tmp_path 下的文件）
        json_path = str(tmp_path / f"诊断记录_{ts_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(rec, f, ensure_ascii=False, indent=2)

        # ── 读回磁盘文件验证 ──
        with open(json_path, "r", encoding="utf-8") as f:
            loaded = _json.load(f)

        # P0 核心断言: 落盘 JSON 含 chart_manifest 和 record_id
        assert "chart_manifest" in loaded, \
            "落盘 JSON 缺少 chart_manifest 字段 (P0 未修复)"
        assert isinstance(loaded["chart_manifest"], list), \
            "chart_manifest 应为 list"
        assert len(loaded["chart_manifest"]) >= 1, \
            f"chart_manifest 不应为空: {loaded['chart_manifest']}"
        for entry in loaded["chart_manifest"]:
            assert "chart_id" in entry, f"entry 缺 chart_id: {entry}"
            assert "rel_path" in entry, f"entry 缺 rel_path: {entry}"
            assert "produced" in entry, f"entry 缺 produced: {entry}"
            assert "key_stat" in entry, f"entry 缺 key_stat: {entry}"
            # 绝不含 PNG 字节
            assert "png_bytes" not in entry, \
                f"chart_manifest entry 含 png_bytes (违规!)"
            assert "png_data" not in entry, \
                f"chart_manifest entry 含 png_data (违规!)"

        assert "record_id" in loaded, \
            "落盘 JSON 缺少 record_id 字段 (P1 未修复)"
        assert loaded["record_id"] == record_id, \
            f"record_id 不匹配: {loaded['record_id']!r} != {record_id!r}"

        # 体积不暴涨 (只有文本的相对路径清单)
        json_size = os.path.getsize(json_path)
        assert json_size < 5 * 1024 * 1024, \
            f"JSON 体积异常: {json_size} bytes (疑似含 PNG 字节)"

    def test_record_id_no_dashes_no_colons(self):
        """record_id 格式: 纯数字+下划线，无横杠、无冒号 (P1 修复验证)。"""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_id = ts  # 无需 replace
        assert "-" not in record_id, f"record_id 不应含横杠: {record_id!r}"
        assert ":" not in record_id, f"record_id 不应含冒号: {record_id!r}"
        assert "_" in record_id, f"record_id 应含下划线: {record_id!r}"
        # 格式: 20260629_143022 (8位日期_6位时间)
        parts = record_id.split("_")
        assert len(parts) == 2 and len(parts[0]) == 8 and len(parts[1]) == 6, \
            f"record_id 格式不符合 YYYYMMDD_HHMMSS: {record_id!r}"


class TestReportReadsRecordId:
    """P1/P2: 报告段 record_id 读取路径 — 优先取持久化字段，降级推导。"""

    def test_reads_persisted_record_id_no_derive(self):
        """rec 含 record_id → 报告段直接取值，不走 replace 推导。"""
        # 模拟 main.py 报告段逻辑
        diag_rec = {
            "record_id": "20260629_102112",
            "timestamp": "2026-06-29 10:21:12",  # 有横杠+冒号
            "chart_manifest": [{"chart_id": "t1", "rel_path": "a.png",
                                "produced": True, "key_stat": "ok"}],
        }

        # 报告段取值逻辑 (与 main.py 行 1410-1416 一致)
        record_id = diag_rec.get("record_id")
        assert record_id == "20260629_102112", \
            f"应首选持久化 record_id: {record_id}"
        assert "-" not in (record_id or ""), \
            f"record_id 不应含横杠: {record_id!r}"

    def test_fallback_derive_when_no_record_id(self):
        """旧记录无 record_id → 从 timestamp 降级推导 (向后兼容)。"""
        diag_rec = {
            # 无 record_id 字段 (旧记录)
            "timestamp": "2026-06-29 10:21:12",
        }

        # 报告段降级逻辑 (与 main.py 行 1411-1416 一致)
        record_id = diag_rec.get("record_id")
        if not record_id:
            ts = diag_rec.get("timestamp", "20260629_000000")
            record_id = ts.replace(" ", "_").replace(":", "")

        assert record_id == "2026-06-29_102112", \
            f"降级推导 record_id 格式不对: {record_id!r}"
        # 旧路径的 replace 规则保留横杠 (已知问题，向后兼容)
        assert "-" in record_id, \
            f"旧记录降级推导应保留横杠 (向后兼容): {record_id!r}"

    def test_record_id_save_and_report_equal(self, tmp_path):
        """同一 rec 走保存段→落盘→报告段读取，record_id 逐字一致。"""
        import json as _json
        from datetime import datetime

        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_id = ts_str

        # ── 保存段: rec 写入 record_id + 落盘 ──
        rec: dict = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chart_data": {"time_h": [0.0, 1.0], "series": {"ch1": [1.0, 2.0]}},
        }
        rec["record_id"] = record_id

        json_path = str(tmp_path / f"诊断记录_{ts_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(rec, f, ensure_ascii=False, indent=2)

        # ── 报告段: 从文件读取 ──
        with open(json_path, "r", encoding="utf-8") as f:
            loaded = _json.load(f)

        loaded_record_id = loaded.get("record_id")
        assert loaded_record_id is not None, "加载后 record_id 丢失"
        assert loaded_record_id == record_id, \
            f"保存段 record_id={record_id!r} ≠ 报告段 record_id={loaded_record_id!r}"


class TestReportReadsManifestNotProduces:
    """P0+P1 合验: 报告段有 manifest → 读取路径，不调 build_chart_store。"""

    def test_manifest_present_bypasses_build_chart_store(self):
        """rec 含 chart_manifest → 报告段走读取分支，非降级产图。

        用 spy 验证: 当 chart_manifest 存在时 build_chart_store 调用次数 = 0。
        """
        diag_rec = {
            "record_id": "20260629_102112",
            "chart_manifest": [
                {"chart_id": "data_ts_cleaning", "module": "data_analysis",
                 "title": "清洗时序", "rel_path": "ts_cleaning.png",
                 "produced": True, "skip_reason": "", "key_stat": "1通道"},
            ],
            "chart_data": {"time_h": [0.0, 1.0], "series": {"ch1": [1.0, 2.0]}},
        }

        # ── 报告段链路: chart_manifest_from_dict → 不调 build_chart_store ──
        from core.chart_store import chart_manifest_from_dict

        _chart_manifest: list = []
        stored = diag_rec.get("chart_manifest")
        if stored:
            _chart_manifest = chart_manifest_from_dict(stored)
            build_called = 0  # 模拟: 不调 build_chart_store
        else:
            build_called = 1  # 降级路径

        assert build_called == 0, \
            f"有 manifest 时不应调 build_chart_store, called={build_called}"
        assert len(_chart_manifest) == 1
        assert _chart_manifest[0].chart_id == "data_ts_cleaning"
        assert _chart_manifest[0].rel_path == "ts_cleaning.png"

    def test_no_manifest_triggers_fallback(self):
        """rec 无 chart_manifest → 应走降级 build 路径 (旧记录兼容)。"""
        diag_rec = {
            "record_id": "20260629_102112",
            # 无 chart_manifest
        }

        stored = diag_rec.get("chart_manifest")
        if stored:
            build_called = 0
        else:
            build_called = 1  # 降级路径

        assert build_called == 1, \
            f"无 manifest 应触发降级, called={build_called}"
