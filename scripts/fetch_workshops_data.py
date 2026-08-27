#!/usr/bin/env python3
"""Fetch workshop catalog data at build time and write it to a local JSON file."""

# Add new partner repositories to the MANUAL_PARTNER_REPO_LIST constant below in the org/repo format. The list of main workshops is fetched automatically from the c3g GitHub organization.

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime, timezone

ORG = "c3g"
PREFIX = "c3gW"
WORKSHOP_TOPIC = "workshop"

SUGGESTED_CATEGORY_TAGS = [
    "cancer",
    "rna-seq",
    "chip-seq",
    "metagenomics",
    "variant-calling",
    "single-cell",
    "epigenomics",
    "proteomics",
    "genome-assembly",
    "clinical",
]

MANUAL_PARTNER_REPO_LIST = [
    "QLS-MiCM/Introduction-to-RNA-seq",
    "QLS-MiCM/RNA-seq-Data-Processing",
]

MANUAL_PARTNER_TOPIC_OVERRIDES = {
    "QLS-MiCM/Introduction-to-RNA-seq": ["workshop", "rna-seq"],
    "QLS-MiCM/RNA-seq-Data-Processing": ["workshop", "rna-seq"],
}


def github_get(path: str) -> Any:
    base_url = "https://api.github.com"
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "c3g-workshop-catalog-build",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(f"{base_url}{path}", headers=headers)

    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"GitHub API error {exc.code} for {path}: {details}") from exc


def normalize_topics(topics: Any) -> List[str]:
    if not isinstance(topics, list):
        return []
    normalized = []
    seen = set()
    for topic in topics:
        value = str(topic).strip().lower()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def normalize_repo(repo: Dict[str, Any], topics: List[str] = None) -> Dict[str, Any]:
    repo_topics = normalize_topics(topics if topics is not None else repo.get("topics", []))
    return {
        "name": repo.get("name", ""),
        "full_name": repo.get("full_name", repo.get("name", "")),
        "description": repo.get("description") or "",
        "homepage": repo.get("homepage") or "",
        "html_url": repo.get("html_url", ""),
        "pushed_at": repo.get("pushed_at"),
        "topics": repo_topics,
    }


def merge_topics(*topic_lists: List[str]) -> List[str]:
    merged = []
    seen = set()
    for topic_list in topic_lists:
        for topic in normalize_topics(topic_list):
            if topic not in seen:
                merged.append(topic)
                seen.add(topic)
    return merged


def fetch_repo_topics(owner: str, repo_name: str) -> List[str]:
    payload = github_get(f"/repos/{owner}/{repo_name}/topics")
    return normalize_topics(payload.get("names", []))


def fetch_org_repos() -> List[Dict[str, Any]]:
    repos: List[Dict[str, Any]] = []
    page = 1
    while True:
        payload = github_get(f"/orgs/{ORG}/repos?type=public&per_page=100&page={page}&sort=full_name")
        if not isinstance(payload, list):
            break
        repos.extend(payload)
        if len(payload) < 100:
            break
        page += 1
    return repos


def fetch_main_workshops() -> List[Dict[str, Any]]:
    query = f"org:{ORG} {PREFIX} in:name topic:{WORKSHOP_TOPIC}"
    encoded = urllib.parse.quote(query)

    try:
        payload = github_get(f"/search/repositories?q={encoded}&sort=full_name&order=asc&per_page=100")
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if items:
            workshops = []
            for repo in items:
                topics = normalize_topics(repo.get("topics", []))
                if not topics:
                    topics = fetch_repo_topics(ORG, repo.get("name", ""))
                normalized = normalize_repo(repo, topics)
                if normalized["name"].lower().startswith(PREFIX.lower()) and WORKSHOP_TOPIC in normalized["topics"]:
                    workshops.append(normalized)
            return sorted(workshops, key=lambda item: item["name"].lower())
    except Exception as exc:
        print(f"Search API failed, using org repo fallback: {exc}", file=sys.stderr)

    org_repos = fetch_org_repos()
    workshops = []
    for repo in org_repos:
        name = str(repo.get("name", ""))
        if not name.lower().startswith(PREFIX.lower()):
            continue
        topics = fetch_repo_topics(ORG, name)
        normalized = normalize_repo(repo, topics)
        if WORKSHOP_TOPIC in normalized["topics"]:
            workshops.append(normalized)

    return sorted(workshops, key=lambda item: item["name"].lower())


def fetch_partner_workshops() -> List[Dict[str, Any]]:
    workshops = []
    for entry in MANUAL_PARTNER_REPO_LIST:
        full_name = str(entry).strip()
        if "/" not in full_name:
            continue
        owner, repo_name = full_name.split("/", 1)
        repo = github_get(f"/repos/{owner}/{repo_name}")
        topics = merge_topics(
            fetch_repo_topics(owner, repo_name),
            MANUAL_PARTNER_TOPIC_OVERRIDES.get(full_name, []),
        )
        workshops.append(normalize_repo(repo, topics))
    return sorted(workshops, key=lambda item: item["name"].lower())


def main() -> int:
    out_path = Path("data/workshops-data.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": {
                "organization": ORG,
                "prefix": PREFIX,
                "requiredTopic": WORKSHOP_TOPIC,
                "manualPartnerRepoList": MANUAL_PARTNER_REPO_LIST,
                "suggestedCategoryTags": SUGGESTED_CATEGORY_TAGS,
            },
            "suggestedCategoryTags": SUGGESTED_CATEGORY_TAGS,
            "mainWorkshops": fetch_main_workshops(),
            "partnerWorkshops": fetch_partner_workshops(),
        }

        out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(
            "Wrote data/workshops-data.json "
            f"(main={len(output['mainWorkshops'])}, partner={len(output['partnerWorkshops'])})"
        )
    except Exception as err:
        if out_path.exists():
            print(
                "Warning: unable to refresh workshops data from GitHub; "
                "reusing existing data/workshops-data.json. "
                f"Reason: {err}",
                file=sys.stderr,
            )
            return 0
        raise

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print(f"Failed to fetch workshop data: {err}", file=sys.stderr)
        raise SystemExit(1)
