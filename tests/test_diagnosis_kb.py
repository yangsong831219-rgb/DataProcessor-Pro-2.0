"""DiagnosisKB tests — trigger eval, sensor rules, multisource, cleaning, dedup, safety.

Coverage:
- evaluate_trigger: basic, null guard, missing field, dangerous expression
- Sensor rules: hys_fail, sigma_good_but_hys_fail, sigma_and_hys_both_high, pass_excellent, single_grating_na, ke2_near_zero
- Multisource: ms_real_disagreement, ms_consistent, ms_systematic_bias not triggered (null thr)
- Cleaning: clean_channel_hotspot, clean_data_modified_note
- global: apparent_strain_primer (always)
- Dedup: same rule across multiple sensors merged
- sigma threshold override from grade_thresholds
- Severity ordering: high before medium before low before info
"""

from __future__ import annotations
import pytest
from types import SimpleNamespace


# ═══════════════════════════════════════════════════════════════════════
# Static imports (no disk I/O)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def kb():
    from core.diagnosis_kb import DiagnosisKB
    kb_instance = DiagnosisKB()
    # Inject known constants directly (bypass YAML for tests)
    kb_instance._constants = {
        "thr_hys_fail_pct_fs": 5.0,
        "thr_hys_warn_pct_fs": 4.0,
        "thr_sigma_excellent_pct_fs": 1.0,
        "thr_sigma_good_pct_fs": 2.0,
        "thr_sigma_pass_pct_fs": 4.0,
        "thr_temp_sens_high": None,
        "thr_e_std_high_pct_fs": None,
        "thr_repeat_high_pct_fs": None,
        "thr_ms_mae_high_pct_fs": None,
        "thr_channel_hotspot_factor": 3.0,
        "multisource_corr_high": 0.95,
        "multisource_corr_low": 0.80,
    }
    kb._rules = []
    return kb_instance


# ═══════════════════════════════════════════════════════════════════════
# C1 fixture   σ ≤ good 且 迟滞 FAIL → sigma_good_but_hys_fail
# ═══════════════════════════════════════════════════════════════════════

C1_METRICS = {
    "residual_sigma_pct_fs": 0.784,   # ≤ 2.0 (good)
    "hysteresis_max_pct_fs": 5.33,    # > 5.0 (FAIL)
    "repeatability_pct_fs": 0.82,
    "temp_sensitivity_max": None,
    "e_std": 81.5, "e_mean": 6.5, "e_range": 60.0,
    "fs": 1000, "comp_form": "lut",
    "low_confidence": False, "is_single_grating": False,
    "grade": "FAIL", "passed": False,
    "reasons": ["hysteresis_max=53.3 με (5.3%FS) > 5.0%FS (废品拦截: 迟滞超标)"],
    "Ke1": 1.15, "Ke2": 0.04,
}

# B1 fixture   σ > pass 且 迟滞 > fail → sigma_and_hys_both_high
B1_METRICS = {
    "residual_sigma_pct_fs": 6.369,
    "hysteresis_max_pct_fs": 14.78,
    "repeatability_pct_fs": 7.26,
    "temp_sensitivity_max": None,
    "e_std": 87.3, "e_mean": 56.9, "e_range": 100.0,
    "fs": 1000, "comp_form": "lut",
    "low_confidence": False, "is_single_grating": False,
    "grade": "FAIL", "passed": False,
    "reasons": ["hysteresis_max=147.8 με (14.8%FS) > 5.0%FS (废品拦截: 迟滞超标)"],
    "Ke1": 0.73, "Ke2": 1.12,
}

# A1 fixture   grade=优 passed=True → pass_excellent
A1_METRICS = {
    "residual_sigma_pct_fs": 1.129,
    "hysteresis_max_pct_fs": 3.06,
    "repeatability_pct_fs": 1.29,
    "temp_sensitivity_max": None,
    "e_std": 11.4, "e_mean": 22.4, "e_range": 15.0,
    "fs": 1000, "comp_form": "lut",
    "low_confidence": False, "is_single_grating": False,
    "grade": "优", "passed": True,
    "reasons": [],
    "Ke1": 1.18, "Ke2": 0.0008,
}

