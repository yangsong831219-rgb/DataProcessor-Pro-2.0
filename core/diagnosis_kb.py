"""FBG 诊断知识库 — Phase 3: KB 加载 + 安全 trigger 求值 + 条件触发检索 + RAG 组装.

设计:
  KB = YAML 文件 (fbg_diagnosis_kb_v0.2.yaml)
  运行: 对每个传感器/多源对比设备/清洗结果, 用其实际指标对 trigger 求值,
        命中的规则注入 prompt 的【诊断规则(命中)】段.

约束:
  - trigger 求值在白名单命名空间内执行, 禁用 __builtins__
  - null 常量 → 对应规则自动 no-op
  - σ 三档阈值从软件 grade_thresholds 覆盖 YAML fallback
  - 按 severity 排序, 去融合并 (同一规则跨传感器合并为一条)

Usage:
    from core.diagnosis_kb import DiagnosisKB
    kb = DiagnosisKB()
    kb.reload()  # 从 YAML 重载
    kb.load_constants_from_thresholds(grade_thresholds_dict)
    hits = kb.retrieve(sensor_data, multisource_data, cleaning_data)
    rag_text = kb.assemble_rag_prompt(hits)
"""

from __future__ import annotations

import ast
import os
import textwrap
from typing import Any

import yaml


# ═══════════════════════════════════════════════════════════════════════
# 白名单安全求值
# ═══════════════════════════════════════════════════════════════════════


def _safe_median(values: list[float]) -> float:
    """白名单 median — 不使用 numpy/stats.median (无外部依赖)."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return float(s[n // 2 - 1] + s[n // 2]) / 2.0


# 白名单: 仅这些名字对 trigger 表达式可用
_SAFE_BUILTINS: dict[str, Any] = {
    "any": any, "all": all, "abs": abs,
    "max": max, "min": min, "len": len,
    "median": _safe_median, "list": list,
    "True": True, "False": False, "None": None,
}


def evaluate_trigger(expression: str, namespace: dict[str, Any]) -> bool:
    """在严格受限命名空间中求值 trigger 表达式, 返回 bool.

    安全机制:
      - __builtins__ 设为 {}, 完全阻断系统函数
      - 命名空间仅含 白名单函数 + scope 字段 + constants
      - 求值异常 (字段缺失/SyntaxError/etc.) → 返回 False

    Args:
        expression: Python 表达式字符串 (如 'residual_sigma_pct_fs > 5')
        namespace: 求值命名空间 (含 scope 字段, constants 等)

    Returns:
        True 若 trigger 命中有
    """
    if expression.strip() == "always":
        return True

    # 构建严格受限的全局命名空间
    restricted_globals: dict[str, Any] = {"__builtins__": {}}
    restricted_globals.update(_SAFE_BUILTINS)

    # 合并用户命名空间
    local_ns: dict[str, Any] = dict(namespace)

    # 注入 constants 作为属性访问对象 (trigger 中写 constants.X 而不是 X)
    if "constants" not in local_ns:
        local_ns["constants"] = _AttrDict(namespace.get("_constants_raw", {}))

    try:
        # 解析表达式 → AST 合法性检查
        tree = ast.parse(expression.strip(), mode="eval")
        _validate_ast(tree)

        # 求值
        result = eval(compile(tree, "<trigger>", "eval"),
                      restricted_globals, local_ns)
        return bool(result)
    except Exception:
        return False


class _AttrDict:
    """属性访问字典 — 让 trigger 中的 constants.X 能工作."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)


def _validate_ast(node: ast.AST) -> None:
    """遍历 AST, 确保不含危险结构 (import, __开头的属性, exec 等)."""
    for child in ast.walk(node):
        # 禁止 import / exec
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            raise ValueError("import not allowed in trigger expression")
        # 禁止以 __ 开头的属性
        if isinstance(child, ast.Attribute):
            if child.attr.startswith("_"):
                raise ValueError(
                    f"attribute '{child.attr}' not allowed in trigger expression"
                )


