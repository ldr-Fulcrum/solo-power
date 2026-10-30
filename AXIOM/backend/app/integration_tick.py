"""Explicit finite combined worker; importing does not activate anything."""

import argparse
from pathlib import Path

from backend.app.bakeoff import Services
from backend.app.bakeoff_worker import Worker
from backend.app.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-jobs", type=int, choices=range(1, 51), default=20)
    args = parser.parse_args()
    settings = Settings.from_environment()
    settings = Settings(
        root=settings.root,
        state_dir=args.state_dir,
        synthetic_mode=settings.synthetic_mode,
        bind_host=settings.bind_host,
        allow_docker_host=settings.allow_docker_host,
    )
    services = Services(settings)
    try:
        print(Worker(services).tick(max_jobs=args.max_jobs))
    finally:
        if services.bakeoff:
            services.bakeoff.close()
        services.store.engine.dispose()


if __name__ == "__main__":
    main()
