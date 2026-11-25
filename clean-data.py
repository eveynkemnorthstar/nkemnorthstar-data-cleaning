
import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


NULL_MARKERS = {"", "na", "n/a", "nan", "none", "null"}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
)

EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)

def normalize_column_name(name: str) -> str:
    """Standardize column names."""
    name = name.strip().casefold()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_") or "unnamed"


def normalize_value(value: str) -> str:
    """Remove unnecessary whitespace and normalize missing markers."""
    value = value.strip()

    if value.casefold() in NULL_MARKERS:
        return ""

    return value


def clean_email(value: str) -> tuple[str, bool]:
    """Normalize and validate email addresses."""
    value = value.casefold().strip()

    if not value:
        return "", False

    if EMAIL_PATTERN.fullmatch(value):
        return value, False

    return value, True


def clean_date(value: str) -> tuple[str, bool]:
    """Standardize recognized dates to YYYY-MM-DD."""
    value = value.strip()

    if not value:
        return "", False

    # Try ISO format first.
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return parsed.strftime("%Y-%m-%d"), False
    except ValueError:
        pass

    # Try known date formats.
    matches = []

    for date_format in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, date_format)
            if parsed not in matches:
                matches.append(parsed)
        except ValueError:
            continue

    # Ambiguous dates must not be guessed.
    unique_dates = set(matches)

    if len(unique_dates) == 1:
        return next(iter(unique_dates)).strftime("%Y-%m-%d"), False

    if len(unique_dates) > 1:
        return value, True

    return value, True


def clean_text(value: str, column_name: str) -> str:
    """Apply text standardization based on column name."""
    value = normalize_value(value)

    if not value:
        return ""

    if "email" in column_name:
        return clean_email(value)[0]

    if column_name in {"state", "state_code"}:
        return value.upper()

    if column_name in {"city", "customer_name", "first_name",
                       "last_name", "full_name"}:
        return value.title()

    if "category" in column_name:
        return re.sub(r"\s+", " ", value).title()

    return re.sub(r"\s+", " ", value)


def clean_dataset(input_path: Path, output_dir: Path) -> dict[str, Any]:

    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_cleaned.csv"

    stats = Counter()
    seen_rows = set()

    with input_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as source:

        reader = csv.reader(source)

        headers = next(reader, None)

        if not headers:
            raise ValueError(f"{input_path} has no header row.")

        cleaned_headers = [
            normalize_column_name(header)
            for header in headers
        ]

        # Prevent duplicate column names after normalization.
        header_counts = Counter()
        unique_headers = []

        for header in cleaned_headers:
            header_counts[header] += 1

            if header_counts[header] > 1:
                header = f"{header}_{header_counts[header]}"

            unique_headers.append(header)

        cleaned_headers = unique_headers

        with output_path.open(
            "w", encoding="utf-8", newline=""
        ) as destination:

            writer = csv.writer(destination)
            writer.writerow(cleaned_headers)

            for row in reader:
                stats["rows_processed"] += 1

                # Skip completely empty rows.
                if not row or all(
                    not normalize_value(value) for value in row
                ):
                    stats["empty_rows_removed"] += 1
                    continue

                # Do not silently discard records with extra fields.
                if len(row) != len(headers):
                    stats["malformed_rows"] += 1
                    continue

                cleaned_row = []

                for column_name, value in zip(
                    cleaned_headers, row
                ):

                    original = value
                    cleaned = normalize_value(value)

                    if cleaned != original:
                        stats["values_normalized"] += 1

                    if "email" in column_name:
                        cleaned, invalid = clean_email(cleaned)

                        if invalid:
                            stats["invalid_emails"] += 1

                    elif "date" in column_name or column_name.endswith("_at"):
                        cleaned, invalid = clean_date(cleaned)

                        if invalid and cleaned:
                            stats["invalid_dates"] += 1

                    elif column_name in {
                        "city", "customer_name", "first_name",
                        "last_name", "full_name", "state",
                        "state_code"
                    } or "category" in column_name:
                        cleaned = clean_text(
                            cleaned, column_name
                        )

                    else:
                        cleaned = re.sub(
                            r"\s+", " ", cleaned
                        )

                    cleaned_row.append(cleaned)

                row_key = tuple(cleaned_row)

                # Remove exact duplicates after normalization.
                if row_key in seen_rows:
                    stats["duplicate_rows_removed"] += 1
                    continue

                seen_rows.add(row_key)
                writer.writerow(cleaned_row)

                stats["rows_written"] += 1

    return {
        "input_file": str(input_path),
        "output_file": str(output_path),
        "statistics": dict(stats),
    }


def main() -> int:

    parser = argparse.ArgumentParser(
        description="Clean and standardize CSV datasets."
    )

    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent / "raw",
        help="CSV file or directory to clean."
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Output directory for cleaned CSV files."
    )

    args = parser.parse_args()

    try:
        if args.path.is_file():
            if args.path.suffix.casefold() != ".csv":
                raise ValueError("Input file must be CSV.")

            files = [args.path]

        elif args.path.is_dir():
            files = sorted(
                path for path in args.path.rglob("*")
                if path.is_file()
                and path.suffix.casefold() == ".csv"
            )

        else:
            raise ValueError("Input path does not exist.")

        if not files:
            raise ValueError("No CSV files found.")

        # Prevent output files from being included as inputs.
        output_resolved = args.output.resolve()

        for file in files:
            if file.resolve() == output_resolved or (
                output_resolved in file.resolve().parents
            ):
                raise ValueError(
                    "Output directory must not contain input files."
                )

        reports = []

        for file in files:
            print(f"Cleaning: {file}")

            report = clean_dataset(file, args.output)
            reports.append(report)

            print(json.dumps(report, indent=2))

        report_path = args.output / "cleaning_report.json"

        report_path.write_text(
            json.dumps(
                {"datasets": reports},
                indent=2
            ) + "\n",
            encoding="utf-8"
        )

        print(f"\nCleaning completed.")
        print(f"Cleaning report: {report_path}")

    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        parser.error(str(error))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
