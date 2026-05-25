#!/usr/bin/env bash
# Shared GitHub repository slug helpers for shell scripts.
#
# ⟦AI:AUTO-LOOP⟧

resolve_github_repo_slug() {
  local allow_gh_view="${1:-0}" require_slug="${2:-0}" slug
  slug="${GH_REPO_SLUG:-${GH_OWNER:+$GH_OWNER/}${GH_REPO_NAME:-${GH_REPO:-}}}"
  if [ -n "${slug:-}" ] && ! [[ "$slug" == */* ]]; then
    echo "FATAL: GH_REPO_SLUG must be OWNER/REPO; got '$slug'" >&2
    return 2
  fi
  if [ -z "${slug:-}" ] && [ "$allow_gh_view" = "1" ]; then
    slug="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  fi
  if [ -z "${slug:-}" ] && [ "$require_slug" = "1" ]; then
    echo "FATAL: GH_REPO_SLUG is unset and gh repo view failed" >&2
    return 2
  fi
  printf '%s\n' "$slug"
}

set_gh_repo_args() {
  local slug
  slug="$(resolve_github_repo_slug "${1:-0}" "${2:-0}")" || return $?
  GH_REPO_SLUG="$slug"
  gh_repo_args=()
  [ -n "${GH_REPO_SLUG:-}" ] && gh_repo_args=(--repo "$GH_REPO_SLUG")
  export GH_REPO_SLUG
}
