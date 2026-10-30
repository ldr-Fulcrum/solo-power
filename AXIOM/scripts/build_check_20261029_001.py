"""Run one documented clean-review check and print a stable success marker."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=["install", "generate", "ui", "types", "build", "ruff", "format", "backend"],
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if root.name != "AXIOM":
        raise SystemExit("Use a disposable review checkout named AXIOM")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["UV_CACHE_DIR"] = str(root / ".local/cache/uv")
    env["npm_config_cache"] = str(root / ".local/cache/npm")
    env.pop("DARLA_BAKEOFF_URL", None)
    env.pop("DARLA_BAKEOFF_TOKEN_ENV", None)
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if not uv or not npm:
        raise SystemExit("Install the documented uv and Node/npm versions first")

    def run(command, frontend=False):
        subprocess.run(command, cwd=root / "frontend" if frontend else root, env=env, check=True)

    def python(*rest):
        return [uv, "run", "--frozen", "python", "-B", *rest]

    if args.stage == "install":
        run([uv, "sync", "--frozen", "--python", sys.executable])
        run([npm, "ci", "--ignore-scripts"], frontend=True)
    elif args.stage == "generate":
        run([npm, "run", "generate"], frontend=True)
    elif args.stage == "ui":
        run([npm, "test"], frontend=True)
    elif args.stage == "types":
        run([npm, "exec", "--", "tsc", "-b"], frontend=True)
    elif args.stage == "build":
        run([npm, "run", "build"], frontend=True)
    elif args.stage == "ruff":
        run(python("-m", "ruff", "check", "--no-cache", "backend"))
    elif args.stage == "format":
        run(python("-m", "ruff", "format", "--check", "--no-cache", "backend"))
    elif args.stage == "backend":
        if (root / ".local/reproduction-backend-001").exists():
            raise SystemExit("Use a fresh review copy; existing test state is preserved")
        run(python("scripts/prepare_legacy_test_refs_20261029_001.py"))
        run(
            python(
                "-m",
                "pytest",
                "backend/tests",
                "--basetemp=.local/reproduction-backend-001",
                "-q",
                "--tb=short",
            )
        )
    print(args.stage.upper() + " PASS")


if __name__ == "__main__":
    main()
