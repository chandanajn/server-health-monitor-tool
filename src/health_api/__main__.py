"""Entrypoint for running the service directly with python -m health_api."""

from __future__ import annotations

import uvicorn

from health_api.main import create_app
from health_api.settings import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    # Run with a single worker because the checkpoint file has one writer
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()

