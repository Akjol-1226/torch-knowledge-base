from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from decimal import Decimal, DecimalException, DivisionByZero, InvalidOperation


@dataclass(frozen=True)
class CalculationResult:
    ok: bool
    result: str = ""
    error: str = ""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPR_LEN = 200
_MAX_AST_NODES = 40
_MAX_ABS_VALUE = Decimal("1e12")
_MAX_POWER_EXPONENT = Decimal("12")


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _eval(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER_EXPONENT:
            raise ValueError("指数过大")
        if isinstance(node.op, ast.Div) and right == 0:
            raise ZeroDivisionError
        value = _BIN_OPS[type(node.op)](left, right)
        if abs(value) > _MAX_ABS_VALUE:
            raise ValueError("结果超出允许范围")
        return value
    raise ValueError("不支持的表达式")


def calculate(expression: str) -> CalculationResult:
    expr = (expression or "").strip()
    if not expr:
        return CalculationResult(ok=False, error="表达式不能为空")
    if len(expr) > _MAX_EXPR_LEN:
        return CalculationResult(ok=False, error="表达式过长")
    try:
        tree = ast.parse(expr, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
            return CalculationResult(ok=False, error="表达式过于复杂")
        value = _eval(tree)
        return CalculationResult(ok=True, result=_format_decimal(value))
    except ZeroDivisionError:
        return CalculationResult(ok=False, error="除零错误")
    except (DecimalException, DivisionByZero, InvalidOperation):
        return CalculationResult(ok=False, error="数值计算失败")
    except (SyntaxError, ValueError) as exc:
        return CalculationResult(ok=False, error=str(exc) or "不支持的表达式")