# Single grating fixture
SINGLE_METRICS = {
    "residual_sigma_pct_fs": None,
    "hysteresis_max_pct_fs": None,
    "repeatability_pct_fs": None,
    "is_single_grating": True,
    "grade": "N/A", "passed": False, "reasons": [],
    "Ke1": None, "Ke2": None,
    "e_std": None, "e_mean": None, "e_range": None, "fs": 1000,
    "comp_form": "", "low_confidence": False, "temp_sensitivity_max": None,
}


# ═══════════════════════════════════════════════════════════════════════
# Test 1: evaluate_trigger
# ═══════════════════════════════════════════════════════════════════════

class TestEvaluateTrigger:

    def test_always(self):
        from core.diagnosis_kb import evaluate_trigger
        assert evaluate_trigger("always", {})

    def test_simple_comparison(self):
        from core.diagnosis_kb import evaluate_trigger
        assert evaluate_trigger("residual_sigma_pct_fs > 5.0",
                                {"residual_sigma_pct_fs": 6.0})

    def test_simple_comparison_false(self):
        from core.diagnosis_kb import evaluate_trigger
        assert not evaluate_trigger("residual_sigma_pct_fs > 5.0",
                                    {"residual_sigma_pct_fs": 3.0})

    def test_any_in_reasons(self):
        from core.diagnosis_kb import evaluate_trigger
        ns = {"grade": "FAIL", "reasons": ["hysteresis超标", "sigma偏高"]}
        assert evaluate_trigger(
            'grade == "FAIL" and any("hysteresis" in r for r in reasons)', ns)

    def test_negative_uncategorized_exception(self):
        """Missing field → evaluate_trigger returns False, not raise."""
        from core.diagnosis_kb import evaluate_trigger
        assert not evaluate_trigger("missing_field > 5.0", {})

    def test_null_guard(self):
        """Null constant with None check → False (no-op)."""
        from core.diagnosis_kb import evaluate_trigger
        const = SimpleNamespace(thr_temp_sens_high=None)
        ns = {"temp_sensitivity_max": 10.0, "constants": const}
        assert not evaluate_trigger(
            "constants.thr_temp_sens_high != None and temp_sensitivity_max > constants.thr_temp_sens_high",
            ns)

    def test_null_guard_non_null_triggers(self):
        """Non-null constant + value exceeds → True."""
        from core.diagnosis_kb import evaluate_trigger
        const = SimpleNamespace(thr_temp_sens_high=5.0)
        ns = {"temp_sensitivity_max": 10.0, "constants": const}
        assert evaluate_trigger(
            "constants.thr_temp_sens_high != None and temp_sensitivity_max > constants.thr_temp_sens_high",
            ns)

    def test_dangerous_import_rejected(self):
        from core.diagnosis_kb import evaluate_trigger
        assert not evaluate_trigger("__import__('os')", {})

    def test_dangerous_dunder_attr_rejected(self):
        from core.diagnosis_kb import evaluate_trigger
        assert not evaluate_trigger("constants.__class__", {})


# ═══════════════════════════════════════════════════════════════════════
# Test 2: Sensor rules
# ═══════════════════════════════════════════════════════════════════════


def _make_rules(rules_data: list[dict]) -> list:
    from core.diagnosis_kb import KBRule
    return [KBRule(r) for r in rules_data]


def _retrieve(kb, sensor_data=None, ms_data=None, clean_data=None):
    return kb.retrieve(sensor_data or {}, ms_data, clean_data)


