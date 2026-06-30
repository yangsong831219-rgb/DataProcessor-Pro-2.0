"""公式引擎 — asteval 安全求值器

从 v2.1 起，公式求值统一使用 asteval.Interpreter（minimal 模式），
消除旧引擎 eval + 正则替换的常量误匹配漏洞（k1/k10 冲突）。

核心改进：
  - 向量化求值：整列一次注入 asteval symtable，一次求值得全列结果
  - 安全沙箱：minimal=True 禁用内置函数访问
  - 零正则替换：参数和列均作为 symtable 变量注入，不再做字符串替换

API 保持不变，对调用方完全透明：
  FormulaEngine.set_param() / set_column() / evaluate()
  calculate(formula, columns, params, global_params)
"""

from __future__ import annotations

import re as _re
import math
from typing import Any, Callable, Optional

import numpy as np
from asteval import Interpreter


def _nanmean(arr):
    """NaN-safe mean for vectorized asteval calls."""
    return np.nanmean(np.asarray(arr, dtype=np.float64))


def _nanstd(arr):
    """NaN-safe standard deviation for vectorized asteval calls."""
    return np.nanstd(np.asarray(arr, dtype=np.float64))


def _nanmax(arr):
    return np.nanmax(np.asarray(arr, dtype=np.float64))


def _nanmin(arr):
    return np.nanmin(np.asarray(arr, dtype=np.float64))


class FormulaEngine:
    """基于 asteval 的安全公式求值引擎（向量化）"""

    def __init__(self) -> None:
        self.params: dict[str, float] = {}
        self.columns: dict[str, np.ndarray] = {}
        self._interp = self._build_interpreter()

    def _build_interpreter(self) -> Interpreter:
        """构建 minimal 模式的 asteval 解释器，注入安全数学函数。"""
        interp = Interpreter(minimal=True)

        # 向量化数学函数 — NumPy 实现，直接支持数组广播
        interp.symtable['sin'] = np.sin
        interp.symtable['cos'] = np.cos
        interp.symtable['tan'] = np.tan
        interp.symtable['log'] = np.log
        interp.symtable['sqrt'] = np.sqrt
        interp.symtable['abs'] = np.abs
        interp.symtable['max'] = _nanmax
        interp.symtable['min'] = _nanmin

        # 聚合函数 — NaN-safe 实现
        interp.symtable['avg'] = _nanmean
        interp.symtable['std'] = _nanstd

        # 数学常量
        interp.symtable['pi'] = math.pi
        interp.symtable['e'] = math.e

        return interp

    def _rebuild(self) -> None:
        """重建解释器（参数/列变更后调用以保持状态同步）。

        asteval 的 symtable 是 mutable dict，可直接增删，因此无需频繁
        重建。仅当需要"清空全局副作用"时调用。
        """
        self._interp = self._build_interpreter()
        # 恢复参数
        for name, value in self.params.items():
            self._interp.symtable[name] = value
        # 恢复列
        for name, arr in self.columns.items():
            self._interp.symtable[name] = arr

    # ── 公开 API ──

    def set_param(self, name: str, value: float) -> None:
        """设置公式参数（常量）

        Args:
            name: 参数名，如 "k1"
            value: 参数值
        """
        self.params[name] = float(value)
        self._interp.symtable[name] = float(value)

    def set_column(self, name: str, values: list[float] | np.ndarray) -> None:
        """设置公式列变量

        Args:
            name: 列名，如 "W1"
            values: 列数据，将转换为 float64 数组
        """
        arr = np.asarray(values, dtype=np.float64)
        self.columns[name] = arr
        self._interp.symtable[name] = arr

    def evaluate(self, formula: str) -> list[Optional[float]]:
        """计算公式，返回与列等长的结果列表。

        Args:
            formula: 数学表达式，如 "W1 * k1"、"avg(W1) - std(W2)"

        Returns:
            结果列表，NaN → None，保持与旧引擎一致的 API 契约。
        """
        if not formula or not formula.strip():
            n = self._column_length()
            return [None] * n if n > 0 else []

        # 预处理：注入公式中引用的未定义常量（默认 1.0，与旧引擎兼容）
        self._ensure_constants_in_symtable(formula)

        try:
            result = self._interp(formula)
        except Exception as exc:
            print(f"[FormulaEngine] 求值失败: {formula!r} → {exc}")
            import traceback
            traceback.print_exc()
            n = self._column_length()
            return [None] * n if n > 0 else []

        return self._post_process(result)

    # ── 内部方法 ──

    def _column_length(self) -> int:
        """获取列数据长度"""
        if self.columns:
            return len(next(iter(self.columns.values())))
        return 0

    def _ensure_constants_in_symtable(self, formula: str) -> None:
        """扫描公式中引用的 k1/k10 等常量，未定义则默认注入 1.0。

        这是旧引擎的兼容行为：公式中写了"k1"但用户没在 constants 中定义，
        旧引擎会把未解析的 k1 替换为 (1.0)。我们保留此行为但不用正则替换。
        """
        # 匹配独立的标识符（含下划线数字），过滤掉已知函数名
        known_functions = {
            'sin', 'cos', 'tan', 'log', 'sqrt', 'abs',
            'max', 'min', 'avg', 'std', 'pi', 'e',
        }
        tokens = set(_re.findall(r'\b([a-zA-Z_]\w*)\b', formula))
        for token in tokens:
            if token in known_functions:
                continue
            if token in self._interp.symtable:
                continue
            if token in self.params:
                continue
            if token in self.columns:
                continue
            # 可能是未定义的常量 → 默认 1.0
            self._interp.symtable[token] = 1.0
            print(f"[FormulaEngine] 未定义常量 {token!r} 默认按 1.0 计算。")

    def _post_process(
        self, result: Any,
    ) -> list[Optional[float]]:
        """将 asteval 输出转换为标准结果列表。

        - 标量 → 广播为等长列表
        - 数组 → NaN → None
        - None / 无列 → 空列表
        """
        n = self._column_length()
        if n == 0:
            return []

        if result is None:
            return [None] * n

        # 标量结果（如 avg/std 返回值）
        if isinstance(result, (int, float, np.floating)):
            val = float(result)
            if math.isnan(val) or math.isinf(val):
                return [None] * n
            return [val] * n

        # numpy 数组或列表
        try:
            arr = np.asarray(result, dtype=np.float64).ravel()
        except (ValueError, TypeError):
            print(f"[FormulaEngine] 无法转换结果类型: {type(result)}")
            return [None] * n

        # NaN → None (保持与旧引擎一致)
        return [
            None if np.isnan(v) else float(v)
            for v in arr
        ]


