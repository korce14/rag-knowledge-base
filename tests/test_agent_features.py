from __future__ import annotations

import pytest

from app.agent_runner import run_tool
from app.pipeline import _choose_tool_heuristic, _looks_like_tool_request
from app.config import settings
from app.models import Role, User
from app.pipeline import KnowledgeBaseService


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("计算一下文档里有什么", False),
        ("这个文档主要讲了什么", False),
        ("你好", False),
        ("帮我计算 3*7+2 等于多少", True),
        ("帮我画一个柱状图", True),
        ("请查询数据库中的表", True),
    ],
)
def test_looks_like_tool_request(question: str, expected: bool) -> None:
    assert _looks_like_tool_request(question) is expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("帮我计算 3*7+2", "calculate"),
        ("帮我画一个柱状图", "plot_chart"),
        ("SELECT COUNT(*) FROM documents", "sql_query"),
        ("检索一下 802.15.4 的内容", "retrieve"),
    ],
)
def test_choose_tool_heuristic(question: str, expected: str) -> None:
    assert _choose_tool_heuristic(question) == expected


def test_agent_runner_calculate() -> None:
    result = run_tool("calculate", {"expression": "(3+5)*2-4"})
    assert result == {"result": 12.0}


def test_agent_runner_rejects_write_sql() -> None:
    with pytest.raises(ValueError):
        run_tool("sql_query", {"query": "DELETE FROM documents"})


def test_react_agent_runs_multiple_tool_calls(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password", "")
    service = KnowledgeBaseService()
    actor = User(id="user_test", username="test", password_hash="x", role=Role.ADMIN)

    decisions = iter([
        {"tool": "retrieve", "args": {"query": "增长率", "top_k": 2}},
        {"tool": "calculate", "args": {"expression": "10*2"}},
        {"final": "最终结果"},
    ])
    seen: list[tuple[str, list[str]]] = []

    def fake_decision(question: str, transcript: list[str]) -> dict:
        seen.append((question, list(transcript)))
        return next(decisions)

    monkeypatch.setattr(service, "_react_decision", fake_decision)
    monkeypatch.setattr(service, "run_agent_tool", lambda name, args, actor=None: {"result": [1, 2]})
    monkeypatch.setattr(service, "_format_tool_output", lambda name, result: f"obs-{name}")

    answer, tool_calls = service._react_agent(actor, "先检索再计算", "kb")
    assert answer == "最终结果"
    assert [call["tool"] for call in tool_calls] == ["retrieve", "calculate"]
    assert len(seen) == 3
    assert "obs-retrieve" in seen[1][1][0]
