"""Install preserved reference fixtures for the two legacy AST tests; never overwrite."""

import hashlib
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    if root.name != "AXIOM" or root.parents[1].name != "candidates":
        raise SystemExit("Use a review layout ending in candidates/current/AXIOM")
    refs = {
        "backend/app/tick.py": "ARC1_REF_TICK_20261029_001.txt",
        "backend/tests/test_process.py": "ARC1_REF_PROCESS_20261029_001.txt",
    }
    for relative, filename in refs.items():
        source = root / "integration" / filename
        destination = root.parents[1] / "g1-v4-001/AXIOM" / relative
        data = source.read_bytes()
        if destination.exists():
            if destination.read_bytes() != data:
                raise SystemExit("STOP: a different legacy reference already exists")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(data)
        print(relative + " sha256=" + hashlib.sha256(data).hexdigest())
    print("Legacy AST reference fixtures ready (2); source candidate unchanged.")


if __name__ == "__main__":
    main()
