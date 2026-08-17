from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.config import settings


def main() -> None:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = settings.data_dir / "backups" / f"backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_path = settings.data_dir / "knowledge.db"
    if db_path.exists():
        shutil.copy2(db_path, backup_dir / "knowledge.db")

    uploads_dir = settings.data_dir / "uploads"
    if uploads_dir.exists():
        shutil.copytree(uploads_dir, backup_dir / "uploads", dirs_exist_ok=True)

    prompts_dir = settings.prompt_dir
    if prompts_dir.exists():
        shutil.copytree(prompts_dir, backup_dir / "prompts", dirs_exist_ok=True)

    print(f"backup created: {backup_dir}")


if __name__ == "__main__":
    main()
