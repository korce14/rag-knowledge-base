from __future__ import annotations

from typing import Any

from . import tools


def run_tool(name: str, args: dict[str, Any], service=None, actor=None) -> Any:
    if name == "calculate":
        return {"result": tools.calculate(str(args.get("expression", "")))}
    if name == "sql_query":
        return {"result": tools.sql_query(str(args.get("query", "")))}
    if name == "plot_chart":
        return {
            "path": tools.plot_chart(
                str(args.get("kind", "line")),
                args.get("x", []),
                args.get("y", []),
                str(args.get("title", "chart")),
            )
        }
    if name == "retrieve":
        return {
            "result": tools.retrieve(
                str(args.get("kb_id", "")),
                str(args.get("query", "")),
                int(args.get("top_k", 5)),
                service,
                actor,
                args.get("document_id"),
            )
        }
    return {"error": f"未知工具：{name}"}
