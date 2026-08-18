from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .code_security import is_sensitive_path, validate_code_content
from .guard import Guard
from .sandbox import Sandbox, create_sandbox, run_command

CODING_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出沙箱项目目录中的文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "相对路径，默认 /"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目文件内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或修改项目文件",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "在沙箱中运行测试命令，默认 python -m pytest -q",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "array", "items": {"type": "string"}}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在沙箱中运行白名单命令（python/pytest/npm/node/pip）",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                "required": ["command"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "你是编码工作流 Agent，同时扮演 Developer 和 Tester 两个角色。"
    "Developer 负责读取文件、编写和修改代码；Tester 负责阅读代码、编写测试并运行测试。"
    "所有文件操作和命令都在隔离沙箱中执行。测试未通过时继续修复，最多运行 8 步。"
    "禁止读取或写入 .env、密钥、数据库、日志等敏感文件。"
    "完成时输出 final 结果，包含完成情况和测试结论。"
)


class CodingAgent:
    def __init__(self, generator, guard: Guard | None = None):
        self.generator = generator
        self.guard = guard or Guard()

    def run(
        self,
        task: str,
        project_path: Path | str,
        max_steps: int = 8,
        planner: Callable[..., dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        checked = self.guard.validate_input(task)
        if not checked.ok:
            return {"status": "blocked", "reason": checked.reason, "logs": []}

        sandbox = create_sandbox(Path(project_path))
        logs: list[dict[str, Any]] = []
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        try:
            for step in range(1, max_steps + 1):
                decision = self._decide(planner, messages)
                if decision.get("final"):
                    return {
                        "status": "success",
                        "answer": str(decision["final"]),
                        "steps": step,
                        "logs": logs,
                    }
                tool_name = str(decision.get("tool") or decision.get("function", {}).get("name") or "")
                raw_args = decision.get("args") or decision.get("arguments") or "{}"
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args or {}
                if tool_name not in {tool["function"]["name"] for tool in CODING_TOOLS}:
                    logs.append({"step": step, "error": f"未知工具 {tool_name}"})
                    break
                result = self.execute_tool(tool_name, args, sandbox)
                logs.append({"step": step, "tool": tool_name, "result": result})
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call_{step}",
                                "type": "function",
                                "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
                            }
                        ],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"call_{step}",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if tool_name in {"run_tests", "run_command"} and result.get("exit_code") == 0:
                    logs.append({"step": step, "note": "测试通过，等待 Agent 输出 final 结论"})
            return {
                "status": "success",
                "answer": "已达到最大步骤数，流程结束。",
                "steps": max_steps,
                "logs": logs,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc), "logs": logs}
        finally:
            sandbox.cleanup()

    def _decide(self, planner: Callable[..., dict[str, Any]] | None, messages: list[dict[str, str]]) -> dict[str, Any]:
        if planner is not None:
            return planner(messages)
        content, tool_calls = self.generator.generate_with_tools(messages, CODING_TOOLS)
        if tool_calls:
            call = tool_calls[0]
            return {"tool": call["function"]["name"], "args": call["function"]["arguments"]}
        return {"final": content or "任务完成"}

    @staticmethod
    def execute_tool(tool_name: str, args: dict[str, Any], sandbox: Sandbox) -> dict[str, Any]:
        if tool_name == "list_files":
            path = args.get("path") or "/"
            return {"files": CodingAgent._list_files(sandbox, path)}
        if tool_name == "read_file":
            return CodingAgent._read_file(sandbox, str(args.get("path", "")))
        if tool_name == "write_file":
            return CodingAgent._write_file(sandbox, str(args.get("path", "")), str(args.get("content", "")))
        if tool_name == "run_tests":
            command = args.get("command") or ["python", "-m", "pytest", "-q"]
            return run_command(sandbox, list(command), timeout=120)
        if tool_name == "run_command":
            return run_command(sandbox, list(args.get("command") or []), timeout=120)
        return {"error": f"未知工具：{tool_name}"}

    @staticmethod
    def _resolve(sandbox: Sandbox, relative: str) -> Path:
        target = (sandbox.root / relative.lstrip("/")).resolve()
        if not target.is_relative_to(sandbox.root.resolve()):
            raise ValueError("路径越界，禁止访问沙箱外文件")
        return target

    @staticmethod
    def _list_files(sandbox: Sandbox, relative: str) -> list[str]:
        target = CodingAgent._resolve(sandbox, relative)
        if not target.exists():
            return []
        if target.is_file():
            return [str(target.relative_to(sandbox.root))]
        files: list[str] = []
        for path in sorted(target.rglob("*")):
            if path.is_file() and not is_sensitive_path(path.relative_to(sandbox.root)):
                files.append(str(path.relative_to(sandbox.root)))
        return files

    @staticmethod
    def _read_file(sandbox: Sandbox, relative: str) -> dict[str, Any]:
        target = CodingAgent._resolve(sandbox, relative)
        if is_sensitive_path(target.relative_to(sandbox.root)):
            return {"error": "敏感文件禁止读取"}
        if not target.is_file():
            return {"error": f"文件不存在：{relative}"}
        return {"content": target.read_text(encoding="utf-8", errors="replace")[:20000]}

    @staticmethod
    def _write_file(sandbox: Sandbox, relative: str, content: str) -> dict[str, Any]:
        target = CodingAgent._resolve(sandbox, relative)
        if is_sensitive_path(target.relative_to(sandbox.root)):
            return {"error": "敏感文件禁止写入"}
        ok, findings = validate_code_content(content)
        if not ok:
            return {"error": "危险代码检测未通过", "findings": findings}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": relative}
