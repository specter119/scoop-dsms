# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from manifest_utils import load_active_manifests, load_archive_history


STALE_DAYS = 540
MEDIUM_STALE_DAYS = 365


def github_get_json(url: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "scoop-dsms-archive-collector",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(value: str | None) -> int | None:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return None
    return (datetime.now(UTC) - parsed).days


def fetch_repo_activity(repo: str, token: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "repo": repo,
        "archived": None,
        "disabled": None,
        "pushed_at": None,
        "pushed_age_days": None,
        "latest_release_published_at": None,
        "latest_release_age_days": None,
        "html_url": f"https://github.com/{repo}",
        "error": None,
    }

    try:
        repo_data = github_get_json(f"https://api.github.com/repos/{repo}", token)
        metadata["archived"] = repo_data.get("archived")
        metadata["disabled"] = repo_data.get("disabled")
        metadata["pushed_at"] = repo_data.get("pushed_at")
        metadata["pushed_age_days"] = age_days(repo_data.get("pushed_at"))
        metadata["html_url"] = repo_data.get("html_url", metadata["html_url"])

        releases = github_get_json(
            f"https://api.github.com/repos/{repo}/releases?per_page=1",
            token,
        )
        if releases:
            latest_release = releases[0]
            published_at = latest_release.get("published_at") or latest_release.get("created_at")
            metadata["latest_release_published_at"] = published_at
            metadata["latest_release_age_days"] = age_days(published_at)
    except HTTPError as error:
        metadata["error"] = f"HTTP {error.code}"
    except URLError as error:
        metadata["error"] = str(error.reason)

    return metadata


def build_signal_summary(activity: dict[str, Any]) -> dict[str, Any]:
    archived = bool(activity.get("archived"))
    pushed_age_days = activity.get("pushed_age_days")
    latest_release_age_days = activity.get("latest_release_age_days")

    reasons: list[str] = []
    confidence = "low"
    should_archive = False

    if archived:
        should_archive = True
        confidence = "high"
        reasons.append("The upstream GitHub repository is archived.")
    elif (
        isinstance(pushed_age_days, int)
        and isinstance(latest_release_age_days, int)
        and pushed_age_days >= STALE_DAYS
        and latest_release_age_days >= STALE_DAYS
    ):
        should_archive = True
        confidence = "high"
        reasons.append(
            f"The upstream repository has not published a release for {latest_release_age_days} days "
            f"and has not received code activity for {pushed_age_days} days."
        )
    elif (
        isinstance(pushed_age_days, int)
        and pushed_age_days >= STALE_DAYS
        and latest_release_age_days is None
    ):
        confidence = "medium"
        reasons.append(
            f"The upstream repository has not received code activity for {pushed_age_days} days."
        )
    elif (
        isinstance(pushed_age_days, int)
        and isinstance(latest_release_age_days, int)
        and pushed_age_days >= MEDIUM_STALE_DAYS
        and latest_release_age_days >= MEDIUM_STALE_DAYS
    ):
        confidence = "medium"
        reasons.append(
            f"The upstream repository has been quiet for at least {MEDIUM_STALE_DAYS} days across both code and releases."
        )

    if activity.get("error"):
        reasons.append(f"GitHub activity lookup failed: {activity['error']}.")

    return {
        "should_archive": should_archive,
        "confidence": confidence,
        "reasons": reasons,
    }


def fetch_recent_archive_prs(repo: str, token: str | None) -> list[dict[str, Any]]:
    try:
        pulls = github_get_json(
            f"https://api.github.com/repos/{repo}/pulls?state=all&per_page=100",
            token,
        )
    except (HTTPError, URLError):
        return []

    results = []
    for pull in pulls:
        title = pull.get("title", "")
        if not title.lower().startswith("[archive]"):
            continue
        results.append(
            {
                "number": pull.get("number"),
                "title": title,
                "state": pull.get("state"),
                "merged_at": pull.get("merged_at"),
                "html_url": pull.get("html_url"),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect archive recommendation signals.")
    parser.add_argument("--bucket-dir", default="bucket")
    parser.add_argument("--history", default="docs/archive-history.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo", required=True, help="Current GitHub repository in owner/name format.")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    bucket_dir = Path(args.bucket_dir)
    manifests = load_active_manifests(bucket_dir)
    history = load_archive_history(Path(args.history))

    repo_cache: dict[str, dict[str, Any]] = {}
    packages = []
    for manifest in manifests:
        activity = None
        if manifest.github_repo:
            activity = repo_cache.get(manifest.github_repo)
            if activity is None:
                activity = fetch_repo_activity(manifest.github_repo, token)
                repo_cache[manifest.github_repo] = activity
        signal_summary = build_signal_summary(activity or {})
        packages.append(
            {
                "package": manifest.package,
                "version": manifest.version,
                "description": manifest.description,
                "homepage": manifest.homepage,
                "github_repo": manifest.github_repo,
                "github_activity": activity,
                **signal_summary,
            }
        )

    strong_candidates = [
        {
            "package": package["package"],
            "version": package["version"],
            "reasons": package["reasons"],
            "github_repo": package["github_repo"],
        }
        for package in packages
        if package["should_archive"]
    ]

    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_repo": args.repo,
        "history": history,
        "recent_archive_pull_requests": fetch_recent_archive_prs(args.repo, token),
        "packages": packages,
        "strong_candidates": strong_candidates,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
