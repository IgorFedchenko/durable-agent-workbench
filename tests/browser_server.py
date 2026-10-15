"""Real HTTP server with test-owned read-delay barriers; response bodies are never mocked."""

import asyncio
import os
from pathlib import Path

import uvicorn

from agent_workbench.api import create_app


def main():
    directory = Path(os.environ["TEST_WORKBENCH_DATA"])
    port = int(os.environ["TEST_WORKBENCH_PORT"])
    app = create_app(directory, port)

    @app.middleware("http")
    async def controlled_read(request, call_next):
        control = directory / "delay-read"
        if request.method == "GET" and control.exists() and request.url.path == control.read_text():
            (directory / "read-blocked").write_text(request.url.path)
            while control.exists():
                await asyncio.sleep(0.03)
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