# ═══════════════════════════════════════════════════════════════════════
# 模块级便捷函数（向后兼容）
# ═══════════════════════════════════════════════════════════════════════

_engine = FormulaEngine()


def calculate(
    formula: str,
    columns: dict[str, list[float] | np.ndarray],
    params: dict[str, float | dict],
    global_params: dict[str, float | dict] | None = None,
) -> list[Optional[float]]:
    """计算公式（便捷函数，向后兼容旧 API）

    Args:
        formula: 数学表达式
        columns: {列名: 列数据列表}
        params: {参数名: 参数值}  — 可以为 {'value': ...} 格式的 dict
        global_params: 全局参数，打底后用局部 params 覆盖

    Returns:
        结果列表

    注意：每次调用重建独立的 FormulaEngine 实例，防止列数据跨调用泄漏。
    """
    # 每次调用重建引擎，避免状态污染
    engine = FormulaEngine()

    # SSOT: 全局参数打底，局部常量覆盖
    merged: dict[str, Any] = {}
    if global_params:
        merged.update(global_params)
    merged.update(params or {})

    # 归一化：dict 格式 {value, unit, description} → 浮点数
    normalized: dict[str, float] = {}
    for k, v in merged.items():
        if isinstance(v, dict):
            normalized[k] = float(v.get('value', 0))
        else:
            normalized[k] = float(v)

    # 设置列
    for name, values in columns.items():
        engine.set_column(name, values)

    # 设置参数
    for name, value in normalized.items():
        engine.set_param(name, value)

    return engine.evaluate(formula)
