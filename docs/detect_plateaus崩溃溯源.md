# detect_plateaus 崩溃溯源 — 代理信号列 `_d` 不存在

**日期**: 2026-06-28  
**分支**: llama-cpp  
**状态**: 只记现状 + 根因 + 证据，不给方案

---

## 1. `_d` 列是什么、谁生成

### 1.1 `_d` 列的含义

`_d` 后缀列 = **波长漂移 dL (pm)** = `(λ - λ_base) × 1000`。

生成函数：`compute_dL()`（`dp_engine/calibration/temperature_calibration.py:21-66`）

```python
# temperature_calibration.py:53-55
for col in wavelength_cols:
    dcol = f"{col}_d"
    df[dcol] = (df[col].astype(float) - float(base[col])) * 1000.0
```

对每个波长列 `w{N}`，新增一列 `w{N}_d`，值为该行波长减去首行基准波长，单位 pm。

### 1.2 `detect_plateaus` 为什么需要 `_d` 列

`detect_plateaus()`（`dp_engine/calibration/step_extractor.py:28-37`）签名：

```python
def detect_plateaus(
    df: pd.DataFrame,
    wavelength_cols: list[str],
    *,
    ...
    proxy_cols: Optional[list[str]] = None,
    delta_suffix: str = "_d",          # ← 默认后缀
) -> pd.DataFrame:
```

关键逻辑（`step_extractor.py:66-82`）：

```python
proxy_cols = proxy_cols or wavelength_cols       # line 66
proxy_names = [f"{c}{delta_suffix}" for c in proxy_cols]  # line 67
# → 若 wavelength_cols = ['w1','w2'], 则 proxy_names = ['w1_d','w2_d']

proxy_series = None
for name in proxy_names:
    if name in df.columns:           # line 72 — 在 df 中查找 _d 列
        ...
if proxy_series is None:
    raise ValueError(                # line 79 — 找不到 → 崩溃
        f"代理信号列不存在: {proxy_names}。"
        f"可用列: {list(df.columns)}"
    )
```

**`detect_plateaus` 永远需要 `_d` 后缀列**，因为 `delta_suffix` 默认 `"_d"`，且没有回退到裸列名的逻辑。

---

## 2. `_auto_match` 传入了什么

### 2.1 调用链

```
用户点击 Phase A 对话框的「🔄 自动匹配」按钮
  → DetectionParamsDialog._auto_match()
    → detect_plateaus(self._df, self._wavelength_cols, ...)
```

`calibration_tab.py:433-449`

### 2.2 `self._df` 是什么

`DetectionParamsDialog.__init__()`（`calibration_tab.py:280-288`）接收参数：

```python
def __init__(self, current_params: dict, df=None, wavelength_cols=None,
             n_expected=None, time_col_idx=None, parent=None):
    ...
    self._df = df                     # ← 直接保存，不做任何处理
    self._wavelength_cols = wavelength_cols or []
```

调用方 `PhaseADialog._open_detect()`（`calibration_tab.py:1461-1469`）：

```python
def _open_detect(self):
    _, wave_cols = detect_numeric_wavelength_columns(self._df)
    dlg = DetectionParamsDialog(self._params, self._df, wave_cols, ...)
    #                                         ^^^^^^^^
    #                                         原始 df，无 _d 列
```

而 `PhaseADialog._df` 本身来自 `TemperatureCalibrationPage._loaded_df`（`calibration_tab.py:2862-2863`），即 `parse_enlight_file()` 的原始输出 —— **不含 `_d` 列**。

### 2.3 `PhaseAWorker.run()` 的正确做法（对照）

`calibration_tab.py:544-560`：

```python
def run(self):
    df, base = compute_dL(self.df, self.wavelength_cols)  # ← 先算 _d
    P = detect_plateaus(df, self.wavelength_cols, ...)     # ← 再检测
```

**PhaseAWorker 先调 `compute_dL` 再加 `_d` 列，然后才调 `detect_plateaus`。所以主运行路径正常。**

---

## 3. 为什么 `_d` 列缺失

### 3.1 直接原因

`_auto_match`（和 `_preview`）直接对 `self._df` 调用 `detect_plateaus`，而 `self._df` 是原始解析的 DataFrame（只有 `w1..w12` 裸波长列），没有任何 `_d` 列。`detect_plateaus` 构造 `proxy_names = ['w1_d', ..., 'w12_d']` 后在 df 中找不到，抛 `ValueError`。

### 3.2 `_preview` 同样会崩（但被 try/except 兜住）

`calibration_tab.py:409-431`：

```python
def _preview(self):
    try:
        P = detect_plateaus(
            self._df, self._wavelength_cols, ...   # ← 同样没算 _d
        )
        ...
    except Exception as e:
        self.match_info.setText(f"⚠ 预览失败: {e}")  # ← 兜住，不崩
```

`_preview` 有 try/except → 显示"预览失败"而不崩。`_auto_match` **没有** try/except → 裸抛异常崩对话框。

### 3.3 `_anomaly` 列的出现

