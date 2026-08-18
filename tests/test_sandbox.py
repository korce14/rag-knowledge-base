from __future__ import annotations

from pathlib import Path

from app.sandbox import create_sandbox, run_command


def test_create_sandbox_excludes_sensitive_and_dependencies(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    (project / ".env").write_text("SECRET=1", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "knowledge.db").write_bytes(b"db")

    sandbox = create_sandbox(project)
    try:
        assert (sandbox.root / "src" / "app.py").exists()
        assert not (sandbox.root / ".env").exists()
        assert not (sandbox.root / "node_modules").exists()
        assert not (sandbox.root / "data" / "knowledge.db").exists()
    finally:
        sandbox.cleanup()


def test_run_command_allowed_and_blocked(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print(42)", encoding="utf-8")
    sandbox = create_sandbox(project)
    try:
        ok = run_command(sandbox, ["python", "main.py"])
        assert ok["exit_code"] == 0
        assert "42" in ok["stdout"]
        blocked = run_command(sandbox, ["rm", "-rf", "."])
        assert blocked["exit_code"] == 126
    finally:
        sandbox.cleanup()