class TestSensorRules:

    def test_c1_hits_sigma_good_but_hys_fail(self, kb):
        kb._rules = _make_rules([
            {"id": "sigma_good_but_hys_fail", "scope": "sensor",
             "trigger": 'residual_sigma_pct_fs <= constants.thr_sigma_good_pct_fs and grade == "FAIL" and any("迟滞" in r for r in reasons)',
             "severity": "high", "meaning": "σ好但迟滞FAIL", "mechanism": "正交", "recommendation": "不误导"},
        ])
        hits = _retrieve(kb, sensor_data={"C1": C1_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "sigma_good_but_hys_fail"
        assert hits[0][1] == ["C1"]

    def test_b1_hits_sigma_and_hys_both_high(self, kb):
        kb._rules = _make_rules([
            {"id": "sigma_and_hys_both_high", "scope": "sensor",
             "trigger": 'residual_sigma_pct_fs > constants.thr_sigma_pass_pct_fs and hysteresis_max_pct_fs > constants.thr_hys_fail_pct_fs',
             "severity": "high", "meaning": "双高", "mechanism": "系统性", "recommendation": "排查"},
        ])
        hits = _retrieve(kb, sensor_data={"B1": B1_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "sigma_and_hys_both_high"

    def test_a1_hits_pass_excellent(self, kb):
        kb._rules = _make_rules([
            {"id": "pass_excellent", "scope": "sensor",
             "trigger": 'grade in ["优", "良"] and passed',
             "severity": "info", "meaning": "可用", "mechanism": "正常", "recommendation": "使用"},
        ])
        hits = _retrieve(kb, sensor_data={"A1": A1_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "pass_excellent"
        assert hits[0][1] == ["A1"]

    def test_single_grating_hits_na(self, kb):
        kb._rules = _make_rules([
            {"id": "single_grating_na", "scope": "sensor",
             "trigger": "is_single_grating",
             "severity": "info", "meaning": "单栅", "mechanism": "不解耦", "recommendation": "N/A"},
        ])
        hits = _retrieve(kb, sensor_data={"D1": SINGLE_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "single_grating_na"

    def test_ke2_near_zero(self, kb):
        kb._rules = _make_rules([
            {"id": "ke2_near_zero", "scope": "sensor",
             "trigger": "Ke2 is not None and abs(Ke2) < 0.01",
             "severity": "low", "meaning": "Ke2近零", "mechanism": "温度参考", "recommendation": "确认"},
        ])
        hits = _retrieve(kb, sensor_data={"A1": A1_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "ke2_near_zero"

    def test_c1_does_not_hit_sigma_high(self, kb):
        """C1 sigma_good=0.78 ≤ 4.0 (pass) → sigma_high should NOT fire."""
        kb._rules = _make_rules([
            {"id": "sigma_high", "scope": "sensor",
             "trigger": "residual_sigma_pct_fs > constants.thr_sigma_pass_pct_fs",
             "severity": "high", "meaning": "σ高", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, sensor_data={"C1": C1_METRICS})
        assert len(hits) == 0

    def test_hys_warn_on_marginal(self, kb):
        """A1 hys=3.06 < 4.0 (warn) → should NOT fire."""
        kb._rules = _make_rules([
            {"id": "hys_warn", "scope": "sensor",
             "trigger": "passed and hysteresis_max_pct_fs >= constants.thr_hys_warn_pct_fs",
             "severity": "medium", "meaning": "迟滞预警", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, sensor_data={"A1": A1_METRICS})
        assert len(hits) == 0

    def test_hys_fail_on_c1(self, kb):
        """C1 grade=FAIL + reasons含迟滞 → hys_fail fires."""
        kb._rules = _make_rules([
            {"id": "hys_fail", "scope": "sensor",
             "trigger": 'grade == "FAIL" and any("迟滞" in r for r in reasons)',
             "severity": "high", "meaning": "迟滞废品", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, sensor_data={"C1": C1_METRICS})
        assert len(hits) == 1
        assert hits[0][0].id == "hys_fail"


# ═══════════════════════════════════════════════════════════════════════
# Test 3: Multisource rules
# ═══════════════════════════════════════════════════════════════════════

class TestMultisourceRules:

    def test_low_corr_hits_disagreement(self, kb):
        kb._rules = _make_rules([
            {"id": "ms_real_disagreement", "scope": "multisource",
             "trigger": "corr < constants.multisource_corr_low",
             "severity": "high", "meaning": "不一致", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, ms_data=[{"corr": 0.6, "mae": 0.1, "rmse": 0.2}])
        assert len(hits) == 1
        assert hits[0][0].id == "ms_real_disagreement"

    def test_high_corr_hits_consistent(self, kb):
        kb._rules = _make_rules([
            {"id": "ms_consistent", "scope": "multisource",
             "trigger": 'corr >= constants.multisource_corr_high and (constants.thr_ms_mae_high_pct_fs == None or (mae / fs * 100) <= constants.thr_ms_mae_high_pct_fs)',
             "severity": "info", "meaning": "一致", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, ms_data=[{"corr": 0.97, "mae": 0.05, "rmse": 0.08, "fs": 1000}])
        assert len(hits) == 1
        assert hits[0][0].id == "ms_consistent"

    def test_ms_systematic_bias_not_triggered_with_null_threshold(self, kb):
        """null thr_ms_mae_high_pct_fs → ms_systematic_bias no-op."""
        kb._rules = _make_rules([
            {"id": "ms_systematic_bias", "scope": "multisource",
             "trigger": 'corr >= constants.multisource_corr_high and constants.thr_ms_mae_high_pct_fs != None and (mae / fs * 100) > constants.thr_ms_mae_high_pct_fs',
             "severity": "medium", "meaning": "系统偏置", "mechanism": "...", "recommendation": "..."},
        ])
        # thr_ms_mae_high_pct_fs is None → rule no-op
        hits = _retrieve(kb, ms_data=[{"corr": 0.97, "mae": 50.0, "rmse": 60.0, "fs": 1000}])
        assert len(hits) == 0


# ═══════════════════════════════════════════════════════════════════════
# Test 4: Cleaning rules
# ═══════════════════════════════════════════════════════════════════════

class TestCleaningRules:

    def test_hotspot(self, kb):
        kb._rules = _make_rules([
            {"id": "clean_channel_hotspot", "scope": "cleaning",
             "trigger": 'len(per_column_count) > 0 and max(per_column_count.values()) > constants.thr_channel_hotspot_factor * median(list(per_column_count.values()))',
             "severity": "medium", "meaning": "热点", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, clean_data={
            "per_column_count": {"w1": 5, "w2": 3, "w3": 200, "w4": 2, "w5": 4},
            "cleaning_has_run": True, "fill_method": "linear",
        })
        # median = 4, 200 > 4*3=12 → True
        assert len(hits) == 1
        assert hits[0][0].id == "clean_channel_hotspot"

    def test_data_modified_note(self, kb):
        kb._rules = _make_rules([
            {"id": "clean_data_modified_note", "scope": "cleaning",
             "trigger": "cleaning_has_run and fill_method is not None",
             "severity": "info", "meaning": "已填充", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb, clean_data={
            "per_column_count": {}, "cleaning_has_run": True, "fill_method": "linear",
        })
        assert len(hits) == 1
        assert hits[0][0].id == "clean_data_modified_note"


# ═══════════════════════════════════════════════════════════════════════
# Test 5: Dedup + severity + global
# ═══════════════════════════════════════════════════════════════════════

class TestDedupAndSeverity:

    def test_dedup_across_sensors(self, kb):
        """hys_fail across B1,B2,C1 → 1 rule with 3 objects."""
        kb._rules = _make_rules([
            {"id": "hys_fail", "scope": "sensor",
             "trigger": 'grade == "FAIL" and any("迟滞" in r for r in reasons)',
             "severity": "high", "meaning": "迟滞废品", "mechanism": "...", "recommendation": "..."},
        ])
        sd = {
            "B1": B1_METRICS,
            "B2": dict(B1_METRICS),  # copy
            "C1": C1_METRICS,
        }
        hits = _retrieve(kb, sensor_data=sd)
        assert len(hits) == 1
        assert sorted(hits[0][1]) == ["B1", "B2", "C1"]

    def test_severity_ordering(self, kb):
        """high before medium before low before info."""
        kb._rules = _make_rules([
            {"id": "pass_excellent", "scope": "sensor",
             "trigger": 'grade in ["优", "良"] and passed',
             "severity": "info", "meaning": "可用", "mechanism": "...", "recommendation": "..."},
            {"id": "ke2_near_zero", "scope": "sensor",
             "trigger": "Ke2 is not None and abs(Ke2) < 0.01",
             "severity": "low", "meaning": "Ke2近零", "mechanism": "...", "recommendation": "..."},
            {"id": "hys_fail", "scope": "sensor",
             "trigger": 'grade == "FAIL" and any("迟滞" in r for r in reasons)',
             "severity": "high", "meaning": "迟滞废品", "mechanism": "...", "recommendation": "..."},
        ])
        sd = {"B1": B1_METRICS}
        hits = _retrieve(kb, sensor_data=sd)
        severities = [h[0].severity for h in hits]
        assert severities == ["high"]  # B1 has both hys_fail(high) and ke2 not near-zero

    def test_global_always_rule(self, kb):
        kb._rules = _make_rules([
            {"id": "apparent_strain_primer", "scope": "global",
             "trigger": "always", "severity": "info",
             "meaning": "表观应变背景", "mechanism": "...", "recommendation": "..."},
        ])
        hits = _retrieve(kb)
        assert len(hits) == 1
        assert hits[0][0].id == "apparent_strain_primer"
        assert hits[0][1] is None


# ═══════════════════════════════════════════════════════════════════════
# Test 6: σ threshold override
# ═══════════════════════════════════════════════════════════════════════

class TestSigmaThresholdOverride:

    def test_grade_thresholds_override_yaml(self, kb):
        from core.diagnosis_kb import DiagnosisKB
        kb2 = DiagnosisKB()
        kb2._constants = {
            "thr_sigma_excellent_pct_fs": 1.5,
            "thr_sigma_good_pct_fs": 3.0,
            "thr_sigma_pass_pct_fs": 5.0,
        }
        gt = SimpleNamespace(
            thr_sigma_excellent_pct_fs=0.8,
            thr_sigma_good_pct_fs=1.5,
            thr_sigma_pass_pct_fs=3.0,
        )
        kb2.load_constants_from_thresholds(gt)
        assert kb2._constants["thr_sigma_excellent_pct_fs"] == 0.8
        assert kb2._constants["thr_sigma_good_pct_fs"] == 1.5
        assert kb2._constants["thr_sigma_pass_pct_fs"] == 3.0

    def test_partial_override_leaves_others(self, kb):
        from core.diagnosis_kb import DiagnosisKB
        kb2 = DiagnosisKB()
        kb2._constants = {
            "thr_sigma_excellent_pct_fs": 1.5,
            "thr_sigma_good_pct_fs": 3.0,
            "thr_sigma_pass_pct_fs": 5.0,
            "thr_hys_fail_pct_fs": 5.0,
        }
        # Only override sigma_good
        gt = SimpleNamespace(
            thr_sigma_excellent_pct_fs=None,
            thr_sigma_good_pct_fs=2.0,
            thr_sigma_pass_pct_fs=None,
        )
        kb2.load_constants_from_thresholds(gt)
        # Only sigma_good was overridden
        assert kb2._constants["thr_sigma_good_pct_fs"] == 2.0
        # Others unchanged
        assert kb2._constants["thr_sigma_excellent_pct_fs"] == 1.5
        assert kb2._constants["thr_sigma_pass_pct_fs"] == 5.0
        assert kb2._constants["thr_hys_fail_pct_fs"] == 5.0


# ═══════════════════════════════════════════════════════════════════════
# Test 7: RAG assembly
# ═══════════════════════════════════════════════════════════════════════

class TestRAGAssembly:

    def test_empty_hits(self, kb):
        from core.diagnosis_kb import get_kb
        kb = get_kb()
        result = kb.assemble_rag_prompt([])
        assert "无规则触发" in result

    def test_budget_truncation(self, kb):
        from core.diagnosis_kb import KBRule
        rules = [KBRule({
            "id": "rule_%d" % i, "scope": "sensor",
            "trigger": "always", "severity": "info",
            "meaning": "Test rule %d meaning" % i,
            "mechanism": "Mechanism for rule %d" % i,
            "recommendation": "Recommendation for rule %d" % i,
        }) for i in range(20)]
        hits = [(r, ["S1"]) for r in rules]
        result = kb.assemble_rag_prompt(hits, budget_chars=500)
        assert "另有" in result  # skipped indicator

    def test_summary_contains_meaning(self, kb):
        from core.diagnosis_kb import KBRule
        rule = KBRule({
            "id": "test_rule", "scope": "sensor",
            "trigger": "always", "severity": "medium",
            "meaning": "核心诊断要点",
            "mechanism": "物理机理说明",
            "recommendation": "可操作建议",
        })
        result = kb.assemble_rag_prompt([(rule, ["A1", "B1"])])
        assert "核心诊断要点" in result
        assert "物理机理说明" in result
        assert "可操作建议" in result
        assert "A1, B1" in result
