"""Read the pinned IMM workbooks to JSON with the bundled analysis Python.

Requires openpyxl for reading only. Never saves or changes the original XLSX.
The project research environment does not acquire an Excel authoring dependency.
"""

import argparse
import json
from pathlib import Path

import openpyxl
from collect_public_data import CATALOG, LIBRARY, digest_file, validate_file, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--library", type=Path, default=LIBRARY)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    source = next(s for s in catalog["sources"] if s["id"] == "istanbul-district-consumption")
    sheets = []
    for spec in source["files"]:
        if not spec["path"].endswith(".xlsx"):
            continue
        path = args.library / source["id"] / "raw" / spec["path"]
        actual = validate_file(path, spec)
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook:
                sheets.append(
                    {
                        "file": spec["path"],
                        "sheet": sheet.title,
                        "input_sha256": actual["sha256"],
                        "rows": [list(row) for row in sheet.iter_rows(values_only=True)],
                    }
                )
        finally:
            workbook.close()
    output = args.library / source["id"] / "worksheet-snapshot.json"
    write_json(
        output,
        {
            "schema": "urban-water.imm-worksheet-snapshot.v1",
            "script_sha256": digest_file(Path(__file__))["sha256"],
            "openpyxl_version": openpyxl.__version__,
            "sheets": sheets,
        },
    )
    print(f"Read {len(sheets)} sheets to {output}")


if __name__ == "__main__":
    main()
