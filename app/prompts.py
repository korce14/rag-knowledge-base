from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import Any

import yaml

from .config import settings


class PromptVersionNotFound(RuntimeError):
    pass


class PromptManager:
    def __init__(self, prompt_dir: Path | None = None):
        self.prompt_dir = Path(prompt_dir or settings.prompt_dir)
        self.prompt_dir.mkdir(parents=True, exist_ok=True)

    def list_versions(self, name: str) -> list[dict[str, str]]:
        versions: list[dict[str, str]] = []
        pattern = re.compile(rf"^{re.escape(name)}_v(.+)\.ya?ml$")
        for path in sorted(self.prompt_dir.glob(f"{name}_v*.y*ml")):
            match = pattern.match(path.name)
            if not match:
                continue
            data = self._load(path)
            versions.append(
                {
                    "version": str(data.get("version", match.group(1))),
                    "name": str(data.get("name", name)),
                    "file": path.name,
                }
            )
        return versions

    def render(self, name: str, variables: dict[str, Any] | None = None, version: str | None = None) -> dict[str, str]:
        variables = variables or {}
        path = self._resolve(name, version)
        data = self._load(path)
        return {
            "version": str(data.get("version", "")),
            "name": str(data.get("name", name)),
            "system": self._template(data.get("system", "")).safe_substitute(variables),
            "user": self._template(data.get("user", "")).safe_substitute(variables),
        }

    def get(self, name: str, version: str | None = None) -> dict[str, Any]:
        path = self._resolve(name, version)
        return self._load(path)

    def _resolve(self, name: str, version: str | None) -> Path:
        requested = version or settings.prompt_version
        if requested and requested != "latest":
            candidate = self.prompt_dir / f"{name}_v{requested}.yaml"
            if candidate.exists():
                return candidate
            candidate = self.prompt_dir / f"{name}_v{requested}.yml"
            if candidate.exists():
                return candidate
            raise PromptVersionNotFound(f"未找到 Prompt 版本：{name} v{requested}")

        versions = self.list_versions(name)
        if not versions:
            raise PromptVersionNotFound(f"未找到 Prompt：{name}")
        latest_file = versions[-1]["file"]
        return self.prompt_dir / latest_file

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Prompt 文件格式错误：{path}")
        return data

    @staticmethod
    def _template(text: str) -> Template:
        return Template(text)
