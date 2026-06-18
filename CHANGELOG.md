# 变更记录 (CHANGELOG)

> DataProcessor Pro 2.0 · 分支 `llama-cpp`
> 本条目汇总一轮迭代：ENLIGHT 解析器修复 → 解析后校验层 → 应变标定修复 → main.py 拆分。

## [未发布] — 2026-06-18

### 修复 (Fixed)

- **ENLIGHT Peaks 时间戳列错位**
  Peaks 格式数据行 = `时间戳 + 16 个峰值数量(# CH n) + sum(数量) 个波长`；`# CH n` 是**计数**不是波长。
  旧解析按表头宽读取导致 pandas 右对齐错位（时间戳被挤出、波长落进 `# CH` 列）。
  新增 `parse_hyperion_peaks()` 显式按字段解析、按通道槽位对齐（缺峰填 NaN），波长列命名 `w1…wN`。

- **Peaks 表头锚点误匹配**
  元数据行 `Timestamp Format: Full` 被宽松前缀匹配当成数据表头，导致后续元数据行进入 `int()` 解析崩溃
  （`invalid literal for int(): ''`）。改为严格 `Timestamp\t# CH 1` 匹配（`_find_peaks_header`），
  新增 `_is_peaks_data_row` 防御式行校验，并在 0 有效行时硬报错而非静默成功。

- **ENLIGHT Sensors（公式版）解析**
  修正三处：① 未剥 UTF-8 BOM（表头首格变 `\ufeffTimestamp`）；② 文件内嵌暗号行被当数据、污染数值列为 object；
  ③ 暗号混用直引号(`'`)与中文弯引号(`'`)漏剥。改为 `utf-8-sig` 去 BOM、识别并抽取暗号行、统一引号集合剥离。

- **公式版原始导出（带元数据块）解析失败**
  真实导出有 130 行元数据块、无 BOM、无暗号行，真实表头在第 130 行；旧逻辑假设表头在行 0，
  把 FBG 定义行当成表头（只注册 1 个假 FBG、5 行假数据）。改为**全文件扫描 `Timestamp\t` 真实表头并跳过元数据块**，
  放宽 dispatch 门控（全文件找到 Timestamp 表头即进 Sensors），FBG 从正确表头注册（12 个 `FBG_A1…FBG_J2`）。

- **应变标定：传感器名永远为 C1、已标定列表只能存一个**
  名字取自读数录入表头的**循环号**（`G1_C1` 的 `C1`）而非下拉框暗号。根因为 `_get_grating_sources` 返回
  **陈旧缓存**（新建标定后不刷新），而 `_extract_table_data` 实时读控件，两源发散。
  改为名字从下拉框暗号前缀解析（`C2-1/C2-2 → C2`）、名字与图例共用同一实时源、新建标定时失效缓存。

- **Δλ-ε 标定图例**
  写死的 `G1/G2` 改为映射下拉框暗号名（`C2-1/C2-2`）。

- **插入备注暗号行错位/越界**
  旧逻辑按模板固定列索引贴标签（与实际 25 列不符，标签全错位）。改为按真实 `df.columns` 逐列生成：
  仅标注 `Timestamp`(→`时间戳`) 与波长列（值 ∈ 1400~1700nm，即"15 开头"，→`wN-类型-位置`），其余列留空。

### 新增 (Added)

- **带暗号行的矩形 Peaks 文件支持**
  `_parse_peaks_rectangular` + 格式判别（首条数据行 `f[1:17]` 全整数 → 计数列；否则矩形），
  覆盖"保存数据"导出的矩形文件（真实时间戳 13 列 / Timestamp=0 17 列两种形态）。

- **解析后校验层** `utils/parse_validation.py`
  `ParseValidationError` + `validate_parsed_data`，strict(ENLIGHT)/lenient(通用)双档。
  拦截：空表、元数据行被当表头（列名含 `(FBG):`/`Range (`/`Configuration` 等）、波长列值越界（列错位/时间戳=0）、
  暗号或元数据污染数值列。接入 `parse_enlight_file`(strict) 与 `parse_file`(lenient)。新增 27 个测试。
  **效果：彻底杜绝"静默加载 0 行/假数据"——不达标即带诊断报错。**

- **暗号三件套单元测试**
  `is_wave_col` / `build_annotation_row` / `apply_annotation_row` 锁定值域判定（1400~1700nm）与逐列对齐行为。

### 重构 (Refactored)

- **main.py 瘦身 3240 → 2294 行（-29%）**
  - Phase 1（纯逻辑提取）：16 个纯函数 → `utils/annotation_utils.py`、`column_utils.py`、`dataframe_utils.py`、
    `file_parser.py`(扩展)；main.py 保留 16 个薄 delegation 壳（3–5 行/个，签名不变）。
  - Phase 3（QDialog 搬家）：`FBGEditDialog`、`SensorEditDialog`、`AIModelConfigDialog`、`ReportWorker` → `ui/`。
  - "卸妆"正则三处重复合并为 `strip_bracket_units`。
  - Phase 2（Mixin 多继承）**评估后缓做**：收益主要是文件组织，成本是多继承 + Protocol 维护，
    在 2294 行量级不值当；`ConfigMixin` 若回头做应改走 `AppState` 序列化，避免 god-mixin。

### 质量门 (Quality Gates)

- pyright：0 errors（无裸 `# type: ignore`）。
- 测试：174 passed / 10 skipped / 0 failed；行为零变更。
- 已推送 `llama-cpp` 分支。

### 技术债 / 后续 (Backlog)

- **更新 CLAUDE.md**：过时的"main.py ~8800 行"改为 ~2294；拓扑补充新模块（`annotation_utils`/`column_utils`/
  `dataframe_utils`/`parse_validation` + `ui/` 新 Dialog）；记录 Phase 1+3 已完成、Phase 2 缓做。
- 解析层可进一步走**模板驱动的确定性解析**，替代现有启发式判别（`csv.Sniffer`/数值占比表头检测）。
- 性能：表头定位后数据块改用 pandas C 解析器（`read_csv(skiprows=…)`）提速 4.5 万行级文件；时间戳解析为 `datetime`。
- 公式冗余确认：仪器已算好的物理量列（`A1_1…C2_2`）与软件自带 asteval 公式引擎是否重复计算、口径是否一致。
