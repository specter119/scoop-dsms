# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from manifest_utils import (
    load_active_manifests,
    load_archive_history,
    write_archive_history,
)


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_json_payload(text: str) -> Any:
    candidates = [text.strip(), strip_code_fence(text)]

    stripped = strip_code_fence(text)
    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = stripped.find(open_char)
        end = stripped.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError("Could not parse JSON recommendations from Gemini output.")


def load_recommendations(path: Path) -> list[dict[str, Any]]:
    payload = parse_json_payload(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        recommendations = payload
    else:
        recommendations = payload.get("recommendations", [])

    if not isinstance(recommendations, list):
        raise ValueError("Recommendations payload must contain a list.")
    return [item for item in recommendations if isinstance(item, dict)]


def should_apply(recommendation: dict[str, Any]) -> bool:
    if recommendation.get("action") == "archive":
        return True
    return bool(recommendation.get("should_archive"))


def build_pr_title(packages: list[str]) -> str:
    if len(packages) == 1:
        return f"[archive] Archive {packages[0]}"
    if len(packages) <= 3:
        return f"[archive] Archive {', '.join(packages)}"
    return f"[archive] Archive {len(packages)} inactive manifests"


def build_pr_body(selected: list[dict[str, Any]]) -> str:
    lines = [
        "## Summary",
        "",
        "This automated PR archives manifests that look inactive based on upstream GitHub activity and Gemini review.",
        "",
        "## Archived Candidates",
        "",
        "| Package | Version | Confidence | Reason |",
        "| --- | --- | --- | --- |",
    ]

    for item in selected:
        lines.append(
            f"| `{item['package']}` | `{item['version']}` | {item.get('confidence', 'unknown')} | {item['reason']} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Please review each candidate before merging.",
            "- The corresponding history entries are tracked in `docs/archive-history.json`.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply archive recommendations produced by Gemini.")
    parser.add_argument("--recommendations", required=True)
    parser.add_argument("--history", default="docs/archive-history.json")
    parser.add_argument("--bucket-dir", default="bucket")
    parser.add_argument("--date", required=True)
    parser.add_argument("--metadata-out", required=True)
    parser.add_argument("--max-candidates", type=int, default=5)
    args = parser.parse_args()

    active_manifests = {manifest.package: manifest for manifest in load_active_manifests(Path(args.bucket_dir))}
    history_path = Path(args.history)
    history = load_archive_history(history_path)
    history_index = {(entry["package"], entry["version"]) for entry in history}
    recommendations = load_recommendations(Path(args.recommendations))

    selected: list[dict[str, Any]] = []
    for recommendation in recommendations:
        package = recommendation.get("package")
        if not isinstance(package, str) or package not in active_manifests:
            continue
        if not should_apply(recommendation):
            continue

        manifest = active_manifests[package]
        if (manifest.package, manifest.version) in history_index:
            continue

        selected.append(
            {
                "package": manifest.package,
                "version": manifest.version,
                "reason": str(recommendation.get("reason", "Recommended by Gemini archive review.")),
                "confidence": str(recommendation.get("confidence", "unknown")),
                "evidence": recommendation.get("evidence", []),
                "source_repo": manifest.github_repo,
                "source_path": str(manifest.path),
            }
        )

        if len(selected) >= args.max_candidates:
            break

    metadata = {
        "selected_count": len(selected),
        "title": "",
        "branch": "",
        "commit_message": "",
        "body_path": ".archive/archive-pr-body.md",
    }

    if not selected:
        metadata_path = Path(args.metadata_out)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print("No archive candidates were applied.")
        return 0

    for item in selected:
        source_path = Path(item["source_path"])
        target_dir = Path(args.bucket_dir) / "old" / item["package"]
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{item['version']}.yml"
        if target_path.exists():
            raise FileExistsError(f"Archive target already exists: {target_path}")

        shutil.move(str(source_path), str(target_path))

        history.append(
            {
                "package": item["package"],
                "version": item["version"],
                "archived_at": args.date,
                "reason": item["reason"],
                "evidence": item["evidence"],
                "source_repo": item["source_repo"],
            }
        )

    write_archive_history(history_path, history)

    packages = [item["package"] for item in selected]
    slug = "-".join(packages[:3])
    metadata.update(
        {
            "selected_count": len(selected),
            "title": build_pr_title(packages),
            "branch": f"automation/archive-{args.date}-{slug}",
            "commit_message": f"chore: archive {', '.join(packages)}",
        }
    )

    body_path = Path(metadata["body_path"])
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(build_pr_body(selected), encoding="utf-8")

    metadata_path = Path(args.metadata_out)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Applied {len(selected)} archive recommendation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
