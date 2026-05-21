import math
import re
from typing import Callable

class FormulaEngine:
    def __init__(self):
        self.functions = {
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'log': math.log, 'sqrt': math.sqrt, 'abs': abs,
            'avg': lambda *args: sum(args) / len(args),
            'max': max, 'min': min,
            'std': lambda *args: (sum((x - sum(args)/len(args))**2 for x in args) / len(args)) ** 0.5,
        }
        self.params: dict[str, float] = {}
        self.columns: dict[str, list[float]] = {}

    def set_param(self, name: str, value: float):
        self.params[name] = value

    def set_column(self, name: str, values: list[float]):
        self.columns[name] = values

    def evaluate(self, formula: str) -> list[float]:
        """计算公式，返回结果列表"""
        # 解析公式中的列引用 [ColName] 和参数 ParamName
        col_pattern = r'\[([^\]]+)\]'
        param_pattern = r'([^[\]]+)\s*×\s*([^[\]]+)'

        # 简化：直接用 eval 计算标量，然后用 columns 广播
        # 实际实现需要更复杂的解析
        result = []
        n = len(next(iter(self.columns.values()), []))
        for i in range(n):
            local_vars = {name: col[i] for name, col in self.columns.items()}
            local_vars.update(self.params)
            try:
                val = eval(formula, {"__builtins__": {}}, local_vars)
                result.append(val)
            except:
                result.append(0)
        return result

engine = FormulaEngine()

def calculate(formula: str, columns: dict[str, list[float]], params: dict[str, float]) -> list[float]:
    engine.columns = columns
    engine.params = params
    return engine.evaluate(formula)