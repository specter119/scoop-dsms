# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
from pathlib import Path

from manifest_utils import history_sort_key, load_active_manifests, load_archive_history


README_HEADER = """# Scoop/Shovel dsms Bucket [![Build status](https://ci.appveyor.com/api/projects/status/uem1o78pyghs6lqg/branch/main?svg=true)](https://ci.appveyor.com/project/specter119/dsms/branch/main)

`scoop bucket add dsms 'https://github.com/specter119/scoop-dsms.git'`

This bucket contains curated Windows application manifests for Scoop/Shovel.
The package index below is generated from the manifests stored in this repository.

To refresh this document locally, run `python scripts/generate_readme.py --write`.
"""


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|")


def homepage_cell(homepage: str) -> str:
    if not homepage:
        return "—"
    escaped = escape_cell(homepage)
    return f"[Website]({escaped})"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    divider_row = "| " + " | ".join(["---"] * len(headers)) + " |"
    body_rows = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_row, divider_row, *body_rows])


def build_available_packages_section() -> str:
    manifests = load_active_manifests(Path("bucket"))
    rows = [
        [
            f"`{manifest.package}`",
            escape_cell(manifest.description),
            homepage_cell(manifest.homepage),
        ]
        for manifest in manifests
    ]

    lines = [
        "## Available Packages",
        "",
        f"Active manifests: **{len(manifests)}**",
        "",
        render_table(["Package", "Description", "Homepage"], rows),
    ]
    return "\n".join(lines)


def build_archive_history_section() -> str:
    history = sorted(
        load_archive_history(Path("docs/archive-history.json")),
        key=history_sort_key,
        reverse=True,
    )

    rows = []
    for entry in history:
        rows.append(
            [
                f"`{escape_cell(str(entry['package']))}`",
                f"`{escape_cell(str(entry['version']))}`",
                escape_cell(str(entry.get("archived_at", "—"))),
                escape_cell(str(entry.get("reason", "—"))),
            ]
        )

    if not rows:
        rows.append(["—", "—", "—", "—"])

    lines = [
        "## Pinned Archive History",
        "",
        "This section tracks manifests that have already been moved to `bucket/old`.",
        "",
        render_table(["Package", "Version", "Archived At", "Reason"], rows),
    ]
    return "\n".join(lines)


def build_readme() -> str:
    return "\n\n".join(
        [
            README_HEADER.strip(),
            build_available_packages_section(),
            build_archive_history_section(),
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate README.md from bucket manifests.")
    parser.add_argument("--write", action="store_true", help="Write the generated README to disk.")
    parser.add_argument("--check", action="store_true", help="Fail if README.md is out of date.")
    args = parser.parse_args()

    readme_path = Path("README.md")
    generated = build_readme()

    if args.check:
        current = readme_path.read_text(encoding="utf-8")
        if current != generated:
            print("README.md is out of date.")
            return 1
        print("README.md is up to date.")
        return 0

    if args.write:
        readme_path.write_text(generated, encoding="utf-8")
        print(f"Updated {readme_path}.")
        return 0

    print(generated, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
