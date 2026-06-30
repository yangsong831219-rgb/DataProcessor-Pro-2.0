"""merge_annotations 纯函数单测 + restore 回归 + 对话框往返

测试:
1. merge_annotations: base 优先、fallback 兜底、dirty 锁定、空 base 退化
2. restore 等价 — 加载项目暗号恢复与封板 B 一致
3. 对话框往返 — 手改 → 确定 → 重开 → 手改值不丢
4. 删按钮回归 — accept 流程 _groups 正确重算

用法: python tests/test_merge_annotations.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import shutil

from PyQt6.QtWidgets import QApplication, QTableWidgetItem
_app = QApplication.instance() or QApplication(sys.argv)

import pandas as pd
import numpy as np

from utils.annotation_utils import merge_annotations
from dp_engine.calibration.project_config import (
    ProjectConfig, StrainSubConfig, ProjectConfigManager, PROFILES_DIR,
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
# Test 1: merge_annotations 基础 — base 优先
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test1_BaseWins ═══")

# base has A, fallback has A+B → A from base, B from fallback
r = merge_annotations({"A": "A1-1"}, {"A": "W1", "B": "W2"})
total += 1; passed += check("base A preserved", r.get("A") == "A1-1")
total += 1; passed += check("fallback B filled", r.get("B") == "W2")


# ═══════════════════════════════════════════════════════════════════════
# Test 2: merge_annotations — fallback 兜底
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test2_FallbackFills ═══")

r = merge_annotations({"A": "A1-1"}, {"A": "W1", "B": "W2", "C": "W3"})
total += 1; passed += check("A stays base", r["A"] == "A1-1")
total += 1; passed += check("B filled", r["B"] == "W2")
total += 1; passed += check("C filled", r["C"] == "W3")
total += 1; passed += check("len=3", len(r) == 3)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: dirty 锁定
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test3_DirtyLock ═══")

# dirty → column protected from fallback
r = merge_annotations({"A": "A1-1", "B": "B1-orig"}, {"A": "W1", "B": "W2"}, dirty={"B"})
total += 1; passed += check("dirty B stays orig", r["B"] == "B1-orig")
total += 1; passed += check("non-dirty A stays base", r["A"] == "A1-1")


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 空 base 退化等价旧行为
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test4_EmptyBase ═══")

r = merge_annotations({}, {"A": "C1-W1", "B": "C1-W2"})
total += 1; passed += check("A from fallback", r["A"] == "C1-W1")
total += 1; passed += check("B from fallback", r["B"] == "C1-W2")
total += 1; passed += check("len=2", len(r) == 2)


# ═══════════════════════════════════════════════════════════════════════
# Test 5: base 某列为空字符串 → fallback 填充
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test5_EmptyValueFill ═══")

r = merge_annotations({"A": ""}, {"A": "W1"})
total += 1; passed += check("空值被 fallback 填充", r["A"] == "W1")


# ═══════════════════════════════════════════════════════════════════════
# Test 6: dirty=None (缺省参数)
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test6_DirtyNone ═══")

r = merge_annotations({"A": "A1-1"}, {"A": "W1", "B": "W2"})
total += 1; passed += check("dirty=None 不抛异常", True)
total += 1; passed += check("结果正确", r["A"] == "A1-1" and r["B"] == "W2")


# ═══════════════════════════════════════════════════════════════════════
# Test 7: 对话框往返 — 手改 → 确定 → 重开 → 手改值不丢
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test7_DialogRoundtrip ═══")

# 构造温度页
ctw = CalibrationTabWidget()
tp = ctw.temp_page

df = pd.DataFrame({
    "Timestamp": range(100),
    "Col_A": [1550.0 + i * 0.5 for i in range(100)],
    "Col_B": [1545.0 + i * 0.3 for i in range(100)],
})
tp._loaded_df = df
tp._annotation_dict = {"Col_A": "W1", "Col_B": "W2"}  # 初始占位
tp._annotation_groups = {}
tp._annotation_dirty = set()
tp._phase_a_state = {}
tp._detection_params = {}
tp._time_col_idx = 0

# 第一次打开 PhaseA 对话框 — 模拟用户手改「输入新暗号」列 → 确定
from ui.calibration_tab import PhaseADialog
dlg1 = PhaseADialog(
    tp._loaded_df, tp._annotation_dict, tp._annotation_groups,
    tp._detection_params, tp._time_col_idx, tp,
)

# 验证初始状态: annotation 来自 tp._annotation_dict
total += 1; passed += check("R1: init annotation from tp dict",
                             dlg1._annotation.get("Col_A") == "W1")

# 模拟用户手改: 设置「输入新暗号」列(item at col 2) → 点确定 (accept 调 _on_apply → _write_state_to_main_page)
# 直接设置 self._annotation (= 等价于 _on_apply 读 UI 后的效果)
dlg1._annotation["Col_A"] = "A1-1"
dlg1._annotation["Col_B"] = "A1-2"
dlg1._annotation_dirty.add("Col_A")
dlg1._annotation_dirty.add("Col_B")

# 调用 accept() — 应写回 tp
dlg1.accept()

# 确认写回
total += 1; passed += check("R1: tp annotation after accept A1-1",
                             tp._annotation_dict.get("Col_A") == "A1-1")
total += 1; passed += check("R1: tp annotation after accept A1-2",
                             tp._annotation_dict.get("Col_B") == "A1-2")
total += 1; passed += check("R1: tp dirty after accept",
                             "Col_A" in tp._annotation_dirty)
total += 1; passed += check("R1: tp phase_a_state annotation",
                             tp._phase_a_state.get("annotation", {}).get("Col_A") == "A1-1")

# 重开 PhaseA 对话框 — 验证值还在
dlg2 = PhaseADialog(
    tp._loaded_df, tp._annotation_dict, tp._annotation_groups,
    tp._detection_params, tp._time_col_idx, tp,
)

total += 1; passed += check("R2: reopen A1-1 persists",
                             dlg2._annotation.get("Col_A") == "A1-1")
total += 1; passed += check("R2: reopen A1-2 persists",
                             dlg2._annotation.get("Col_B") == "A1-2")
total += 1; passed += check("R2: dirty restored",
                             "Col_A" in dlg2._annotation_dirty)


# ═══════════════════════════════════════════════════════════════════════
# Test 8: _groups 在 accept 中被正确重算
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test8_GroupsRebuiltInAccept ═══")

ctw3 = CalibrationTabWidget()
tp3 = ctw3.temp_page
tp3._loaded_df = df
tp3._annotation_dict = {"Col_A": "W1", "Col_B": "W2"}
tp3._annotation_groups = {}
tp3._annotation_dirty = set()
tp3._phase_a_state = {}
tp3._detection_params = {}
tp3._time_col_idx = 0

dlg3 = PhaseADialog(
    tp3._loaded_df, tp3._annotation_dict, tp3._annotation_groups,
    tp3._detection_params, tp3._time_col_idx, tp3,
)

# 模拟用户输入成对双栅暗号 + 确定
dlg3._annotation["Col_A"] = "A1-1"
dlg3._annotation["Col_B"] = "A1-2"
dlg3._annotation_dirty.add("Col_A")
dlg3._annotation_dirty.add("Col_B")
dlg3.accept()

# groups 应被重建 + 写回
total += 1; passed += check("G: groups non-empty after accept",
                             len(tp3._annotation_groups) > 0)
a1_gratings = tp3._annotation_groups.get("A1", [])
total += 1; passed += check("G: A1 has 2 gratings (dual)",
                             len(a1_gratings) >= 2)
total += 1; passed += check("G: phase_a_state groups written",
                             len(tp3._phase_a_state.get("groups", {})) > 0)


# ═══════════════════════════════════════════════════════════════════════
# Test 9: restore 调用 merge_annotations — 等价封板 B
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ Test9_RestoreBackCompat ═══")

ctw4 = CalibrationTabWidget()
tp4 = ctw4.temp_page
sp4 = ctw4.strain_page

df2 = pd.DataFrame({
    "Timestamp": range(100),
    "Col_A": [1550.0 + i * 0.5 for i in range(100)],
    "Col_B": [1545.0 + i * 0.3 for i in range(100)],
})
tp4._loaded_df = df2

# 场景: profile 有完整暗号 + file_annot 有旧占位
t_config = {
    "file_path": "",
    "annotation": {"Col_A": "A1-1", "Col_B": "A1-2"},
    "detection_params": {},
    "s_eff_results": {},
    "ke_table": {},
    "decoupling_results": {},
    "compensation": {},
    "temp_min": 10.0, "temp_max": 70.0, "temp_step": 10.0,
}
file_annot_old = {"Col_A": "W1", "Col_B": "W2"}

# 模拟 restore 合并
profile_annot = t_config.get("annotation", {}) or {}
merged = merge_annotations(base=profile_annot, fallback=(file_annot_old or {}))
total += 1; passed += check("RB: profile wins A1-1 over W1", merged["Col_A"] == "A1-1")
total += 1; passed += check("RB: profile wins A1-2 over W2", merged["Col_B"] == "A1-2")

# 旧项目兼容: annotation 为空
profile_empty = {}
merged_old = merge_annotations(base=profile_empty, fallback=(file_annot_old or {}))
total += 1; passed += check("RB: old proj A from file", merged_old["Col_A"] == "W1")
total += 1; passed += check("RB: old proj len=2", len(merged_old) == 2)


# ═══════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {total-passed}")
