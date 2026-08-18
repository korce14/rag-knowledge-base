from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .code_security import SANDBOX_EXCLUDE_DIRS, is_sensitive_path, safe_command_allowed


@dataclass
class Sandbox:
    root: Path
    source_dir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _ignore_patterns(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    base = Path(directory)
    for name in names:
        candidate = base / name
        if name in SANDBOX_EXCLUDE_DIRS:
            ignored.add(name)
        elif is_sensitive_path(candidate.relative_to(base.parent) if candidate.is_absolute() else candidate):
            ignored.add(name)
    return ignored


def create_sandbox(source_dir: Path, root: Path | None = None) -> Sandbox:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError("项目目录不存在")
    sandbox_root = root or Path(tempfile.mkdtemp(prefix="rag-sandbox-"))
    target = sandbox_root / source_dir.name
    shutil.copytree(source_dir, target, ignore=_ignore_patterns, dirs_exist_ok=True)
    return Sandbox(root=target, source_dir=source_dir)


def run_command(sandbox: Sandbox, command: list[str], timeout: int = 60) -> dict[str, Any]:
    if not safe_command_allowed(command):
        return {
            "exit_code": 126,
            "stdout": "",
            "stderr": "命令被安全策略拦截：仅允许 python/pytest/npm/node/pip 白名单命令",
        }
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(sandbox.root)
        result = subprocess.run(
            command,
            cwd=str(sandbox.root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": "命令执行超时"}
    except FileNotFoundError:
        return {"exit_code": 127, "stdout": "", "stderr": "命令不存在"}
