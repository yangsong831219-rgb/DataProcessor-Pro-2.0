# calib_lin 图丢失溯源 — 报告生成路径只走 provider 不出标定图

**日期**: 2026-06-28  
**分支**: llama-cpp  
**状态**: 只记现状，不给方案

**真机日志矛盾**:
```
gen_charts_from_bundle DONE: made=['calib_lin', 'ts_cleaning']   ← 做了2张
manifest_count=2                                                  ← manifest 记2张
[图表诊断] manifest 图数: 1                                        ← 只剩1张
[图表诊断] charts/ 落盘 PNG 数: 1 (cleaning_timeseries.png)        ← 只落盘1张
```

---

## 1. made 与落盘的差距 — 两次不同操作的日志交织

这四行日志来自 **两次独立操作**，分别用了不同目录和不同 manifest：

### 操作 1：保存诊断到项目

**入口**：`ai_diagnosis.py:1301-1305` — "保存诊断到项目"按钮 handler

```python
# line 1301-1305
manifest = FigureManifest()                        # ← manifest A
wrn: list[str] = []
gen_charts_from_diagnosis(rec, manifest, img_dir, wrn)
#     img_dir = 项目资料库/项目名/图片/              (line 1291)
```

**`gen_charts_from_diagnosis` 内部** (`chart_bundle.py:510`)：

```python
gen_charts_from_bundle(cd, manifest, charts_dir, warnings)
#     → calib_linearity.png  → charts_dir (line 314-315)
#     → ts_cleaning.png      → charts_dir (line 332-333)
```

日志 → `gen_charts_from_bundle DONE: made=['calib_lin', 'ts_cleaning']` (`chart_bundle.py:397`)  
日志 → `manifest_count=2` (`chart_bundle.py:511`)

→ PNG 落盘到 `项目资料库/项目名/图片/`，共 2 张。manifest A 有 2 张。

### 操作 2：生成报告

**入口**：`main.py:1282-1298` — 报告 worker 的 `_build_and_save` 函数

```python
# line 1288-1298
charts_dir = os.path.join(report_dir, 'charts')     # 报告/charts/
manifest = FigureManifest()                          # ← manifest B (新建)

# 1a. 实时 provider 取数
_generate_charts_from_providers(
    self, manifest, charts_dir, _report_warnings,     # line 1291
)

# 1b. 降级(诊断快照)
if manifest.count == 0 and diag_rec:                  # line 1299
    gen_charts_from_diagnosis(...)   → 但 count=1，不走这！！！
```

日志 → `[图表诊断] manifest 图数: 1` (`main.py:141`)  
日志 → `charts/ 落盘 PNG 数: 1 (cleaning_timeseries.png)` (`main.py:146`)

→ PNG 落盘到 `报告/charts/`，仅 1 张。manifest B 只有 1 张。

### 结论

**这不是 bug 在一个流程里丢图，而是 `_generate_charts_from_providers` 根本没生产 `calib_lin`。** calib_lin 是 `gen_charts_from_bundle` 的产物，报告路径不调它（只当 fallback 且 count != 0 时被跳过）。

---

## 2. calib_lin 落盘点 — 只在 `gen_charts_from_bundle` 里

`chart_bundle.py:307-322`（`gen_charts_from_bundle` 内部）：

```python
cal_ref = cd.get('calib_ref', []) or []
cal_meas = cd.get('calib_measured', []) or []
if cal_ref and cal_meas:
    try:
        fig = make_calibration_linearity(
            np.array(cal_ref, dtype=float), np.array(cal_meas, dtype=float),
            sensor=cd.get('calib_sensor', ''), unit=cd.get('calib_unit', 'με'),
        )
        png = _os.path.join(charts_dir, "calib_linearity.png")
        save_figure(fig, png)                    # ← 落盘点
        _made.append("calib_lin")
        manifest.add("calib_lin", ...)
    except Exception as e:
        _skipped.append(f"calib_lin({e})")       # ← 失败时进 skipped，不进 made
```

`_made.append("calib_lin")` 在 `save_figure()` 成功之后 (`line 314-316`)。`made` 列表确实证明落盘成功。

**对比 ts_cleaning 的落盘** (`chart_bundle.py:328-338`)：同样的 `save_figure` + `_made.append` 模式，两边没有结构性差异。两者在 `gen_charts_from_bundle` 里地位对等。

---

## 3. 两个"manifest 图数"日志 — 来自两个不同的 manifest 对象

| 日志 | 来源 | manifest | 操作 |
|------|------|---------|------|
| `manifest_count=2` | `chart_bundle.py:511` | manifest A（诊断保存） | `gen_charts_from_diagnosis after bundle` |
| `[图表诊断] manifest 图数: 1` | `main.py:141` | manifest B（报告生成） | `_generate_charts_from_providers` 末尾 |

两者是不同的 `FigureManifest()` 实例，**无过滤、无剔除、无重建**。日志矛盾是 `manifest A 有 2 张 ≠ manifest B 有 1 张`，不是因为某一步丢了一张——是两条路产出本就不同。

---

## 4. 是否静默丢

**没有**。calib_lin 没有被 try/except 吞掉。它在 `gen_charts_from_bundle` 中成功生成并落盘 (`made` 列表确认)。但那个 manifest A 是诊断保存的，报告生成的 manifest B 是另一个——calib_lin 根本没进入 manifest B。

`_gen_charts_for` (`main.py:150-255`) 是 provider 路径唯一的生产函数。看它的 CalibrationProvider 处理器 (`main.py:226-255`)：

```python
elif name == "传感器标定":
    ...
    # 迟滞回线: 每个有 T_abs+eps_orig 的传感器
    for s_name, sd in sensors.items():
        # → make_hysteresis_loop → save_figure → manifest.add(...)

    # ← 到此结束。没有任何 calib_lin/标定线性度图
    return
```

**`_gen_charts_for` 不生产 `calib_lin`。** 全文搜索 "calib_lin" 出现点：仅 `chart_bundle.py` 的 `gen_charts_from_bundle`。这两条路 (`_gen_charts_for` vs `gen_charts_from_bundle`) 是独立的、不对等覆盖同一组图。

---

## 5. 根因指向

**根因: (c) 落盘成功，但不在报告路径的 manifest 里——因为报告路径 (`_gen_charts_for`) 不生产 calib_lin。**

证据链：

| # | 事实 | 证据 |
|---|------|------|
| 1 | calib_lin 只在 `gen_charts_from_bundle` 中产生 | `chart_bundle.py:307-322` |
| 2 | `gen_charts_from_bundle` 只被 `gen_charts_from_diagnosis` 调用 | `chart_bundle.py:510` |
| 3 | `gen_charts_from_diagnosis` 只在"保存诊断到项目"路径（`ai_diagnosis.py:1305`）无条件执行；报告路径仅当 `manifest.count == 0` 时作 fallback | `main.py:1299-1301` |
| 4 | 报告路径用 `_generate_charts_from_providers` → `_gen_charts_for`，其中 CalibrationProvider 只产 hyst 回线，不产 calib_lin | `main.py:226-255` |
| 5 | CleaningProvider 永产至少 1 张图（只要数据已加载）→ `manifest.count >= 1` → fallback 永不被触发 | `main.py:175-178` |

**简化**：报告路径的图生成走 provider → CalibrationProvider 不画标定线性度 → 诊断保存路径的 `gen_charts_from_diagnosis` 有这能力，但报告路径只在"完全无图"时才走它 → CleaningProvider 已产图，断死。
