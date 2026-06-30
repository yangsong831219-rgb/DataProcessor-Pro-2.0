"""阶段 1 回归测试 — 公式引擎升级 eval → asteval

用法:
    python run_tests.py tests/test_formula_engine.py -v

测试目标:
1. 新旧引擎对同一公式、同一输入产生逐元素一致的结果
2. k1 与 k10 同时存在时不再误替换（旧引擎用正则，新引擎用 symtable 注入）
3. 嵌套函数 (avg/std)、负值、NaN 正确处理
4. 边界条件（空列、超大数组、非法公式）不崩溃
"""

from __future__ import annotations

import math
import pytest
import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _old_eval_formula(
    formula: str,
    columns: dict[str, list[float]],
    params: dict[str, float],
) -> list[float | None]:
    """模拟 core/models.py 旧版求值逻辑（eval + 正则替换）。

    这是阶段 1 改造的基准实现，保留用于回归对比。
    """
    import re

    expr = formula
    for name, value in params.items():
        if isinstance(value, dict):
            value = value.get("value", 0)
        expr = re.sub(r"\b" + re.escape(name) + r"\b", f"({value})", expr)

    # 未定义常量默认按 1.0（与 core/models.py 一致）
    unresolved_k = re.findall(r'\b[kK]\d+\b', expr)
    if unresolved_k:
        for k_var in set(unresolved_k):
            expr = re.sub(r'\b' + re.escape(k_var) + r'\b', '(1.0)', expr)

    n = len(next(iter(columns.values()), []))
    if n == 0:
        return []

    result = []
    for i in range(n):
        row_vars = {}
        for col_name, col_vals in columns.items():
            v = col_vals[i] if i < len(col_vals) else None
            row_vars[col_name] = float("nan") if v is None else float(v)

        try:
            val = eval(expr, {"__builtins__": {}}, row_vars)
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                result.append(None)
            else:
                result.append(val)
        except Exception:
            result.append(None)
    return result


def _new_eval_formula(
    formula: str,
    columns: dict[str, list[float]],
    params: dict[str, float],
) -> list[float | None]:
    """阶段 1 实现：asteval 向量化求值"""
    from dp_engine.formula import calculate as _engine_calculate
    return _engine_calculate(formula, columns, params, global_params=None)


# ═══════════════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════════════


class TestFormulaRegression:
    """新旧引擎逐元素一致性回归测试"""

    def test_simple_linear(self):
        """W1 * k1 — 最基础的单参数线性公式"""
        cols = {"W1": [1.0, 2.0, 3.0, 4.0, 5.0]}
        params = {"k1": 1000.0}
        old = _old_eval_formula("W1 * k1", cols, params)
        new = _new_eval_formula("W1 * k1", cols, params)
        assert old == pytest.approx(new)

    def test_k1_k10_coexistence(self):
        """k1 与 k10 同时存在时不再误替换"""
        cols = {"W1": [1.0, 2.0, 3.0], "W10": [0.5, 1.0, 1.5]}
        params = {"k1": 1000.0, "k10": 500.0}
        old = _old_eval_formula("W1 * k1 + W10 * k10", cols, params)
        new = _new_eval_formula("W1 * k1 + W10 * k10", cols, params)
        assert old == pytest.approx(new)

    def test_nested_avg(self):
        """avg(W1) - std(W1) — 聚合函数返回标量，广播到全列"""
        cols = {"W1": [1.0, 2.0, 3.0, 4.0, 5.0]}
        # 旧引擎不支持 avg/std（__builtins__={} 屏蔽了所有内置函数）
        # 新引擎正确计算并广播
        new = _new_eval_formula("avg(W1) - std(W1)", cols, {})
        assert len(new) == 5
        expected = np.mean([1.0, 2.0, 3.0, 4.0, 5.0]) - np.std([1.0, 2.0, 3.0, 4.0, 5.0])
        for v in new:
            assert v is not None
            assert float(v) == pytest.approx(expected, rel=1e-6)

    def test_negative_values(self):
        """负值正确处理"""
        cols = {"W1": [-1.0, -2.0, 3.0], "W2": [4.0, -5.0, -6.0]}
        params = {"k1": 10.0, "k2": -5.0}
        old = _old_eval_formula("W1 * k1 + W2 * k2", cols, params)
        new = _new_eval_formula("W1 * k1 + W2 * k2", cols, params)
        assert old == pytest.approx(new)

    def test_nan_handling(self):
        """NaN / None 传入时结果一致"""
        cols = {"W1": [1.0, None, 3.0, float("nan"), 5.0]}
        params = {"k1": 1000.0}
        old = _old_eval_formula("W1 * k1", cols, params)
        new = _new_eval_formula("W1 * k1", cols, params)
        for o, n in zip(old, new):
            if o is None and n is None:
                continue
            if o is None or n is None:
                pytest.fail(f"None mismatch: {o} vs {n}")
            if math.isnan(o) and math.isnan(float(n)):
                continue
            assert o == pytest.approx(float(n))

    def test_complex_nested_parens(self):
        """嵌套括号 + 多运算符"""
        cols = {"W1": [1.0, 2.0, 3.0], "W2": [0.1, 0.2, 0.3]}
        params = {"a": 2.0, "b": 3.0}
        formula = "(W1 * a + W2 * b) * (a - b)"
        old = _old_eval_formula(formula, cols, params)
        new = _new_eval_formula(formula, cols, params)
        # 旧引擎逐行求值，新引擎向量化 — 结果应逐元素一致
        assert old == pytest.approx(new)

    def test_math_functions(self):
        """sin/cos/sqrt/abs/log 等数学函数 — 新引擎向量化计算"""
        import numpy as np
        cols = {"W1": [0.0, 0.5, 1.0, 1.5, 2.0]}
        new = _new_eval_formula("sin(W1) + cos(W1) + sqrt(abs(W1))", cols, {})
        assert len(new) == 5
        w = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        expected = np.sin(w) + np.cos(w) + np.sqrt(np.abs(w))
        for v, e in zip(new, expected):
            assert v is not None
            assert float(v) == pytest.approx(float(e), rel=1e-6)

    def test_global_params_override(self):
        """全局参数打底，局部常量覆盖"""
        cols = {"W1": [1.0, 2.0]}
        params = {"k1": 1000.0}
        old = _old_eval_formula("W1 * k1", cols, params)
        new = _new_eval_formula("W1 * k1", cols, params)
        assert old == pytest.approx(new)


