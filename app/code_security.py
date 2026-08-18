from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path
from typing import Any

SENSITIVE_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*credential*",
    "*secret*",
    "server*.log",
    "*.log",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
]

SANDBOX_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "data",
    ".pytest_cache",
    ".agents",
    ".codex",
}

DANGEROUS_PYTHON_CALLS = {
    "subprocess",
    "eval",
    "exec",
    "compile",
    "__import__",
    "pickle.loads",
    "marshal.loads",
    "ctypes",
    "os.system",
    "os.popen",
    "shutil.rmtree",
    "socket",
    "pty",
}


def is_sensitive_path(path: Path | str) -> bool:
    relative = Path(path)
    if relative.is_absolute():
        relative = Path(*relative.parts[1:])
    parts = [part.lower() for part in relative.parts]
    if any(part in {"env", "data"} for part in parts):
        return True
    name = relative.name.lower()
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in SENSITIVE_FILE_PATTERNS)


def detect_dangerous_code(content: str) -> list[str]:
    findings: list[str] = []
    try:
        tree = ast.parse(content, mode="exec")
    except SyntaxError:
        return ["无法解析的代码文件，禁止写入"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            dotted = _dotted_name(node.func)
            if dotted and dotted in DANGEROUS_PYTHON_CALLS:
                findings.append(f"禁止调用 {dotted}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "compile", "__import__"}:
                findings.append(f"禁止调用 {node.func.id}")
        elif isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in {"subprocess", "pickle", "marshal", "ctypes", "socket", "pty"}:
                    findings.append(f"禁止导入 {root}")
    return findings


def validate_code_content(content: str) -> tuple[bool, list[str]]:
    findings = detect_dangerous_code(content)
    return (not findings, findings)


def safe_command_allowed(command: list[str]) -> bool:
    if not command:
        return False
    head = command[0].lower()
    allowed_heads = {"python", "python3", "pytest", "npm", "node", "pip"}
    if head not in allowed_heads:
        return False
    if head == "pip":
        return len(command) >= 2 and command[1].lower() in {"install", "download"}
    if head == "npm":
        return len(command) >= 2 and command[1].lower() in {"test", "run", "install"}
    return True


def _dotted_name(node: ast.Attribute) -> str | None:
    parts: list[str] = []
    current: Any = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts)) if parts else None
