import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


NULL_MARKERS = {"", "na", "n/a", "nan", "none", "null"}
SAMPLE_LIMIT = 5
DATETIME_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%H:%M %Y-%m-%d",
    "%H:%M:%S %Y-%m-%d",
    "%H:%M %m-%d-%Y",
    "%H:%M %d-%m-%Y",
)


def is_missing(value: str) -> bool:
    return value.strip().casefold() in NULL_MARKERS


def classify_value(value: str) -> str:
    normalized = value.strip()
    lowered = normalized.casefold()

    if lowered in {"true", "false", "yes", "no", "y", "n"}:
        return "boolean"
    if re.fullmatch(r"[+-]?\d+", normalized):
        return "integer"
    try:
        number = float(normalized)
        if math.isfinite(number):
            return "number"
    except ValueError:
        pass

    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        pass
    for date_format in DATETIME_FORMATS:
        try:
            datetime.strptime(normalized, date_format)
            return "datetime"
        except ValueError:
            continue
    return "string"


def infer_type(type_counts: Counter[str]) -> str:
    types = set(type_counts)
    if not types:
        return "unknown"
    if types == {"integer"}:
        return "integer"
    if types <= {"integer", "number"}:
        return "number"
    if len(types) == 1:
        return next(iter(types))
    return "mixed"


def profile_csv(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        headers = next(reader, None)
        if headers is None:
            raise ValueError(f"{path} is empty and has no header row.")

        column_stats = [
            {
                "missing_count": 0,
                "distinct_values": set(),
                "sample_values": [],
                "sample_seen": set(),
                "type_counts": Counter(),
            }
            for _ in headers
        ]
        seen_rows: set[tuple[str, ...]] = set()
        duplicate_rows = 0
        malformed_rows = 0
        row_count = 0

        for row in reader:
            row_count += 1
            if len(row) != len(headers):
                malformed_rows += 1
            if len(row) < len(headers):
                row.extend([""] * (len(headers) - len(row)))

            row_key = tuple(row)
            if row_key in seen_rows:
                duplicate_rows += 1
            else:
                seen_rows.add(row_key)

            for index, stats in enumerate(column_stats):
                value = row[index] if index < len(row) else ""
                if is_missing(value):
                    stats["missing_count"] += 1
                    continue

                stats["distinct_values"].add(value)
                stats["type_counts"][classify_value(value)] += 1
                if (
                    value not in stats["sample_seen"]
                    and len(stats["sample_values"]) < SAMPLE_LIMIT
                ):
                    stats["sample_seen"].add(value)
                    stats["sample_values"].append(value)

    columns = []
    for name, stats in zip(headers, column_stats):
        missing_count = stats["missing_count"]
        missing_percentage = (
            round(missing_count / row_count * 100, 2) if row_count else 0.0
        )
        columns.append(
            {
                "name": name,
                "inferred_type": infer_type(stats["type_counts"]),
                "missing_count": missing_count,
                "missing_percentage": missing_percentage,
                "distinct_count": len(stats["distinct_values"]),
                "sample_values": stats["sample_values"],
            }
        )

    return {
        "file": str(path),
        "row_count": row_count,
        "column_count": len(headers),
        "duplicate_rows": duplicate_rows,
        "malformed_rows": malformed_rows,
        "columns": columns,
    }


def find_csv_files(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.casefold() != ".csv":
            raise ValueError(f"{path} is not a CSV file.")
        return [path]
    if not path.is_dir():
        raise ValueError(f"{path} is not a file or directory.")
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.casefold() == ".csv"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a read-only profile of CSV files."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent / "raw",
        help="CSV file or directory to profile (default: ./raw)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the JSON report to this file instead of standard output",
    )
    args = parser.parse_args()

    try:
        csv_files = find_csv_files(args.path)
        if not csv_files:
            raise ValueError(f"No CSV files found in {args.path}.")
        if args.output and args.output.resolve() in {
            csv_file.resolve() for csv_file in csv_files
        }:
            raise ValueError("The output path must not overwrite an input CSV file.")
        report = {"datasets": [profile_csv(path) for path in csv_files]}
        rendered_report = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(rendered_report, encoding="utf-8")
        else:
            sys.stdout.write(rendered_report)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
