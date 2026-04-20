from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TOP_LEVEL_FIELD_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")
GITHUB_URL_RE = re.compile(r"https?://github\.com/([^/\s\"']+)/([^/\s\"')]+)")


@dataclass(frozen=True)
class ManifestRecord:
    package: str
    version: str
    description: str
    homepage: str
    path: Path
    github_repo: str | None


def clean_scalar(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""

    if value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            parsed = value[1:-1]
        return " ".join(str(parsed).split())

    return " ".join(value.split())


def parse_top_level_scalar(text: str, key: str) -> str | None:
    lines = text.splitlines()
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        if line.startswith((" ", "\t")):
            line_index += 1
            continue

        match = TOP_LEVEL_FIELD_RE.match(line)
        if not match or match.group(1) != key:
            line_index += 1
            continue

        raw_value = (match.group(2) or "").rstrip()
        if raw_value in {"|", ">"}:
            block_lines: list[str] = []
            line_index += 1
            while line_index < len(lines):
                next_line = lines[line_index]
                if next_line and not next_line.startswith((" ", "\t")):
                    break
                if next_line.strip():
                    block_lines.append(next_line.lstrip())
                line_index += 1
            return " ".join(" ".join(block_lines).split())

        return clean_scalar(raw_value)

    return None


def normalize_github_repo(url: str) -> str | None:
    parsed = urlparse(url.rstrip(").,"))
    if parsed.netloc.lower() != "github.com":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0].strip()
    repo = parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def discover_github_repo(text: str) -> str | None:
    explicit_match = re.search(
        r"(?m)^\s*github:\s*(https?://github\.com/[^\s\"']+)\s*$",
        text,
    )
    if explicit_match:
        normalized = normalize_github_repo(explicit_match.group(1))
        if normalized:
            return normalized

    for owner, repo in GITHUB_URL_RE.findall(text):
        normalized = normalize_github_repo(f"https://github.com/{owner}/{repo}")
        if normalized:
            return normalized
    return None


def parse_manifest(path: Path) -> ManifestRecord:
    text = path.read_text(encoding="utf-8")
    version = parse_top_level_scalar(text, "version") or "unknown"
    description = parse_top_level_scalar(text, "description") or "—"
    homepage = parse_top_level_scalar(text, "homepage") or ""
    github_repo = discover_github_repo(text)
    return ManifestRecord(
        package=path.stem,
        version=version,
        description=description,
        homepage=homepage,
        path=path,
        github_repo=github_repo,
    )


def load_active_manifests(bucket_dir: Path) -> list[ManifestRecord]:
    return sorted(
        (parse_manifest(path) for path in bucket_dir.glob("*.yml")),
        key=lambda record: record.package.lower(),
    )


def load_archived_manifests(bucket_old_dir: Path) -> list[ManifestRecord]:
    return sorted(
        (parse_manifest(path) for path in bucket_old_dir.glob("*/*.yml")),
        key=lambda record: (record.package.lower(), record.version),
    )


def load_archive_history(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text(encoding="utf-8"))


def write_archive_history(history_path: Path, history: list[dict[str, Any]]) -> None:
    history_path.write_text(
        json.dumps(history, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def history_sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    archived_at = str(entry.get("archived_at", ""))
    try:
        parsed = datetime.strptime(archived_at, "%Y-%m-%d")
        sortable = parsed.strftime("%Y%m%d")
        return (1, sortable, str(entry.get("package", "")).lower())
    except ValueError:
        return (0, archived_at.lower(), str(entry.get("package", "")).lower())