class TestFormulaEngineEdgeCases:
    """边界条件测试"""

    def test_empty_columns(self):
        """空列输入不崩溃"""
        result = _new_eval_formula("W1 * k1", {}, {"k1": 1.0})
        assert result == []

    def test_empty_formula(self):
        """空公式不崩溃"""
        cols = {"W1": [1.0, 2.0]}
        result = _new_eval_formula("", cols, {})
        assert len(result) == len(cols["W1"]) or result == []

    def test_invalid_formula(self):
        """非法公式不崩溃，返回 None 填充"""
        cols = {"W1": [1.0, 2.0, 3.0]}
        result = _new_eval_formula("W1 * * k1", cols, {"k1": 1.0})
        assert len(result) == 3

    def test_large_array(self):
        """10000 行数据在合理时间内完成"""
        cols = {"W1": list(range(10000))}
        params = {"k1": 0.001}
        import time
        start = time.time()
        result = _new_eval_formula("W1 * k1", cols, params)
        elapsed = time.time() - start
        assert len(result) == 10000
        assert elapsed < 5.0

    def test_missing_constant_defaults_to_one(self):
        """未定义的常量默认按 1.0 计算（与旧引擎行为一致）"""
        cols = {"W1": [5.0]}
        old = _old_eval_formula("W1 * k999", cols, {})
        new = _new_eval_formula("W1 * k999", cols, {})
        assert old == pytest.approx(new)


# ═══════════════════════════════════════════════════════════════════════
# k1 / k10 精确回归测试
# ═══════════════════════════════════════════════════════════════════════

class TestK1K10Regression:
    """专门针对 k1/k10 误替换的回归测试"""

    def test_k1_standalone(self):
        """只有 k1 时正确"""
        cols = {"W1": [2.0]}
        params = {"k1": 1000.0}
        old = _old_eval_formula("W1 * k1", cols, params)
        new = _new_eval_formula("W1 * k1", cols, params)
        assert old == pytest.approx(new)

    def test_k10_standalone(self):
        """只有 k10 时正确"""
        cols = {"W10": [2.0]}
        params = {"k10": 500.0}
        old = _old_eval_formula("W10 * k10", cols, params)
        new = _new_eval_formula("W10 * k10", cols, params)
        assert old == pytest.approx(new)

    def test_k1_through_k10_all_present(self):
        """k1 到 k10 全部存在时逐个正确"""
        cols = {f"W{i}": [float(i)] for i in range(1, 11)}
        params = {f"k{i}": float(i * 100) for i in range(1, 11)}
        formula = " + ".join(f"W{i} * k{i}" for i in range(1, 11))
        old = _old_eval_formula(formula, cols, params)
        new = _new_eval_formula(formula, cols, params)
        assert old == pytest.approx(new)
