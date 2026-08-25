"""Targeted regressions for calibration data validity and chart rendering."""

from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest
from PyQt6.QtWidgets import QApplication

from core.chart_bundle import _extract_calibration_series
from ui.calibration_tab import PhaseBWorker, _run_compensation_pipeline_static
from dp_engine.calibration.project_config import StrainSubConfig
from dp_engine.calibration.step_extractor import (
    detect_plateaus,
    filter_plateaus_by_adjacent_jumps,
)
from dp_engine.calibration.temperature_calibration import (
    build_temperature_program_sequence,
    regress_sensitivity,
    validate_temperature_program,
)
from utils.apparent_strain_comp import (
    apply_apparent_strain_comp,
    build_apparent_strain_lut,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def _strain_config(sensor_name: str, base_nm: float, slope: float) -> StrainSubConfig:
    readings = []
    for eps in (0.0, 500.0, 1000.0):
        readings.append({
            "disp_mm": eps * 80.0 / 1e6,
            "eps_theory": eps,
            "G1_C1_load": base_nm + slope * eps / 1000.0,
            "G2_C1_load": base_nm + 3.0 + slope * eps / 1000.0,
        })
    return StrainSubConfig(
        sensor_name=sensor_name,
        sensor_mode="dual_working",
        grating_map={"G1": f"{sensor_name}-1", "G2": f"{sensor_name}-2"},
        readings=readings,
        ke_results={"Ke1": slope, "Ke2": slope},
    )


def test_lut_training_temperature_domain_is_not_reported_as_out_of_bounds() -> None:
    """The exact samples used to construct a LUT must remain in its valid domain."""
    temperatures = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    apparent_strain = 2.0 * (temperatures - 25.0)

    lut = build_apparent_strain_lut(
        temperatures,
        apparent_strain,
        T_base=25.0,
        bin_width=2.0,
        min_count=1,
    )
    _, out_of_bounds = apply_apparent_strain_comp(
        apparent_strain,
        temperatures,
        lut,
    )

    assert lut.T_min == pytest.approx(10.0)
    assert lut.T_max == pytest.approx(50.0)
    assert not np.any(out_of_bounds)


def test_chart_ignores_near_zero_grating_when_a_working_grating_exists() -> None:
    """A near-zero Ke must not halve the displayed working-grating sensitivity."""
    reference_strain = [0.0, 500.0, 1000.0]
    readings = []
    for strain in reference_strain:
        readings.append(
            {
                "disp_mm": strain * 80.0 / 1e6,
                "eps_theory": strain,
                "G1_C1_load": 1550.0 - 0.0042 * strain / 1000.0,
                "G2_C1_load": 1540.0 + 1.1510 * strain / 1000.0,
            }
        )
    cfg = SimpleNamespace(
        readings=readings,
        gauge_length_mm=80.0,
        ke_results={"Ke1": -0.0042, "Ke2": 1.1510},
    )

    series = _extract_calibration_series(cfg)

    assert series is not None
    assert series["contributing_gratings"] == ["G2"]
    assert series["slope"] == pytest.approx(1.1510)
    assert series["measured"] == pytest.approx([0.0, 575.5, 1151.0])


def test_phase_b_does_not_fabricate_nominal_temperature_coefficients() -> None:
    """Phase B only receives Ke, so it must not present Ke as a KT reference."""
    worker = PhaseBWorker(
        df=pd.DataFrame(
            {
                "w1": [1550.0, 1550.1, 1550.2],
                "w2": [1540.0, 1540.1, 1540.2],
            }
        ),
        time_h=np.array([0.0, 1.0, 2.0]),
        wavelength_cols=["w1", "w2"],
        annotation_groups={
            "A1": [
                {"name": "A1-W1", "col_name": "w1"},
                {"name": "A1-W2", "col_name": "w2"},
            ]
        },
        S_eff_result={
            "w1": {"slope": 28.0, "T_base": 20.0},
            "w2": {"slope": 30.0, "T_base": 20.0},
        },
        strain_coeffs={"A1": {"Ke1": 1.2, "Ke2": 0.0}},
        run_compensation=False,
    )
    worker.run()

    assert worker._last_result is not None
    comparisons = worker._last_result["comparisons"]
    assert len(comparisons) == 2
    assert all(np.isnan(item["given_KT"]) for item in comparisons)
    assert all(np.isnan(item["diff_pm_per_C"]) for item in comparisons)


def test_selecting_a_sensor_uses_its_own_saved_readings(qapp) -> None:
    """A chart title, source labels, and calibration points must share one config."""
    from ui.calibration_tab import StrainCalibrationPage

    page = StrainCalibrationPage()
    a1 = _strain_config("A1", 1540.0, 1.2)
    a8 = _strain_config("A8", 1560.0, 1.8)
    page._strain_configs = {"A1": a1, "A8": a8}
    page._refresh_sensor_list(select_sensor="A8")

    page._on_sensor_selected(page.sensor_list.currentRow())

    assert page._current_sensor == "A8"
    assert page._grating_map == {"G1": "A8-1", "G2": "A8-2"}
    assert page._readings[1][1]["load"] == pytest.approx([1560.0, 1560.9, 1561.8])
    plotted = _extract_calibration_series(page._working_result)
    assert plotted is not None
    assert plotted["slope"] == pytest.approx(1.8)


def test_relabelled_existing_sensor_readings_are_rejected(qapp) -> None:
    """Changing A1 labels to A8 without replacing numbers must never create an A8 record."""
    from ui.calibration_tab import StrainCalibrationPage

    page = StrainCalibrationPage()
    page._strain_configs["A1"] = _strain_config("A1", 1540.0, 1.2)
    page._load_sensor_to_workspace("A1")
    page._grating_map = {"G1": "A8-1", "G2": "A8-2"}

    error = page._validate_readings_identity("A8")

    assert error is not None
    assert "A1" in error


def test_frozen_analysis_snapshot_cannot_save_a_later_sensor_workspace(qapp) -> None:
    """The config stored after a worker completes must use the submitted snapshot."""
    from ui.calibration_tab import StrainCalibrationPage

    page = StrainCalibrationPage()
    page._config["grating_kind"] = "dual_both"
    page._levels = [0.0, 0.04, 0.08]
    page._grating_map = {"G1": "A8-1", "G2": "A8-2"}
    page._readings = {
        1: {1: {"load": [1560.0, 1560.9, 1561.8]}},
        2: {1: {"load": [1563.0, 1563.9, 1564.8]}},
    }
    snapshot = page._capture_analysis_snapshot()

    page._grating_map = {"G1": "A1-1", "G2": "A1-2"}
    page._readings[1][1]["load"] = [1540.0, 1540.6, 1541.2]

    stored = page._build_strain_subconfig(snapshot)

    assert stored.sensor_name == "A8"
    assert stored.grating_map == {"G1": "A8-1", "G2": "A8-2"}
    assert stored.readings[1]["G1_C1_load"] == pytest.approx(1560.9)


def test_quality_filter_keeps_consistent_plateaus_and_reports_exclusions() -> None:
    """A weak fit may exclude a separate bad branch, but must report every exclusion."""
    x = np.tile(np.arange(0.0, 80.0, 10.0), 3)
    y = 27.0 * x - 200.0
    y[[1, 6, 10, 15]] += 450.0
    plateau_df = pd.DataFrame({"T_set": x, "w_d": y})

    result = regress_sensitivity(
        plateau_df,
        "w_d",
        "w",
        quality_filter=True,
        quality_r2_trigger=0.98,
        quality_residual_threshold_pm=100.0,
    )

    assert result["quality_filter_applied"]
    assert result["r2"] > 0.999
    assert result["used_plateaus"] < result["total_plateaus"]
    assert len(result["excluded_plateau_positions"]) == 4


def test_balanced_temperature_levels_are_not_weighted_by_duplicate_plateaus() -> None:
    """Repeated dwell detections must not tilt a 0–70 °C calibration line."""
    temperatures = np.repeat(np.arange(0.0, 80.0, 10.0), [3, 7, 1, 14, 7, 4, 3, 4])
    values = 25.0 * temperatures
    plateau_df = pd.DataFrame({"T_set": temperatures, "w_d": values})

    result = regress_sensitivity(
        plateau_df,
        "w_d",
        "w",
        balance_setpoints=True,
    )

    assert result["slope"] == pytest.approx(25.0)
    assert result["T_base"] == pytest.approx(0.0)
    assert result["total_plateaus"] == 8
    assert result["detected_plateaus"] == 43


def test_phase_b_uses_configured_range_and_skips_persistent_bad_reference() -> None:
    """A temperature-dominant grating outside 0–70 °C must yield gaps, not false temperatures."""
    # w1 is the temperature-dominant grating (Ke1≈0).  Its last two values
    # are serially displaced while w2 remains a valid redundant reference.
    worker = PhaseBWorker(
        df=pd.DataFrame(
            {
                "w1": [1550.0, 1550.7, 1549.5, 1549.4],
                "w2": [1540.0, 1540.7, 1541.4, 1542.1],
            }
        ),
        time_h=np.arange(4, dtype=float),
        wavelength_cols=["w1", "w2"],
        annotation_groups={
            "A5": [
                {"name": "A5-1", "col_name": "w1"},
                {"name": "A5-2", "col_name": "w2"},
            ]
        },
        S_eff_result={
            "w1": {"slope": 10.0, "T_base": 0.0},
            "w2": {"slope": 10.0, "T_base": 0.0},
        },
        strain_coeffs={"A5": {"Ke1": 0.0, "Ke2": 1.2}},
        run_compensation=False,
        temperature_range=(0.0, 70.0),
        temperature_reference_C=0.0,
        temperature_disagreement_limit_C=10.0,
    )
    worker.run()

    assert worker._last_result is not None
    sensor = worker._last_result["sensors"]["A5"]
    assert sensor["T_abs"].tolist()[:2] == pytest.approx([0.0, 70.0])
    assert np.isnan(sensor["T_abs"][2:]).all()
    assert sensor["temperature_invalid_count"] == 2


def test_phase_b_clamps_small_calibration_residuals_at_range_boundary() -> None:
    """A small low-end calibration residual is not a serial-data failure."""
    worker = PhaseBWorker(
        df=pd.DataFrame(
            {
                "w1": [1550.0, 1549.978, 1550.700],
                "w2": [1540.0, 1540.000, 1540.700],
            }
        ),
        time_h=np.arange(3, dtype=float),
        wavelength_cols=["w1", "w2"],
        annotation_groups={
            "A5": [
                {"name": "A5-1", "col_name": "w1"},
                {"name": "A5-2", "col_name": "w2"},
            ]
        },
        S_eff_result={
            "w1": {"slope": 10.0, "T_base": 0.0},
            "w2": {"slope": 10.0, "T_base": 0.0},
        },
        strain_coeffs={"A5": {"Ke1": 0.0, "Ke2": 1.2}},
        run_compensation=False,
        temperature_range=(0.0, 70.0),
        temperature_reference_C=0.0,
        temperature_disagreement_limit_C=10.0,
        temperature_range_tolerance_C=2.5,
    )
    worker.run()

    assert worker._last_result is not None
    sensor = worker._last_result["sensors"]["A5"]
    assert sensor["T_abs_raw"].tolist() == pytest.approx([0.0, -2.2, 70.0])
    assert sensor["T_abs"].tolist() == pytest.approx([0.0, 0.0, 70.0])
    assert sensor["temperature_invalid_count"] == 0


def test_compensation_marks_insufficient_valid_temperature_as_not_applicable() -> None:
    """Skipped serial rows are a data-quality result, not a compensation exception."""
    result = _run_compensation_pipeline_static(
        {
            "A5": {
                "single_grating": False,
                "eps_corr": np.array([0.0, 1.0, 2.0]),
                "T_abs": np.array([0.0, np.nan, np.nan]),
                "T_base": 0.0,
            }
        },
        {},
    )

    grade = result["A5"]["grade"]
    assert grade.grade == "N/A"
    assert not grade.passed


def test_cycle_program_requires_the_ordered_15_platforms() -> None:
    """A 0→70→0 cycle is 15 ordered levels, not merely 15 detected rows."""
    setpoints = list(range(0, 71, 10))
    expected = build_temperature_program_sequence(setpoints, "cycle")
    assert expected == [0, 10, 20, 30, 40, 50, 60, 70, 60, 50, 40, 30, 20, 10, 0]

    complete = pd.DataFrame({
        "mid": np.arange(len(expected)),
        "T_set": expected,
        "w_d": np.asarray(expected, dtype=float) * 25.0,
    })
    accepted, diagnostic = validate_temperature_program(
        complete, setpoints, "cycle", cycle_count=1,
    )
    assert diagnostic["is_complete"]
    assert diagnostic["complete_cycles"] == 1
    assert accepted["T_set"].tolist() == expected


def test_cycle_program_rejects_a_count_matched_but_disordered_sequence() -> None:
    """The former count-only auto-match must not accept a scrambled 15-row result."""
    setpoints = list(range(0, 71, 10))
    scrambled = [30, 40, 60, 0, 0, 70, 10, 10, 20, 20, 60, 70, 50, 40, 30]
    candidates = pd.DataFrame({
        "mid": np.arange(len(scrambled)),
        "T_set": scrambled,
        "w_d": np.asarray(scrambled, dtype=float) * 25.0,
    })
    accepted, diagnostic = validate_temperature_program(
        candidates, setpoints, "cycle", cycle_count=1,
    )
    assert len(candidates) == 15
    assert accepted.empty
    assert not diagnostic["is_complete"]
    assert diagnostic["complete_cycles"] == 0


def test_continuous_cycles_share_the_boundary_low_temperature_plateau() -> None:
    """Three continuous 0->70->0 cycles contain 43, not 45, logical dwells."""
    setpoints = list(range(0, 71, 10))
    expected = build_temperature_program_sequence(
        setpoints, "cycle", cycle_count=3,
    )

    assert len(expected) == 43
    assert expected[14:16] == [0.0, 10.0]
    assert expected[28:30] == [0.0, 10.0]

    plateaus = pd.DataFrame({
        "mid": np.arange(len(expected)),
        "T_set": expected,
        "w_d": np.asarray(expected, dtype=float) * 25.0,
    })
    accepted, diagnostic = validate_temperature_program(
        plateaus, setpoints, "cycle", cycle_count=3,
    )

    assert diagnostic["expected_platforms"] == 43
    assert diagnostic["complete_cycles"] == 3
    assert diagnostic["is_complete"]
    assert accepted["cycle_id"].tolist().count(1) == 15
    assert accepted["cycle_id"].tolist().count(2) == 14
    assert accepted["cycle_id"].tolist().count(3) == 14


def test_plateau_proxy_excludes_an_adjacent_jump_column_before_detection() -> None:
    """A serially displaced grating cannot define the common stability proxy."""
    rng = np.random.default_rng(7)
    stable = np.concatenate([
        np.full(100, 0.0), np.full(100, 200.0), np.full(100, 400.0),
    ]) + rng.normal(0.0, 0.2, 300)
    serial = stable.copy()
    serial[::2] += 100.0  # 100 pm = 0.1 nm jump, above the 0.05 nm limit
    data = pd.DataFrame({"good_d": stable, "bad_d": serial})

    plateaus = detect_plateaus(
        data,
        ["good", "bad"],
        rolling_window=11,
        std_percentile=50.0,
        min_plateau_samples=20,
        head_trim_ratio=0.5,
        quality_jump_threshold_nm=0.05,
    )

    assert plateaus.attrs["proxy_columns_used"] == ["good"]
    assert plateaus.attrs["proxy_columns_rejected"]["bad"] > 0


def test_monotonic_thermal_jumps_are_not_a_whole_grating_rejection() -> None:
    """Rapid heating belongs in diagnostics, not a global source-data reject."""
    thermal = np.repeat(np.arange(0.0, 800.0, 80.0), 20)
    smooth = np.repeat(np.arange(0.0, 400.0, 40.0), 20)
    data = pd.DataFrame({"thermal_d": thermal, "smooth_d": smooth})

    plateaus = detect_plateaus(
        data,
        ["thermal", "smooth"],
        rolling_window=11,
        std_percentile=50.0,
        min_plateau_samples=10,
        head_trim_ratio=0.5,
        quality_jump_threshold_nm=0.05,
        min_serial_jump_count=3,
    )

    assert plateaus.attrs["source_adjacent_jump_counts"]["thermal"] > 0
    assert "thermal" not in plateaus.attrs["proxy_columns_rejected"]


def test_only_plateaus_with_repeated_jumps_are_excluded() -> None:
    """Out-of-plateau transitions must not erase otherwise valid calibration data."""
    good = np.zeros(100, dtype=float)
    bad = good.copy()
    bad[40:50] = np.tile([0.0, 0.1], 5)
    data = pd.DataFrame({"w": bad})
    plateaus = pd.DataFrame({
        "idx_start": [0, 40], "idx_end": [30, 60], "T_set": [0.0, 10.0],
    })

    usable, excluded, counts = filter_plateaus_by_adjacent_jumps(
        data, "w", plateaus, jump_threshold_nm=0.05, min_jump_count=3,
    )

    assert counts == [0, 10]
    assert excluded == [1]
    assert usable["T_set"].tolist() == [0.0]
