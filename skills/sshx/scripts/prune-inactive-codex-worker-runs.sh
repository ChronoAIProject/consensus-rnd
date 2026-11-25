#!/bin/bash
set -u

removed_paths=
usage_error() { printf '%s\n' "prune-inactive-codex-worker-runs: USAGE_ERROR: $1" >&2; exit 64; }
internal_error() {
  printf '%s\n' "prune-inactive-codex-worker-runs: INTERNAL_ERROR: $1" >&2
  [ -z "$removed_paths" ] || printf 'prune-inactive-codex-worker-runs: removed before the failure:\n%s' "$removed_paths" >&2
  exit 1
}
require_value() { [ "$2" -ge 2 ] || usage_error "missing value for $1"; }

load_layout() {
  script_dir=${0%/*}
  [ "$script_dir" != "$0" ] || script_dir=.
  runner="$script_dir/run-codex-worker.sh"
  [ -f "$runner" ] && [ ! -L "$runner" ] || internal_error "runner is unavailable"
  # shellcheck source=skills/sshx/scripts/run-codex-worker.sh
  . "$runner" || internal_error "cannot load the run layout"
  normalize_run_root || internal_error "cannot project the run root"
}

completion_sweep() {
  load_layout
  valid_flight_id "$1" || usage_error "invalid completion flight"
  exec 9< "$run_root" || internal_error "cannot open the run root"
  exec python3 - "$run_root" "$1" "$flight_id_pattern" "$attempt_prefix" "$status_filename" "$2" <<'PYTHON'
import fcntl
import os
import re
import stat
import subprocess
import sys
import time


RETIRING_PREFIX = ".sshx-retiring-"


def retiring_name(flight_id: str, entry: os.stat_result) -> str:
    return f"{RETIRING_PREFIX}{flight_id}-{entry.st_dev}-{entry.st_ino}"


def has_terminal_attempts(path: str, prefix: str, status_name: str) -> bool:
    seen = False
    with os.scandir(path) as entries:
        for entry in entries:
            if not entry.name.startswith(prefix):
                continue
            if not re.fullmatch(r"[1-9][0-9]*", entry.name[len(prefix):]):
                return False
            if not entry.is_dir(follow_symlinks=False):
                return False
            try:
                status = os.lstat(os.path.join(entry.path, status_name))
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(status.st_mode):
                return False
            seen = True
    return seen


def sweep(root: str, emitting_flight: str, flight_pattern: str, prefix: str,
          status_name: str, minutes: str) -> int:
    cutoff = time.time() - int(minutes) * 60
    flight_id = re.compile(flight_pattern)
    failures = 0
    first_failure = None
    with os.scandir(root) as entries:
        flights = [
            flight for flight in entries
            if flight.name.startswith(RETIRING_PREFIX) or flight_id.fullmatch(flight.name)
        ]
    for flight in flights:
        retiring = flight.name.startswith(RETIRING_PREFIX)
        identity = flight.name[len(RETIRING_PREFIX):].partition("-")[0] if retiring else flight.name
        if identity == emitting_flight or not flight_id.fullmatch(identity):
            continue
        try:
            entry = flight.stat(follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                continue
            name = retiring_name(identity, entry)
            if retiring:
                if flight.name != name:
                    continue
                target = flight.path
            else:
                if entry.st_mtime > cutoff or not has_terminal_attempts(flight.path, prefix, status_name):
                    continue
                activity = subprocess.run(
                    ["find", flight.path, "-mmin", "-" + minutes, "-print", "-quit"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
                )
                if activity.stdout:
                    continue
                # Commit eligibility outside the claim namespace before rm can erase it.
                target = os.path.join(root, name)
                if os.path.lexists(target):
                    raise FileExistsError(f"retirement destination already exists: {target}")
                os.rename(flight.path, target)
            # A live rm keeps the lock even if this maintainer is killed.
            subprocess.run(
                ["rm", "-rf", "--", target],
                stderr=subprocess.DEVNULL, pass_fds=(9,), check=True,
            )
            if os.path.lexists(target):
                raise OSError("flight remains after removal")
        except (OSError, subprocess.CalledProcessError) as error:
            failures += 1
            if first_failure is None:
                first_failure = f"{flight.name}: {error}"
    if failures:
        print(f"prune-inactive-codex-worker-runs: completion sweep failed for "
              f"{failures} flight(s); first failure: {first_failure}", file=sys.stderr)
    return int(failures != 0)


try:
    fcntl.flock(9, fcntl.LOCK_EX)
    sys.exit(sweep(*sys.argv[1:]))
except OSError as error:
    sys.exit(f"prune-inactive-codex-worker-runs: cannot lock or scan the run root: {error}")
PYTHON
}

default_days=1
if [ "${1:-}" = --on-completion ]; then
  [ "$#" -eq 2 ] || usage_error "expected one completion flight"
  completion_sweep "$2" "$((default_days * 1440))"
  exit 1
fi

older_than=${default_days}d dry_run=0 seen_options='|'
while [ "$#" -gt 0 ]; do
  option=$1
  case "$option" in
    --older-than)
      require_value "$option" "$#"
      case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
      older_than=$2; seen_options="$seen_options$option|"; shift 2
      ;;
    --dry-run)
      case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
      dry_run=1; seen_options="$seen_options$option|"; shift
      ;;
    --*) usage_error "unknown option $option" ;;
    *) usage_error "unexpected positional argument $option" ;;
  esac
done

duration_value=${older_than%?}
duration_unit=${older_than#"$duration_value"}
case "$duration_value" in ''|*[!0-9]*) usage_error "--older-than must be <positive-integer><m|h|d>" ;; esac
while [ "${duration_value#0}" != "$duration_value" ]; do duration_value=${duration_value#0}; done
[ -n "$duration_value" ] || usage_error "--older-than must be a positive duration"
case "$duration_unit" in
  m) older_than_minutes=$duration_value ;;
  h) older_than_minutes=$((duration_value * 60)) ;;
  d) older_than_minutes=$((duration_value * 1440)) ;;
  *) usage_error "--older-than must be <positive-integer><m|h|d>" ;;
esac

if ! jq_path=$(command -v jq 2>/dev/null) || [ ! -x "$jq_path" ]; then internal_error "jq is unavailable"; fi
load_layout

sshx_root=$run_root root_present=false flight_count=0 unrecognized_json='[]'
if [ "$dry_run" -eq 1 ] || [ -d "$run_root" ]; then
  if [ "$dry_run" -eq 0 ]; then
    lock_run_root "$run_root" || internal_error "cannot lock the run root"
  fi
  root_projection=$(bash "$runner" --project-root) || internal_error "cannot project the run root"
  sshx_root=$("$jq_path" -r '.sshx_root' <<<"$root_projection") || internal_error "cannot read sshx_root"
  root_present=$("$jq_path" -r '.root_present' <<<"$root_projection") || internal_error "cannot read root_present"
  case "$root_present" in true|false) ;; *) internal_error "invalid root_present" ;; esac
  flight_count=$("$jq_path" -r '.flights | length' <<<"$root_projection") || internal_error "cannot count flights"
  case "$flight_count" in ''|*[!0-9]*) internal_error "invalid flight count" ;; esac
  unrecognized_json=$("$jq_path" --compact-output '.unrecognized' <<<"$root_projection") || internal_error "cannot read unrecognized entries"
fi

mode=delete; [ "$dry_run" -eq 0 ] || mode=dry-run
flights_json='[]' removed_json='[]' failed_json='[]' interrupted=false failure_seen=0

record_flight() {
  record_id=$1 record_dir=$2 record_inactive=$3 record_state=$4 record_reason=${5:-}
  reason_json=null
  if [ -n "$record_reason" ]; then
    reason_json=$("$jq_path" --compact-output --null-input --arg reason "$record_reason" '$reason') || internal_error "cannot render reason"
  fi
  flights_json=$("$jq_path" --compact-output --null-input --argjson flights "$flights_json" --arg flight_id "$record_id" --arg flight_dir "$record_dir" --argjson inactive "$record_inactive" --arg state "$record_state" --argjson reason "$reason_json" '$flights + [{flight_id:$flight_id,flight_dir:$flight_dir,inactive:$inactive,state:$state,reason:$reason}]') || internal_error "cannot render flight record"
  case "$record_state" in
    removed)
      removed_json=$("$jq_path" --compact-output --null-input --argjson removed "$removed_json" --arg flight_dir "$record_dir" '$removed + [$flight_dir]') || internal_error "cannot render removed list"
      ;;
    failed)
      failure_seen=1
      failed_json=$("$jq_path" --compact-output --null-input --argjson failed "$failed_json" --arg flight_id "$record_id" --arg flight_dir "$record_dir" --argjson reason "$reason_json" '$failed + [{flight_id:$flight_id,flight_dir:$flight_dir,reason:$reason}]') || internal_error "cannot render failed list"
      ;;
  esac
}

record_interrupt() { interrupted=true; trap '' INT TERM; }
trap record_interrupt INT TERM

i=0
while [ "$i" -lt "$flight_count" ]; do
  [ "$interrupted" = false ] || break
  flight_id=$("$jq_path" -r --argjson i "$i" '.flights[$i].flight_id' <<<"$root_projection") || internal_error "cannot read flight_id"
  flight_dir=$("$jq_path" -r --argjson i "$i" '.flights[$i].flight_dir' <<<"$root_projection") || internal_error "cannot read flight_dir"
  i=$((i + 1))
  case "$flight_dir" in "$sshx_root"/*) ;; *) record_flight "$flight_id" "$flight_dir" null skipped OWNER_PROJECTION_INCONSISTENT; continue ;; esac
  if [ -L "$flight_dir" ] || [ ! -d "$flight_dir" ]; then record_flight "$flight_id" "$flight_dir" null skipped INVALID_FLIGHT_DIRECTORY; continue; fi
  if ! recent_entry=$(find "$flight_dir" -mmin -"$older_than_minutes" -print -quit 2>/dev/null); then record_flight "$flight_id" "$flight_dir" null skipped ACTIVITY_PROBE_FAILED; continue; fi
  if [ -n "$recent_entry" ]; then record_flight "$flight_id" "$flight_dir" false kept; continue; fi
  if [ "$dry_run" -eq 1 ]; then record_flight "$flight_id" "$flight_dir" true would-remove; continue; fi
  rm -rf -- "$flight_dir"
  removal_rc=$?
  if [ "$removal_rc" -ne 0 ]; then
    record_flight "$flight_id" "$flight_dir" true failed REMOVE_FAILED
  elif [ -e "$flight_dir" ] || [ -L "$flight_dir" ]; then
    record_flight "$flight_id" "$flight_dir" true failed FLIGHT_REMAINS
  else
    removed_paths="$removed_paths$flight_dir"$'\n'
    record_flight "$flight_id" "$flight_dir" true removed
  fi
done
trap '' INT TERM

report=$("$jq_path" --null-input --argjson schema_version 1 --arg mode "$mode" --arg sshx_root "$sshx_root" --argjson root_present "$root_present" --argjson older_than_minutes "$older_than_minutes" --argjson interrupted "$interrupted" --argjson flights "$flights_json" --argjson unrecognized "$unrecognized_json" --argjson removed "$removed_json" --argjson failed "$failed_json" '{schema_version:$schema_version,mode:$mode,sshx_root:$sshx_root,root_present:$root_present,older_than_minutes:$older_than_minutes,interrupted:$interrupted,flights:$flights,unrecognized:$unrecognized,removed:$removed,failed:$failed}') || internal_error "cannot render report"
printf '%s\n' "$report" || internal_error "cannot publish report"
[ "$interrupted" = false ] && [ "$failure_seen" -eq 0 ] || exit 1
exit 0
