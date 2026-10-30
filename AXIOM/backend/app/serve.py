"""Configuration-aware local server. Importing this module never starts a listener."""

import uvicorn

from backend.app.config import Settings
from backend.app.main import create_app


def main():
    settings = Settings.from_environment()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
