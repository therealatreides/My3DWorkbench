"""Zero-dependency test runner: ``python tests/run_tests.py``

Discovers every ``test_*`` function in the test modules next to it, runs each
in isolation (each failing test reports its own stack trace), and exits
non-zero if anything failed. Works with the stock Flask-only venv — no pytest.
"""
import importlib
import inspect
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
sys.path.insert(0, ROOT)

# Fresh throwaway DB + uploads dir for the whole run (before the app is
# created anywhere).
_TMP_DIR = tempfile.mkdtemp(prefix="spool-test-")
os.environ["SPOOL_DB"] = os.path.join(_TMP_DIR, "test.db")
os.environ["SPOOL_UPLOADS"] = os.path.join(_TMP_DIR, "uploads")
os.environ.pop("SEED_DEMO", None)

FAILED = []
PASSED = []


def _run(mod_name):
    mod = importlib.import_module(mod_name)
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except Exception:
            FAILED.append((mod_name, name))
            print(f"  FAIL  {mod_name}.{name}")
            tb = traceback.format_exc(limit=3)
            for line in tb.rstrip().splitlines()[-3:]:
                print(f"        {line}")
        else:
            PASSED.append((mod_name, name))
            print(f"  ok    {mod_name}.{name}")


def main():
    print(f"spool test run — temp env: {_TMP_DIR}\n")
    for mod_name in ("test_cost", "test_api"):
        try:
            _run(mod_name)
        except Exception:
            FAILED.append((mod_name, "<import>"))
            print(f"  FAIL  {mod_name} could not be imported:")
            traceback.print_exc(limit=4)
        print()

    print("=" * 60)
    print(f"PASSED {len(PASSED)}   FAILED {len(FAILED)}")
    if FAILED:
        print("\nFailing tests:")
        for m, n in FAILED:
            print(f"  - {m}.{n}")
        sys.exit(1)
    print("ALL GREEN")


if __name__ == "__main__":
    main()