# ═══════════════════════════════════════════════════════════════════════
# KB 条目
# ═══════════════════════════════════════════════════════════════════════


class KBRule:
    """单条知识库规则."""

    def __init__(self, raw: dict[str, Any]):
        self.id: str = str(raw.get("id", ""))
        self.domain: str = str(raw.get("domain", ""))
        self.scope: str = str(raw.get("scope", "sensor"))  # sensor|multisource|cleaning|global
        self.trigger: str = str(raw.get("trigger", "always"))
        self.severity: str = str(raw.get("severity", "info"))  # high|medium|low|info
        self.meaning: str = str(raw.get("meaning", "")).strip()
        self.mechanism: str = str(raw.get("mechanism", "")).strip()
        self.recommendation: str = str(raw.get("recommendation", "")).strip()

    def to_summary(self, objects: list[str] | None = None) -> str:
        """生成注入 prompt 的摘要文本.

        Args:
            objects: 适用对象列表 (如 ["B1","B2","C1"]), None 表示全局规则
        """
        lines = [f"### {self.id} [{self.severity}]"]
        if objects:
            lines.append(f"适用: {', '.join(objects)}")
        lines.append(f"要点: {self.meaning}")
        lines.append(f"机理: {self.mechanism}")
        lines.append(f"建议: {self.recommendation}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# KB 主类
# ═══════════════════════════════════════════════════════════════════════


class DiagnosisKB:
    """FBG 诊断知识库.

    单例模式，按需从 YAML 加载规则。
    """

    _instance: DiagnosisKB | None = None

    def __new__(cls) -> "DiagnosisKB":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_loaded"):
            return
        self._loaded = True
        self._yaml_path: str = ""
        self._rules: list[KBRule] = []
        self._constants: dict[str, Any] = {}
        self.reload()

    def reload(self, yaml_path: str | None = None) -> None:
        """从 YAML 重新加载规则和常量.

        Args:
            yaml_path: 可选, KB 文件路径. 默认搜索顺序:
                       1) 环境变量 FBG_KB_PATH
                       2) 项目根目录 / fbg_diagnosis_kb_v0.2.yaml
                       3) 系统 Downloads / fbg_diagnosis_kb_v0.2.yaml
        """
        if yaml_path and os.path.exists(yaml_path):
            self._yaml_path = yaml_path
        elif not self._yaml_path:
            self._yaml_path = self._find_default_path()

        if not os.path.exists(self._yaml_path):
            raise FileNotFoundError(f"知识库文件未找到: {self._yaml_path}")

        with open(self._yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # ── 常量 ──
        self._constants = dict(data.get("constants", {}))

        # ── 规则 ──
        raw_rules = data.get("rules", [])
        self._rules = [KBRule(r) for r in raw_rules if isinstance(r, dict) and r.get("id")]

    def _find_default_path(self) -> str:
        """搜索默认 KB 文件. 复杂度: OS path walk + Downloads."""
        # 1) 环境变量
        env = os.environ.get("FBG_KB_PATH", "")
        if env and os.path.exists(env):
            return env

        # 2) 项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(project_root, "fbg_diagnosis_kb_v0.2.yaml"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        # 3) Downloads
        downloads = os.path.expanduser("~/Downloads")
        cand2 = os.path.join(downloads, "fbg_diagnosis_kb_v0.2.yaml")
        if os.path.exists(cand2):
            return cand2

        # Default fallback (will fail explicitly if not found)
        return candidates[0]

    # ═══════════════════════════════════════════════════════════════════
    # σ 阈值覆盖
    # ═══════════════════════════════════════════════════════════════════

    def load_constants_from_thresholds(self, thresholds: Any) -> None:
        """从软件 GradeThresholds 覆盖 σ 三档阈值.

        grade_thresholds 对象应有属性:
            thr_sigma_excellent_pct_fs, thr_sigma_good_pct_fs, thr_sigma_pass_pct_fs

        Args:
            thresholds: GradeThresholds 实例 或 dict (如 grade_thresholds.to_dict())
        """
        if isinstance(thresholds, dict):
            mapping = {
                "thr_sigma_excellent_pct_fs": thresholds.get("thr_sigma_excellent_pct_fs"),
                "thr_sigma_good_pct_fs": thresholds.get("thr_sigma_good_pct_fs"),
                "thr_sigma_pass_pct_fs": thresholds.get("thr_sigma_pass_pct_fs"),
            }
        else:
            mapping = {
                "thr_sigma_excellent_pct_fs": getattr(thresholds, "thr_sigma_excellent_pct_fs", None),
                "thr_sigma_good_pct_fs": getattr(thresholds, "thr_sigma_good_pct_fs", None),
                "thr_sigma_pass_pct_fs": getattr(thresholds, "thr_sigma_pass_pct_fs", None),
            }

        for key, val in mapping.items():
            if val is not None:
                self._constants[key] = float(val)

    # ═══════════════════════════════════════════════════════════════════
    # 检索
    # ═══════════════════════════════════════════════════════════════════

    def retrieve(
        self,
        sensor_data: dict[str, dict[str, Any]],
        multisource_data: list[dict[str, Any]] | None = None,
        cleaning_data: dict[str, Any] | None = None,
    ) -> list[tuple[KBRule, list[str] | None]]:
        """按 scope 分组求值, 收集所有命中规则 (已去重).

        Args:
            sensor_data: {传感器名: {residual_sigma_pct_fs, hysteresis_max_pct_fs, ..., reasons}}
            multisource_data: [{"corr": 0.97, "mae": 0.05, ...}, ...]
            cleaning_data: {"per_column_count": dict, "fill_method": str, "cleaning_has_run": bool, ...}

        Returns:
            [(KBRule, objects|None), ...]
            objects=None 表示全局规则; 非None 为去重后的适用传感器列表
        """
        hits: dict[str, tuple[KBRule, list[str] | None]] = {}  # rule_id → (rule, objects)

        # ── sensor scope ──
        for s_name, metrics in (sensor_data or {}).items():
            ns = self._build_sensor_ns(metrics)
            for rule in self._rules:
                if rule.scope != "sensor":
                    continue
                if evaluate_trigger(rule.trigger, ns):
                    if rule.id not in hits:
                        hits[rule.id] = (rule, [])
                    objs = hits[rule.id][1]
                    if objs is not None:
                        objs.append(s_name)  # type: ignore[union-attr] protected by None guard

        # ── multisource scope ──
        for i, pair in enumerate(multisource_data or []):
            ns = self._build_multisource_ns(pair)
            for rule in self._rules:
                if rule.scope != "multisource":
                    continue
                if evaluate_trigger(rule.trigger, ns):
                    pair_label = pair.get("device_a", f"pair{i}") + "/" + pair.get("device_b", "")
                    if rule.id not in hits:
                        hits[rule.id] = (rule, [])
                    objs = hits[rule.id][1]
                    if objs is not None:
                        objs.append(pair_label)  # type: ignore[union-attr] protected by None guard

        # ── cleaning scope ──
        if cleaning_data:
            ns = self._build_cleaning_ns(cleaning_data)
            for rule in self._rules:
                if rule.scope != "cleaning":
                    continue
                if evaluate_trigger(rule.trigger, ns):
                    hits[rule.id] = (rule, None)  # cleaning rules are global-level

        # ── global scope ──
        for rule in self._rules:
            if rule.scope != "global":
                continue
            if evaluate_trigger(rule.trigger, {}):
                hits[rule.id] = (rule, None)

        # ── 排序: severity priority ──
        severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        result = sorted(hits.values(), key=lambda x: severity_order.get(x[0].severity, 99))

        return result

    # ═══════════════════════════════════════════════════════════════════
    # 命名空间构建
    # ═══════════════════════════════════════════════════════════════════

    def _build_sensor_ns(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """为 sensor scope 构建求值命名空间."""
        ns: dict[str, Any] = {}
        # 复制指标字段
        for key in ("residual_sigma_pct_fs", "hysteresis_max_pct_fs",
                     "repeatability_pct_fs", "temp_sensitivity_max",
                     "e_std", "e_mean", "e_range", "fs",
                     "comp_form", "low_confidence", "is_single_grating",
                     "passed", "grade", "reasons", "Ke1", "Ke2"):
            ns[key] = metrics.get(key)
        # 常量子对象
        ns["_constants_raw"] = dict(self._constants)
        ns["constants"] = _AttrDict(self._constants)
        return ns

    def _build_multisource_ns(self, pair: dict[str, Any]) -> dict[str, Any]:
        """为 multisource scope 构建求值命名空间."""
        ns: dict[str, Any] = {}
        for key in ("corr", "mae", "rmse", "max_dev", "fs"):
            ns[key] = pair.get(key)
        ns["_constants_raw"] = dict(self._constants)
        ns["constants"] = _AttrDict(self._constants)
        return ns

    def _build_cleaning_ns(self, data: dict[str, Any]) -> dict[str, Any]:
        """为 cleaning scope 构建求值命名空间."""
        ns: dict[str, Any] = {
            "per_column_count": data.get("per_column_count", {}),
            "fill_method": data.get("fill_method"),
            "cleaning_has_run": data.get("cleaning_has_run", False),
        }
        ns["_constants_raw"] = dict(self._constants)
        ns["constants"] = _AttrDict(self._constants)
        return ns

    # ═══════════════════════════════════════════════════════════════════
    # RAG prompt 组装
    # ═══════════════════════════════════════════════════════════════════

    PROCEDURAL_INSTRUCTION = textwrap.dedent("""\
    === 诊断规则使用说明 ===
    以上【诊断规则(命中)】段列出了根据当前数据指标触发的知识库规则。
    每条规则包含: 要点(判据) / 机理(为什么) / 建议(怎么办)。
    你的诊断必须:
    1. 以【数据源摘要】中的实际数值为事实基础，引用规则给出的判据做解释；
    2. 对照每个命中规则说明其适用对象当前状态；
    3. 按规则建议给出可操作的建议，不要越过规则自由发挥；
    4. 数值一律以摘要为准，规则仅提供机理/判据/建议框架。
    """)

    def assemble_rag_prompt(self, hits: list[tuple[KBRule, list[str] | None]],
                            budget_chars: int = 3000) -> str:
        """将命中规则组装为 prompt 段。

        Args:
            hits: retrieve() 返回的命中列表
            budget_chars: 输出上限

        Returns:
            RAG prompt 文本段
        """
        if not hits:
            return "【诊断规则(命中)】无规则触发 (所有指标正常)"

        lines = ["【诊断规则(命中)】\n"]
        lines.append(self.PROCEDURAL_INSTRUCTION)
        lines.append("")

        char_count = sum(len(l) + 1 for l in lines)
        shown = 0
        skipped_count = 0

        for rule, objects in hits:
            block = rule.to_summary(
                objects=None if objects is None
                else (objects if isinstance(objects, list) and len(objects) < 10
                      else objects[:10] + [f"... (共 {len(objects)} 项)"])
            )
            block += "\n"
            if char_count + len(block) > budget_chars:
                skipped_count += 1
                continue
            lines.append(block)
            char_count += len(block)
            shown += 1

        if skipped_count:
            lines.append(f"\n(另有 {skipped_count} 条低优先级命中未列，可调大 budget 查看)")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# 全局便捷函数
# ═══════════════════════════════════════════════════════════════════════


def get_kb() -> DiagnosisKB:
    """获取全局单例."""
    return DiagnosisKB()
