from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    host = "127.0.0.1"
    port = 8000
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app", "--host", host, "--port", str(port)],
        cwd=str(root),
    )

    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    url = f"http://{host}:{port}"
    print(f"服务已启动：{url}")
    if not os.getenv("DOCKER_CONTAINER"):
        webbrowser.open(url)

    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        print("服务已停止。")


if __name__ == "__main__":
    main()
