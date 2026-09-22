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

older_than=1d dry_run=0 seen_options='|'
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
script_dir=${0%/*}
case "$script_dir" in "$0") script_dir=. ;; esac
runner="$script_dir/run-codex-worker.sh"
[ -f "$runner" ] && [ ! -L "$runner" ] || internal_error "runner is unavailable"

root_projection=$(bash "$runner" --project-root) || internal_error "cannot project the run root"
sshx_root=$("$jq_path" -r '.sshx_root' <<<"$root_projection") || internal_error "cannot read sshx_root"
root_present=$("$jq_path" -r '.root_present' <<<"$root_projection") || internal_error "cannot read root_present"
case "$root_present" in true|false) ;; *) internal_error "invalid root_present" ;; esac
flight_count=$("$jq_path" -r '.flights | length' <<<"$root_projection") || internal_error "cannot count flights"
case "$flight_count" in ''|*[!0-9]*) internal_error "invalid flight count" ;; esac
unrecognized_json=$("$jq_path" --compact-output '.unrecognized' <<<"$root_projection") || internal_error "cannot read unrecognized entries"

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
