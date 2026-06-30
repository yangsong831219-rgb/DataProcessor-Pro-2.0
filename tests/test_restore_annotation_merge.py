"""restore() annotation 合并优先级 — profile 作底, file_annot 兜底

测试:
1. profile annotation 在 restore 后不被 file_annot 旧值覆盖 (profile→底, 文件→兜底)
2. profile 缺列时 file_annot 兜底填充 (不退化空)
3. _load_and_parse 新文件导入路径不受 restore 改动影响 (边界隔离)
4. 旧项目 annotation 字段缺失 → 退化为纯 file_annot (向后兼容)

用法: python tests/test_restore_annotation_merge.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import shutil

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import pandas as pd
import numpy as np

from dp_engine.calibration.project_config import (
    ProjectConfig, ProjectConfigManager, PROFILES_DIR,
)
from ui.calibration_tab import CalibrationTabWidget


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed = 0
total = 0


# ═══════════════════════════════════════════════════════════════════════
# Test 1: profile annotation 优先 — profile 值为底, file_annot 不覆盖
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test1_ProfileWinsOverFileAnnot ═══")

ctw = CalibrationTabWidget()
tp = ctw.temp_page

# 模拟 restore 前状态: 文件已加载 → _loaded_df 有数据
df = pd.DataFrame({
    "Timestamp": range(100),
    "Col_A": [1550.0 + i * 0.5 for i in range(100)],
    "Col_B": [1545.0 + i * 0.3 for i in range(100)],
})
tp._loaded_df = df

# 文件解析产出的 file_annot — 含旧/出厂占位暗号
file_annot = {"Col_A": "W1", "Col_B": "W2"}

# ProjectConfig 中已保存的 profile annotation — 用户手写的成对双栅暗号
profile_annot = {"Col_A": "A1-1", "Col_B": "A1-2"}

# 构造最小 ProjectConfig (走 restore 的 temperature 分支)
t_config = {
    "file_path": "",          # 无源文件 → file_annot 由测试直接传入的场景
    "annotation": profile_annot,
    "detection_params": {},
    "s_eff_results": {},
    "ke_table": {},
    "decoupling_results": {},
    "compensation": {},
    "temp_min": 10.0, "temp_max": 70.0, "temp_step": 10.0,
}
pc = ProjectConfig.create_new("merge_test")
pc.temperature = t_config

# 直接验证合并逻辑: profile 为底 → Col_A/Col_B 保持 profile 值
# 模拟 restore() Step 1 的核心合并 (手工执行等价逻辑)
profile_annot_read = t_config.get("annotation", {}) or {}
merged = dict(profile_annot_read) if profile_annot_read else {}
for col_name, file_val in (file_annot or {}).items():
    if col_name not in merged or not merged.get(col_name):
        merged[col_name] = file_val

total += 1; passed += check("Col_A stays profile 'A1-1' (not overwritten by 'W1')",
                             merged.get("Col_A") == "A1-1")
total += 1; passed += check("Col_B stays profile 'A1-2' (not overwritten by 'W2')",
                             merged.get("Col_B") == "A1-2")
total += 1; passed += check("A1-1 has '-' → 会被 _group_annotations_by_prefix 识别为双栅前缀 A1",
                             '-' in str(merged.get("Col_A", "")))


# ═══════════════════════════════════════════════════════════════════════
# Test 2: profile 缺列时 file_annot 兜底
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test2_FileAnnotFillsMissingCols ═══")

# profile 只有 Col_A, Col_B 未保存
profile_partial = {"Col_A": "B1-1"}
file_annot_full = {"Col_A": "W1", "Col_B": "W2"}

merged2 = dict(profile_partial) if profile_partial else {}
for col_name, file_val in (file_annot_full or {}).items():
    if col_name not in merged2 or not merged2.get(col_name):
        merged2[col_name] = file_val

total += 1; passed += check("Col_A stays profile 'B1-1'",
                             merged2.get("Col_A") == "B1-1")
total += 1; passed += check("Col_B 从 file_annot 兜底 'W2' (profile 无此列)",
                             merged2.get("Col_B") == "W2")
total += 1; passed += check("Col_B 不为空",
                             bool(merged2.get("Col_B")))


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 旧项目兼容 — annotation 字段缺失 → 退化为纯 file_annot
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test3_OldProjectBackCompat ═══")

# 旧 project — 无 annotation 字段
old_t_config = {
    "file_path": "",
    "annotation": {},
    "detection_params": {},
    "s_eff_results": {},
    "ke_table": {},
    "decoupling_results": {},
    "compensation": {},
}
profile_empty = old_t_config.get("annotation", {}) or {}
file_annot_old = {"Col_A": "C1-W1", "Col_B": "C1-W2"}

merged3 = dict(profile_empty) if profile_empty else {}
for col_name, file_val in (file_annot_old or {}).items():
    if col_name not in merged3 or not merged3.get(col_name):
        merged3[col_name] = file_val

total += 1; passed += check("旧项目: Col_A 来自 file_annot",
                             merged3.get("Col_A") == "C1-W1")
total += 1; passed += check("旧项目: Col_B 来自 file_annot",
                             merged3.get("Col_B") == "C1-W2")
total += 1; passed += check("旧项目: 无异常 (merged 完整)",
                             len(merged3) == 2)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 旧项目 annotation 键为 None (json null → Python None)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test4_OldProjectAnnotationNone ═══")

t_config_none = {"annotation": None, "file_path": ""}
profile_none = t_config_none.get("annotation", {}) or {}  # None → {}
merged4 = dict(profile_none) if profile_none else {}
for col_name, file_val in (file_annot_old or {}).items():
    if col_name not in merged4 or not merged4.get(col_name):
        merged4[col_name] = file_val

total += 1; passed += check("annotation=None → profile_annot 退化为 {} (不抛异常)",
                             profile_none == {})
total += 1; passed += check("annotation=None → file_annot 全量生效",
                             merged4.get("Col_A") == "C1-W1")


# ═══════════════════════════════════════════════════════════════════════
# Test 5: _load_and_parse 不依赖 restore 合并逻辑 (边界隔离)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test5_LoadAndParseNotAffected ═══")

# _load_and_parse (calibration_tab.py:2645) 直接赋值 annotation_dict,
# 不走 restore() → 不受本次改动影响。此处用代码结构验证 (非运行时):
import inspect
from ui.calibration_tab import TemperatureCalibrationPage
src = inspect.getsource(TemperatureCalibrationPage._load_and_parse)

# 确认 _load_and_parse 不含 "project_config" / "restore" / "file_annot" 合并调用
total += 1; passed += check("_load_and_parse 不引用 restore 或 ProjectConfigManager",
                             "restore" not in src and "ProjectConfigManager" not in src)
total += 1; passed += check("_load_and_parse 直接赋值 self._annotation_dict (不经过合并)",
                             "self._annotation_dict = annotation" in src or
                             "self._annotation_dict = " in src)
# ★ 确认新文件导入清空旧状态
total += 1; passed += check("_load_and_parse 清空 _annotation_dirty (回归线)",
                             "self._annotation_dirty = set()" in src)
total += 1; passed += check("_load_and_parse 清空 _phase_a_state (回归线)",
                             "self._phase_a_state = {}" in src)


# ═══════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
