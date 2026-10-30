"""Finite CLI only. Explicit state directory prevents accidental baseline access."""

import argparse
import os
import time
from pathlib import Path

from backend.app.config import ROOT, Settings
from backend.app.loop import Services
from backend.app.worker import Worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--ticks", type=int, choices=range(1, 61), default=1)
    parser.add_argument("--interval", type=float, default=1)
    args = parser.parse_args()
    if not 0 <= args.interval <= 5:
        parser.error("interval must be 0..5 seconds")
    settings = Settings(
        root=ROOT,
        state_dir=args.state_dir,
        synthetic_mode=os.environ.get("DARLA_SYNTHETIC_MODE") == "1",
    )
    services = Services(settings)
    try:
        completed = 0
        for index in range(args.ticks):
            if index:
                time.sleep(args.interval)
            completed += Worker(services).tick()
        print("Verified local runs completed:", completed)
    finally:
        services.store.engine.dispose()


if __name__ == "__main__":
    main()
