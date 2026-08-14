from __future__ import annotations

import ast
import re
from typing import Any

SAFE_SQL_PATTERN = re.compile(
    r"^\s*(WITH|SELECT)\b.*$",
    flags=re.IGNORECASE | re.DOTALL,
)

FORBIDDEN_SQL_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "attach",
    "detach",
    "pragma",
    "replace",
    "vacuum",
    "reindex",
}


def validate_sql(sql: str) -> bool:
    if not sql or not SAFE_SQL_PATTERN.match(sql):
        return False
    lower = sql.lower()
    return not any(keyword in lower for keyword in FORBIDDEN_SQL_KEYWORDS)


_SAFE_NODES = (
    ast.Expression,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
)

_SAFE_NAMES = {"True", "False", "None"}
_SAFE_FUNCTIONS = {
    "len",
    "min",
    "max",
    "sum",
    "abs",
    "round",
    "str",
    "int",
    "float",
    "bool",
}


def safe_eval(expression: str, context: dict[str, Any] | None = None) -> Any:
    tree = ast.parse(expression, mode="eval")
    names = {name: True for name in _SAFE_NAMES}
    return _eval_node(tree.body, {**names, **(context or {})})


def safe_eval_filtered(
    expression: str,
    context: dict[str, Any] | None = None,
    *,
    max_depth: int = 3,
    max_items: int = 100,
    max_str_length: int = 1000,
) -> Any:
    value = safe_eval(expression, context)
    return _filter_output(value, max_depth, max_items, max_str_length)


def _filter_output(value: Any, max_depth: int, max_items: int, max_str_length: int) -> Any:
    if max_depth <= 0:
        raise ValueError("AST 求值结果超过允许的递归深度")
    if isinstance(value, str):
        return value[:max_str_length]
    if isinstance(value, (list, tuple, set)):
        values = list(value)[:max_items]
        return [_filter_output(item, max_depth - 1, max_items, max_str_length) for item in values]
    if isinstance(value, dict):
        return {
            key: _filter_output(item, max_depth - 1, max_items, max_str_length)
            for key, item in list(value.items())[:max_items]
        }
    return value


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise ValueError(f"不允许访问名称：{node.id}")
        return context[node.id]
    if isinstance(node, ast.List):
        return [_eval_node(item, context) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(item, context) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(item, context) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {_eval_node(key, context): _eval_node(value, context) for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, context)
        right = _eval_node(node.right, context)
        op = _binary_op(node.op)
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, context)
        op = _unary_op(node.op)
        return op(operand)
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, context) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            result = _compare_op(op_node)(left, right)
            if not result:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, context):
            return _eval_node(node.body, context)
        return _eval_node(node.orelse, context)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCTIONS:
            raise ValueError(f"不允许调用函数：{ast.dump(node.func)}")
        args = [_eval_node(arg, context) for arg in node.args]
        import builtins
        return getattr(builtins, node.func.id)(*args)
    raise ValueError(f"不支持的 AST 节点：{type(node).__name__}")


def _binary_op(node: ast.operator):
    operations = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a**b,
    }
    return operations[type(node)]


def _unary_op(node: ast.unaryop):
    operations = {
        ast.UAdd: lambda value: +value,
        ast.USub: lambda value: -value,
        ast.Not: lambda value: not value,
    }
    return operations[type(node)]


def _compare_op(node: ast.cmpop):
    operations = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }
    return operations[type(node)]
