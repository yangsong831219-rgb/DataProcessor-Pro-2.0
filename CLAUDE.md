# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

DataProcessor Pro 2.0 — PyQt6 desktop app for FBG (Fiber Bragg Grating) sensor data processing and analysis. Single large `main.py` (~8800 lines) with backend modules in `py/`.

## Commands

```bash
# Run the app
python main.py

# Quick startup test (creates window, processes events, exits)
python test_run.py

# Install dependencies
pip install -r requirements.txt

# Syntax check (no test suite exists for main.py)
python -c "import py_compile; py_compile.compile('main.py', doraise=True)"
```

No linter, no test suite, no build system. Virtual env at `venv/`.

## Architecture

### Data flow

```
ENLIGHT/CSV/TXT/Excel → parse_file() → DataFrame
  → _auto_populate_fbgs(df) → FBG table (W1=FBG_A1, W2=FBG_A2, ...)
  → SensorSystem.calculate() → wavelength delta × formula constants → physical quantities
  → Cleaning → FFT/Stats → Word/PPT reports
```

### Core classes in `main.py`

| Class | Role |
|---|---|
| `DataTemplate` | Data file format definition (delimiter, skip_rows, columns) |
| `FBG` | Physical FBG sensor: `id` (W1), `channel` (FBG_A1), `wavelength_min/max` |
| `Sensor` | Virtual sensor: `formula` (W1 * k1), `constants` (k1=1000), `sensor_type` |
| `SensorSystem` | Manages FBGs + Sensors; `calculate(df)` returns physical quantities |
| `CleaningRule` | Anomaly detection rule (range, negative, adjacent_diff, nan, zero) |
| `DataProcessorWindow(QMainWindow)` | Main window — 5 tabs + report integration page |
| `SensorEditDialog` | Edits sensor formula/constants; auto-detects k-variables from formula text |
| `AIModelConfigDialog` | Manages AI model configs stored in `ai_models_config.json` |

### `py/` modules

| Module | Purpose |
|---|---|
| `multi_agent.py` | LangGraph 3-agent review system (Data Scientist → Auditor → Chief Scientist) |
| `wiki_system.py` | File-based wiki/knowledge base (markdown pages in `wiki_vault/`) |
| `agent_skill_hub.py` | Agent skill registry and execution |
| `analyzer.py` | Signal processing: `apply_filter()` (Butterworth), FFT |
| `formula.py` | `calculate()` — evaluates user-defined math expressions on column data |
| `cleaner.py` | Data cleaning pipeline |
| `processor.py` | Data file parsing and processing |
| `template.py` | Data template management |
| `report_builder/` | Word (`word_builder.py`) and PPT (`ppt_builder.py`) report generation |

### Tab structure (QTabWidget)

1. **数据文件** — Import/export data, template selection, ENLIGHT parsing
2. **数据清洗** — Anomaly detection rules, interpolation, Butterworth filtering
3. **光纤公式配置** — FBG definitions table + sensor formula configuration
4. **数据分析** — FFT, time-domain analysis, statistics, charting
5. **信息整合** — Word/PPT reports, project files, AI diagnosis, wiki, skill hub

## Key patterns

### FBG auto-detection
Loading data triggers `_auto_populate_fbgs(df)` which scans for `FBG_`-prefixed columns, clears existing FBGs, creates `FBG(W1, FBG_A1, 1520, 1590)`, etc. Wavelength range defaults to 1520–1590 nm.

### Formula evaluation (`SensorSystem._evaluate_sensor_formula`)
- Replaces `k\d+` constants with regex word-boundary matching (not `str.replace`)
- Converts `None` deltas to `float('nan')` so eval doesn't crash on `None * float`
- Unresolved k-constants fall back to 1.0 with a warning
- `fbg_delta` has both FBG ID keys (`W1`) and channel name aliases (`FBG_A1`)

### Empty FBG fallback
If `fbg_delta` is empty after normal matching, `calculate()` auto-detects `FBG_` columns and builds delta entries with both column-name and `W{idx}` keys.

### Config persistence
`save_config()` / `load_config()` persists all 4 module states (data file path, cleaning rules UI, FBGs+sensors, analysis selections + computed results) as JSON v2.0.

### Sensor types
`strain`, `temperature`, `displacement`, `inclination`, `pressure`, `decoupling` (dual-parameter matrix), `strain_cal`, `temp_cal`

## File notes

- `main.py` is ~8800 lines — single-file PyQt6 app. No separation between UI and business logic. When editing, use `Edit` tool with specific context strings to avoid ambiguity.
- `ai_models_config.json` — contains API keys. Do not commit.
- `wiki_vault/` — local markdown knowledge base, managed by `WikiFileSystem`.
- ENLIGHT data format: tab-delimited `.txt`, 104 header rows, Timestamp + FBG_ columns.