真机 traceback 中可用列含 `Timestamp_anomaly`、`w1_anomaly` 等。这些列来自数据清洗模块（`analysis_tab._clean_data → clean_data()`）。温度标定页的数据来源是 `parse_enlight_file()` 直接解析 ENLIGHT 文件（`calibration_tab.py:2648`），正常情况下不含 `_anomaly` 列。可能情况：
- 用户用了一个被清洗过并重新保存的 ENLIGHT 文件
- 或本会话的某个路径将清洗后数据误写入了标定页的 df

但 `_anomaly` 列只是混淆项：无论有没有它，`_d` 列都不会凭空出现，`_auto_match` 都会崩。

---

## 4. 是否本会话引入

### 4.1 git 证据：预存 bug

`_auto_match` 和 `_preview` 在提交 `7fad573`（传感器标定模块 v2.1 初始提交）中引入：

```
$ git show 7fad573 -- ui/calibration_tab.py | grep -n "_auto_match\|_preview"
295:+class DetectionParamsDialog(QDialog):
346:+        self.auto_match_btn = create_button("🔄 自动匹配", self._auto_match, ...
349:+        self.preview_btn = create_button("👁 预览检出平台", self._preview, ...
434:+    def _preview(self):
458:+    def _auto_match(self):
```

从 `7fad573` 开始，`_auto_match` 和 `_preview` 就**始终**对原始 df 直接调 `detect_plateaus`，从未先算 `compute_dL`。

### 4.2 本会话 diff 确认

```diff
# llama-cpp 分支 vs HEAD — 仅 import 路径改名 + annotation_dirty 加字段
-from py.calibration.step_extractor import detect_plateaus
+from dp_engine.calibration.step_extractor import detect_plateaus
```

**本会话未改动 `_auto_match` / `_preview` / `DetectionParamsDialog` 的任何逻辑。**

### 4.3 定性

**(a) 预存** — `7fad573`（传感器标定初始提交）引入时即有此 bug。`PhaseAWorker.run()` 的 `compute_dL → detect_plateaus` 正确链和 `DetectionParamsDialog._auto_match` 的裸调 `detect_plateaus` 同时存在于同一提交，属**设计遗漏**——UI 快捷按钮的调用方没有同步 `compute_dL` 前置步骤。

---

## 5. 根因指向

**根因 (a) — `_d` 列生成步骤没跑**：`_auto_match` 和 `_preview` 对着原始 df（无 `_d` 列）直接调 `detect_plateaus`，跳过了必须的 `compute_dL`。

`PhaseAWorker.run()` 的正确顺序是：
```
compute_dL(df) → df 多了 w{N}_d 列 → detect_plateaus(df) → 成功
```

`_auto_match` 的实际顺序是：
```
原始 df（无 _d 列） → detect_plateaus(df) → ValueError
```

`_preview` 同样顺序但被 try/except 兜住，不崩只显示"预览失败"。

**排除项**：
- (b) `_anomaly` 列混入 → 不相关，只是混淆噪音
- (c) 列名规则变更 → 未变更，`delta_suffix="_d"` 从初始提交即如此
- (d) 本会话连带破坏 → **否**，`git diff` 证实仅 import 改名

**影响面**：
| 按钮 | 崩溃？ | 行号 |
|------|--------|------|
| 🔄 自动匹配 | ✅ 裸崩（无 try/except） | `calibration_tab.py:443-449` |
| 👁 预览检出平台 | ⚠ 静默失败（try/except 兜住，显示"预览失败"） | `calibration_tab.py:416-431` |

---

## 附录：关键文件索引

| 文件 | 关键符号 | 行号 |
|------|---------|------|
| `dp_engine/calibration/step_extractor.py` | `detect_plateaus` 函数签名（`delta_suffix="_d"`） | 28-37 |
| `dp_engine/calibration/step_extractor.py` | 代理信号列查找 + ValueError | 66-82 |
| `dp_engine/calibration/temperature_calibration.py` | `compute_dL` — 生成 `_d` 列 | 21-66 |
| `dp_engine/calibration/temperature_calibration.py` | `_d` 列赋值 | 53-55 |
| `ui/calibration_tab.py` | `DetectionParamsDialog.__init__` — `self._df = df` | 280-288 |
| `ui/calibration_tab.py` | `DetectionParamsDialog._preview` — 裸调 `detect_plateaus` + try/except 兜 | 409-431 |
| `ui/calibration_tab.py` | `DetectionParamsDialog._auto_match` — 裸调 `detect_plateaus` **无 try/except** | 433-460 |
| `ui/calibration_tab.py` | `PhaseAWorker.run` — 正确顺序：`compute_dL` → `detect_plateaus` | 544-560 |
| `ui/calibration_tab.py` | `PhaseADialog._open_detect` — 传原始 df 给 `DetectionParamsDialog` | 1461-1469 |
| `ui/calibration_tab.py` | `PhaseADialog.__init__` — `self._df = loaded_df` | 1052-1055 |
| `ui/calibration_tab.py` | `TemperatureCalibrationPage._loaded_df` — 由 `parse_enlight_file` 赋值 | 2648-2649 |
| `ui/calibration_tab.py` | `TemperatureCalibrationPage._open_phase_a` — 传 `_loaded_df` 给 `PhaseADialog` | 2861-2863 |
