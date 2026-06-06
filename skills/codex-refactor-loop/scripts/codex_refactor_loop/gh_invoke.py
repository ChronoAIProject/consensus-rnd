"""Canonical GitHub CLI argv shaping for repo-scoped calls."""

from __future__ import annotations

from typing import Sequence


REPO_SCOPED_SUBCOMMANDS = frozenset({"pr", "issue"})


def build_gh_argv(repo_slug: str | None, argv: Sequence[str]) -> list[str]:
    """Return a valid gh argv with repo scope applied only where supported."""
    full = [str(part) for part in argv]
    if len(full) < 2 or full[0] != "gh" or not repo_slug or "--repo" in full:
        return full
    if full[1] not in REPO_SCOPED_SUBCOMMANDS:
        return full
    insert_at = 4 if len(full) > 3 and not full[3].startswith("-") else min(3, len(full))
    full[insert_at:insert_at] = ["--repo", repo_slug]
    return full


__all__ = ["REPO_SCOPED_SUBCOMMANDS", "build_gh_argv"]
