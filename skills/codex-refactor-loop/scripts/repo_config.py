# Refactor (iter4/issue17-hygiene): Old pattern: Python controllers independently read GH_OWNER/GH_REPO env. New principle: github_repo_slug() is the single source of truth, preferring GH_REPO_SLUG, falling back to owner+name, and otherwise failing by returning None.
"""Shared host repository configuration helpers."""

from __future__ import annotations

import os


def github_repo_slug() -> str | None:
    slug = os.environ.get("GH_REPO_SLUG")
    if slug:
        return slug
    repo = os.environ.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = os.environ.get("GH_OWNER")
    name = os.environ.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None
