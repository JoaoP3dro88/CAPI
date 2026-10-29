"""validate_schema.py — JSON contract validator.

Checks that the generated JSON files (dashboard.json, realtime.json,
livedata.json) contain the required top-level keys and nested structures
defined in documentation/json-contract.md.

Usage:
    python src/validate_schema.py                       # validate outputs/
    python src/validate_schema.py --public              # validate public/ folder
    python src/validate_schema.py --file outputs/realtime.json --type realtime
    python src/validate_schema.py --all                 # validate both outputs/ and public/

Exit code 0 = all checks pass. Non-zero = at least one file failed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Schema definitions
# Each schema is a nested dict:
#   key → required (True/False) | nested schema dict | list with one element schema
# ---------------------------------------------------------------------------

_MACHINE_STATE_SCHEMA: dict = {
    "activity": True,
    "current_oee": True,
    # optional fields intentionally omitted — presence is not required
}

_MACHINE_SCHEMA: dict = {
    "machine_id": True,
    "process_type": True,
    "line_id": True,
    "machine_type": True,
    "static_oee": True,
    "state": _MACHINE_STATE_SCHEMA,
}

_STOCK_SCHEMA: dict = {
    "line_id": True,
    "trays": True,
    "tray_details": True,
}

_FRAME_SCHEMA: dict = {
    "time_sec": True,
    "backlog_orders": True,
    "produced_orders": True,
    "stock_levels": True,
    "machines": True,
}

_ORDER_META_SCHEMA: dict = {
    "order_id": True,
}

_HSM_ITEM_SCHEMA: dict = {
    "body_partnumber": True,
    "body_qtty": True,
    "nozzles": True,
}

_KPI_SCHEMA: dict = {
    "total_time_sec": True,
    "total_trays": True,
    "global_oee": True,
    "machines_count": True,
}

DASHBOARD_SCHEMA: dict = {
    "kpis": _KPI_SCHEMA,
    "machines": True,
    "timeline": True,
    "stock_final": True,
    "orders": True,
}

REALTIME_SCHEMA: dict = {
    "metadata": {
        "step_sec": True,
        "total_time_sec": True,
        "generated_at": True,
    },
    "frames": True,
    # optional: orders_by_id, hard_stage_market
}

LIVEDATA_SCHEMA: dict = {
    "metadata": {
        "generated_at": True,
    },
    "snapshot": _FRAME_SCHEMA,
    # optional: orders_by_id, hard_stage_market
}

SCHEMAS: dict[str, dict] = {
    "dashboard": DASHBOARD_SCHEMA,
    "realtime": REALTIME_SCHEMA,
    "livedata": LIVEDATA_SCHEMA,
}

# ---------------------------------------------------------------------------

Errors = list[str]


def _check(data: Any, schema: Any, path: str, errors: Errors) -> None:
    """Recursively validate *data* against *schema*, appending errors."""
    if isinstance(schema, dict):
        if not isinstance(data, dict):
            errors.append(f"{path}: expected object, got {type(data).__name__}")
            return
        for key, sub_schema in schema.items():
            if key not in data:
                errors.append(f"{path}.{key}: MISSING required key")
            else:
                _check(data[key], sub_schema, f"{path}.{key}", errors)
    elif schema is True:
        # Just presence check — any non-None value is fine
        if data is None:
            errors.append(f"{path}: value is null (expected non-null)")


def _load(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _detect_type(filename: str) -> str | None:
    name = Path(filename).name.lower().replace(".gz", "")
    if name.startswith("dashboard"):
        return "dashboard"
    if name.startswith("realtime"):
        return "realtime"
    if name.startswith("livedata"):
        return "livedata"
    return None


def validate_file(path: Path, file_type: str | None = None) -> Errors:
    """Validate a single JSON/JSON.GZ file. Returns list of error strings."""
    errors: Errors = []

    if not path.exists():
        return [f"{path}: file not found"]

    try:
        data = _load(path)
    except Exception as exc:
        return [f"{path}: could not parse — {exc}"]

    detected = file_type or _detect_type(path.name)
    if detected is None:
        return [f"{path}: unknown file type (use --type dashboard|realtime|livedata)"]

    schema = SCHEMAS.get(detected)
    if schema is None:
        return [f"{path}: no schema defined for type '{detected}'"]

    _check(data, schema, str(path.name), errors)

    # Extra checks for realtime.json — validate first frame structure
    if detected == "realtime" and isinstance(data.get("frames"), list) and data["frames"]:
        _check(data["frames"][0], _FRAME_SCHEMA, f"{path.name}.frames[0]", errors)
        first_machines = data["frames"][0].get("machines", [])
        if first_machines:
            _check(first_machines[0], _MACHINE_SCHEMA, f"{path.name}.frames[0].machines[0]", errors)

    # Extra checks for livedata.json — validate snapshot machines
    if detected == "livedata":
        snapshot = data.get("snapshot", {})
        if isinstance(snapshot, dict):
            machines = snapshot.get("machines", [])
            if machines:
                _check(machines[0], _MACHINE_SCHEMA, f"{path.name}.snapshot.machines[0]", errors)

    # Extra checks for hard_stage_market if present
    hsm = data.get("hard_stage_market")
    if isinstance(hsm, list) and hsm:
        _check(hsm[0], _HSM_ITEM_SCHEMA, f"{path.name}.hard_stage_market[0]", errors)

    # Extra checks for orders_by_id if present
    orders_by_id = data.get("orders_by_id")
    if isinstance(orders_by_id, dict) and orders_by_id:
        first_key = next(iter(orders_by_id))
        _check(
            orders_by_id[first_key],
            _ORDER_META_SCHEMA,
            f"{path.name}.orders_by_id['{first_key}']",
            errors,
        )

    return errors


def validate_folder(folder: Path) -> dict[str, Errors]:
    """Validate all known JSON files in *folder*. Returns {filename: [errors]}."""
    results: dict[str, Errors] = {}
    for name in ("dashboard.json", "realtime.json", "livedata.json",
                 "dashboard.json.gz", "realtime.json.gz", "livedata.json.gz"):
        path = folder / name
        if path.exists():
            results[str(path)] = validate_file(path)
    return results


def _print_results(results: dict[str, Errors]) -> int:
    """Print results and return exit code (0 = all pass)."""
    all_pass = True
    for filepath, errors in results.items():
        if errors:
            all_pass = False
            print(f"\n❌  {filepath}")
            for err in errors:
                print(f"    • {err}")
        else:
            print(f"✅  {filepath}")
    return 0 if all_pass else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate generated JSON files against the backend contract schema."
    )
    parser.add_argument("--file", help="Path to a single JSON file to validate")
    parser.add_argument(
        "--type",
        choices=["dashboard", "realtime", "livedata"],
        help="Explicitly set file type (overrides auto-detection from filename)",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Validate files in 'Dashboard and Real-Time Playback/public/'",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate both outputs/ and public/ folders",
    )
    args = parser.parse_args()

    results: dict[str, Errors] = {}

    if args.file:
        p = Path(args.file)
        results[str(p)] = validate_file(p, args.type)
    elif args.public or args.all:
        public_folder = REPO_ROOT / "Dashboard and Real-Time Playback" / "public"
        results.update(validate_folder(public_folder))
        if args.all:
            results.update(validate_folder(REPO_ROOT / "outputs"))
    else:
        results.update(validate_folder(REPO_ROOT / "outputs"))

    if not results:
        print("No JSON files found to validate.")
        sys.exit(0)

    sys.exit(_print_results(results))


if __name__ == "__main__":
    main()
