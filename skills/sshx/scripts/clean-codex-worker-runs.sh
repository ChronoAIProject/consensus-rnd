#!/bin/bash
set -u

usage_error() { printf '%s\n' "clean-codex-worker-runs: USAGE_ERROR: $1" >&2; exit 64; }
internal_error() { printf '%s\n' "clean-codex-worker-runs: INTERNAL_ERROR: $1" >&2; exit 1; }
require_value() { [ "$2" -ge 2 ] || usage_error "missing value for $1"; }

manifest= delete_mode=0 seen_options='|'
while [ "$#" -gt 0 ]; do
  option=$1
  case "$option" in
    --manifest)
      require_value "$option" "$#"
      case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
      manifest=$2; seen_options="$seen_options$option|"; shift 2
      ;;
    --delete)
      case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
      delete_mode=1; seen_options="$seen_options$option|"; shift
      ;;
    --*) usage_error "unknown option $option" ;;
    *) usage_error "unexpected positional argument $option" ;;
  esac
done
case "$seen_options" in *'--manifest'*) ;; *) usage_error "missing --manifest" ;; esac
case "$manifest" in /*) ;; *) usage_error "--manifest must be an absolute path" ;; esac
case "$manifest" in *$'\n'*|*$'\r'*) usage_error "--manifest must not contain LF or CR" ;; esac
[ -f "$manifest" ] && [ ! -L "$manifest" ] || usage_error "--manifest must name a regular non-symlink file"

if ! jq_path=$(command -v jq 2>/dev/null) || [ ! -x "$jq_path" ]; then internal_error "jq is unavailable"; fi
if ! "$jq_path" -e -s '
  length == 1 and
  (.[0] | type) == "object" and
  (.[0] | keys) == ["schema_version", "workers"] and
  .[0].schema_version == 1 and
  (.[0].workers | type) == "array" and
  (.[0].workers | length) > 0 and
  (.[0].workers | all(
    type == "object" and
    ((keys) == ["attempt", "brief_ref", "flight_id", "stage", "work_target"] or
     (keys) == ["attempt", "brief_ref", "flight_id", "sandbox", "stage", "work_target"]) and
    (.flight_id | type) == "string" and
    (.flight_id | test("^[A-Za-z0-9._-]+$")) and
    .flight_id != "." and
    (.flight_id | contains("..") | not)
  ))
' "$manifest" >/dev/null 2>&1; then
  usage_error "invalid manifest"
fi
if ! "$jq_path" -e -s '
  .[0].workers | all(
    (.attempt | type) == "number" and
    (.attempt | tostring | test("^[1-9][0-9]*$"))
  )
' "$manifest" >/dev/null 2>&1; then
  usage_error "manifest attempt must project as a positive decimal integer"
fi
if ! "$jq_path" -e -s '.[0].workers | group_by([.flight_id, .attempt]) | all(length == 1)' "$manifest" >/dev/null 2>&1; then
  usage_error "invalid manifest"
fi

script_dir=${0%/*}
case "$script_dir" in "$0") script_dir=. ;; esac
runner="$script_dir/run-codex-worker.sh"
[ -f "$runner" ] && [ ! -L "$runner" ] || internal_error "runner is unavailable"

count_flight_entries() {
  if ! flight_entry_count=$(
    set -o pipefail
    find "$1" -exec /bin/sh -c 'for entry do printf x; done' sh {} + 2>/dev/null | wc -c
  ); then
    return 1
  fi
  flight_entry_count=${flight_entry_count//[[:space:]]/}
  case "$flight_entry_count" in ''|*[!0-9]*) return 1 ;; esac
}

check_flight_eligibility() {
  check_index=$1
  checked_eligible=true
  checked_reason=
  checked_projection=
  checked_flight_dir=
  checked_sshx_root=
  checked_entry_count=
  checked_internal_error=
  if ! checked_projection=$(bash "$runner" --project-flight --flight-id "${flight_ids[$check_index]}"); then
    checked_eligible=false
    checked_reason=OWNER_PROJECTION_UNAVAILABLE
    return
  fi
  if ! checked_flight_dir=$("$jq_path" -r '.flight_dir' <<<"$checked_projection"); then checked_internal_error="cannot read flight_dir"; return 1; fi
  if ! checked_sshx_root=$("$jq_path" -r '.sshx_root' <<<"$checked_projection"); then checked_internal_error="cannot read sshx_root"; return 1; fi

  case "$checked_flight_dir" in "$checked_sshx_root"/*) ;; *) checked_eligible=false; checked_reason=OWNER_PROJECTION_INCONSISTENT ;; esac
  if [ "$checked_eligible" = true ] && { [ ! -d "$checked_sshx_root" ] || [ -L "$checked_sshx_root" ]; }; then checked_eligible=false; checked_reason=SSHX_ROOT_UNAVAILABLE; fi
  if [ "$checked_eligible" = true ] && { [ ! -d "$checked_flight_dir" ] || [ -L "$checked_flight_dir" ]; }; then checked_eligible=false; checked_reason=FLIGHT_DIRECTORY_UNAVAILABLE; fi
  if [ "$checked_eligible" = true ]; then
    canonical_root=$(cd "$checked_sshx_root" 2>/dev/null && pwd -P) || { checked_eligible=false; checked_reason=SSHX_ROOT_UNAVAILABLE; }
  fi
  if [ "$checked_eligible" = true ]; then
    canonical_flight=$(cd "$checked_flight_dir" 2>/dev/null && pwd -P) || { checked_eligible=false; checked_reason=FLIGHT_DIRECTORY_UNAVAILABLE; }
  fi
  if [ "$checked_eligible" = true ]; then
    case "$canonical_flight" in "$canonical_root"/*) ;; *) checked_eligible=false; checked_reason=OWNER_PROJECTION_INCONSISTENT ;; esac
  fi
  if [ "$checked_eligible" = true ]; then
    if ! attempt_count=$("$jq_path" -r '.attempts | length' <<<"$checked_projection"); then checked_internal_error="cannot count projected attempts"; return 1; fi
    attempt_index=0
    while [ "$attempt_index" -lt "$attempt_count" ]; do
      if ! "$jq_path" -e --argjson i "$attempt_index" '.attempts[$i].attempt | type == "number" and floor == . and . > 0' <<<"$checked_projection" >/dev/null; then checked_eligible=false; checked_reason=INVALID_ATTEMPT_DIRECTORY; break; fi
      if ! attempt_dir=$("$jq_path" -r --argjson i "$attempt_index" '.attempts[$i].run_dir' <<<"$checked_projection"); then checked_internal_error="cannot read projected attempt directory"; return 1; fi
      if ! status_ref=$("$jq_path" -r --argjson i "$attempt_index" '.attempts[$i].status_ref' <<<"$checked_projection"); then checked_internal_error="cannot read projected terminal status reference"; return 1; fi
      case "$attempt_dir" in "$checked_flight_dir"/*) ;; *) checked_eligible=false; checked_reason=OWNER_PROJECTION_INCONSISTENT; break ;; esac
      case "$status_ref" in "$attempt_dir"/*) ;; *) checked_eligible=false; checked_reason=OWNER_PROJECTION_INCONSISTENT; break ;; esac
      if [ -L "$attempt_dir" ] || [ ! -d "$attempt_dir" ]; then checked_eligible=false; checked_reason=SYMLINKED_OR_INVALID_ATTEMPT; break; fi
      if [ ! -f "$status_ref" ] || [ -L "$status_ref" ]; then checked_eligible=false; checked_reason=TERMINAL_STATUS_MISSING; break; fi
      attempt_index=$((attempt_index + 1))
    done
    if [ "$checked_eligible" = true ] && [ "$attempt_count" -eq 0 ]; then checked_eligible=false; checked_reason=NO_ATTEMPT_DIRECTORIES; fi
  fi
  if [ "$checked_eligible" = true ]; then
    if count_flight_entries "$checked_flight_dir"; then checked_entry_count=$flight_entry_count; else checked_eligible=false; checked_reason=FLIGHT_SNAPSHOT_UNAVAILABLE; fi
  fi
  return 0
}

flight_count=$("$jq_path" -r '[.workers[].flight_id] | unique | length' "$manifest") || internal_error "cannot count flights"
flight_ids=(); flight_dirs=(); sshx_roots=(); eligible=(); reasons=(); preflight_projections=(); preflight_entry_counts=()
i=0
while [ "$i" -lt "$flight_count" ]; do
  flight_ids[$i]=$("$jq_path" -r --argjson i "$i" '[.workers[].flight_id] | unique | .[$i]' "$manifest") || internal_error "cannot read flight_id"
  check_flight_eligibility "$i" || internal_error "$checked_internal_error"
  flight_dirs[$i]=$checked_flight_dir
  sshx_roots[$i]=$checked_sshx_root
  eligible[$i]=$checked_eligible
  reasons[$i]=$checked_reason
  preflight_projections[$i]=$checked_projection
  preflight_entry_counts[$i]=$checked_entry_count
  i=$((i + 1))
done

all_eligible=true
flights_json='[]'
targets_json='[]'
i=0
while [ "$i" -lt "$flight_count" ]; do
  reason_json=null
  if [ "${eligible[$i]}" != true ]; then all_eligible=false; reason_json=$("$jq_path" --null-input --arg reason "${reasons[$i]}" '$reason'); fi
  flights_json=$("$jq_path" --compact-output --null-input --argjson flights "$flights_json" --arg flight_id "${flight_ids[$i]}" --arg flight_dir "${flight_dirs[$i]}" --argjson eligible "${eligible[$i]}" --argjson reason "$reason_json" '$flights + [{flight_id:$flight_id,flight_dir:$flight_dir,eligible:$eligible,reason:$reason}]') || internal_error "cannot render flight preflight"
  if [ "${eligible[$i]}" = true ]; then
    targets_json=$("$jq_path" --compact-output --null-input --argjson targets "$targets_json" --arg flight_dir "${flight_dirs[$i]}" '$targets + [$flight_dir]') || internal_error "cannot render targets"
  fi
  i=$((i + 1))
done

mode=dry-run; [ "$delete_mode" -eq 1 ] && mode=delete
if [ "$all_eligible" != true ]; then
  if [ "$delete_mode" -eq 1 ]; then
    ineligible_delete_flights=$("$jq_path" --compact-output --null-input --argjson flights "$flights_json" '$flights | map(. + {state:"untouched",failure_reason:null})') || internal_error "cannot render ineligible delete states"
    "$jq_path" --null-input --argjson schema_version 1 --argjson flights "$ineligible_delete_flights" '{schema_version:$schema_version,mode:"delete",all_eligible:false,flights:$flights,removed:[],failed:[]}' || internal_error "cannot render ineligible delete report"
  else
    "$jq_path" --null-input --argjson schema_version 1 --arg mode "$mode" --argjson flights "$flights_json" '{schema_version:$schema_version,mode:$mode,all_eligible:false,flights:$flights,removed:[]}' || internal_error "cannot render ineligible cleanup report"
  fi
  exit 1
fi
if [ "$delete_mode" -eq 0 ]; then
  "$jq_path" --null-input --argjson schema_version 1 --argjson flights "$flights_json" --argjson targets "$targets_json" '{schema_version:$schema_version,mode:"dry-run",all_eligible:true,flights:$flights,would_remove:$targets}' || internal_error "cannot render cleanup plan"
  exit 0
fi

delete_states=(); failure_reasons=()
i=0
while [ "$i" -lt "$flight_count" ]; do
  delete_states[$i]=untouched
  failure_reasons[$i]=
  i=$((i + 1))
done

json_quote() {
  local json_value=$1 json_character json_code json_escape json_index=0
  local LC_ALL=C
  json_quoted=
  while [ "$json_index" -lt "${#json_value}" ]; do
    json_character=${json_value:$json_index:1}
    case "$json_character" in
      '"') json_quoted="${json_quoted}\\\"" ;;
      '\') json_quoted="${json_quoted}\\\\" ;;
      $'\b') json_quoted="${json_quoted}\\b" ;;
      $'\f') json_quoted="${json_quoted}\\f" ;;
      $'\n') json_quoted="${json_quoted}\\n" ;;
      $'\r') json_quoted="${json_quoted}\\r" ;;
      $'\t') json_quoted="${json_quoted}\\t" ;;
      *)
        printf -v json_code '%d' "'$json_character"
        json_code=$((json_code & 0xff))
        if [ "$json_code" -lt 32 ]; then
          printf -v json_escape '\\u%04x' "$json_code"
          json_quoted="${json_quoted}${json_escape}"
        else
          json_quoted="${json_quoted}${json_character}"
        fi
        ;;
    esac
    json_index=$((json_index + 1))
  done
  json_quoted="\"${json_quoted}\""
}

build_delete_report_without_jq() {
  local failed_index=$1 report_index=0 separator= quoted_flight_id quoted_flight_dir quoted_state failure_reason_json
  fallback_document='{"schema_version":1,"mode":"delete","all_eligible":true,"flights":['
  while [ "$report_index" -lt "$flight_count" ]; do
    json_quote "${flight_ids[$report_index]}"; quoted_flight_id=$json_quoted
    json_quote "${flight_dirs[$report_index]}"; quoted_flight_dir=$json_quoted
    json_quote "${delete_states[$report_index]}"; quoted_state=$json_quoted
    failure_reason_json=null
    if [ -n "${failure_reasons[$report_index]}" ]; then json_quote "${failure_reasons[$report_index]}"; failure_reason_json=$json_quoted; fi
    fallback_document="${fallback_document}${separator}{\"flight_id\":${quoted_flight_id},\"flight_dir\":${quoted_flight_dir},\"eligible\":true,\"reason\":null,\"state\":${quoted_state},\"failure_reason\":${failure_reason_json}}"
    separator=,
    report_index=$((report_index + 1))
  done
  fallback_document="${fallback_document}],\"removed\":["
  report_index=0; separator=
  while [ "$report_index" -lt "$flight_count" ]; do
    if [ "${delete_states[$report_index]}" = removed ]; then
      json_quote "${flight_dirs[$report_index]}"
      fallback_document="${fallback_document}${separator}${json_quoted}"
      separator=,
    fi
    report_index=$((report_index + 1))
  done
  fallback_document="${fallback_document}],\"failed\":["
  if [ "$failed_index" -ge 0 ]; then
    json_quote "${flight_ids[$failed_index]}"; quoted_flight_id=$json_quoted
    json_quote "${flight_dirs[$failed_index]}"; quoted_flight_dir=$json_quoted
    json_quote "${delete_states[$failed_index]}"; quoted_state=$json_quoted
    json_quote "${failure_reasons[$failed_index]}"; failure_reason_json=$json_quoted
    fallback_document="${fallback_document}{\"flight_id\":${quoted_flight_id},\"flight_dir\":${quoted_flight_dir},\"state\":${quoted_state},\"failure_reason\":${failure_reason_json}}"
  fi
  fallback_document="${fallback_document}]}"
}

render_delete_report_with_jq() {
  local failed_index=$1 report_index=0 failure_reason_json=null
  local rendered_flights='[]' rendered_removed='[]' rendered_failed='[]'
  while [ "$report_index" -lt "$flight_count" ]; do
    failure_reason_json=null
    if [ -n "${failure_reasons[$report_index]}" ]; then
      failure_reason_json=$("$jq_path" --compact-output --null-input --arg failure_reason "${failure_reasons[$report_index]}" '$failure_reason') || return 1
    fi
    rendered_flights=$("$jq_path" --compact-output --null-input --argjson flights "$rendered_flights" --arg flight_id "${flight_ids[$report_index]}" --arg flight_dir "${flight_dirs[$report_index]}" --arg state "${delete_states[$report_index]}" --argjson failure_reason "$failure_reason_json" '$flights + [{flight_id:$flight_id,flight_dir:$flight_dir,eligible:true,reason:null,state:$state,failure_reason:$failure_reason}]') || return 1
    if [ "${delete_states[$report_index]}" = removed ]; then
      rendered_removed=$("$jq_path" --compact-output --null-input --argjson removed "$rendered_removed" --arg flight_dir "${flight_dirs[$report_index]}" '$removed + [$flight_dir]') || return 1
    fi
    report_index=$((report_index + 1))
  done
  if [ "$failed_index" -ge 0 ]; then
    rendered_failed=$("$jq_path" --compact-output --null-input --arg flight_id "${flight_ids[$failed_index]}" --arg flight_dir "${flight_dirs[$failed_index]}" --arg state "${delete_states[$failed_index]}" --arg failure_reason "${failure_reasons[$failed_index]}" '[{flight_id:$flight_id,flight_dir:$flight_dir,state:$state,failure_reason:$failure_reason}]') || return 1
  fi
  "$jq_path" --null-input --argjson schema_version 1 --argjson flights "$rendered_flights" --argjson removed "$rendered_removed" --argjson failed "$rendered_failed" '{schema_version:$schema_version,mode:"delete",all_eligible:true,flights:$flights,removed:$removed,failed:$failed}'
}

publish_delete_report() {
  if (printf '%s\n' "$1" 2>/dev/null); then return 0; fi
  printf '%s\n' "$1" >&2 || return 1
  return 1
}

report_delete_failure() {
  local failed_reason=$1 before_entry_count=$2 forced_state=${3:-} failed_state delete_report
  trap '' INT TERM
  if [ -n "$forced_state" ]; then
    failed_state=$forced_state
  elif [ ! -e "${flight_dirs[$i]}" ] && [ ! -L "${flight_dirs[$i]}" ]; then
    failed_state=removed
  elif [ -d "${flight_dirs[$i]}" ] && [ ! -L "${flight_dirs[$i]}" ] && count_flight_entries "${flight_dirs[$i]}" && [ "$flight_entry_count" = "$before_entry_count" ]; then
    failed_state=untouched
  else
    failed_state=partially-removed
  fi
  delete_states[$i]=$failed_state
  failure_reasons[$i]=$failed_reason
  if delete_report=$(render_delete_report_with_jq "$i") && [ -n "$delete_report" ]; then
    publish_delete_report "$delete_report"
  else
    build_delete_report_without_jq "$i"
    publish_delete_report "$fallback_document"
  fi
  exit 1
}

delete_interrupt=
record_delete_interrupt() {
  delete_interrupt=INTERRUPTED
  trap '' INT TERM
}
trap record_delete_interrupt INT TERM

i=0
while [ "$i" -lt "$flight_count" ]; do
  [ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "${preflight_entry_counts[$i]}" untouched
  if ! check_flight_eligibility "$i"; then
    printf '%s\n' "clean-codex-worker-runs: INTERNAL_ERROR: $checked_internal_error" >&2
    report_delete_failure INTERNAL_ERROR "${preflight_entry_counts[$i]}" untouched
  fi
  [ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "${preflight_entry_counts[$i]}" untouched
  [ "$checked_eligible" = true ] || report_delete_failure "$checked_reason" "${preflight_entry_counts[$i]}" untouched
  [ "$checked_flight_dir" = "${flight_dirs[$i]}" ] && [ "$checked_sshx_root" = "${sshx_roots[$i]}" ] || report_delete_failure OWNER_PROJECTION_INCONSISTENT "${preflight_entry_counts[$i]}" untouched
  "$jq_path" -e --null-input --argjson before "${preflight_projections[$i]}" --argjson after "$checked_projection" '$before == $after' >/dev/null
  projection_compare_rc=$?
  case "$projection_compare_rc" in
    0) ;;
    1) report_delete_failure FLIGHT_CHANGED "${preflight_entry_counts[$i]}" untouched ;;
    *) report_delete_failure PROJECTION_COMPARE_FAILED "${preflight_entry_counts[$i]}" untouched ;;
  esac
  [ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "${preflight_entry_counts[$i]}" untouched
  removal_entry_count=$checked_entry_count
  rm -rf -- "${flight_dirs[$i]}"
  removal_rc=$?
  [ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "$removal_entry_count"
  [ "$removal_rc" -eq 0 ] || report_delete_failure REMOVE_FAILED "$removal_entry_count"
  [ ! -e "${flight_dirs[$i]}" ] && [ ! -L "${flight_dirs[$i]}" ] || report_delete_failure FLIGHT_REMAINS "$removal_entry_count"
  delete_states[$i]=removed
  failure_reasons[$i]=
  [ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "$removal_entry_count" removed
  i=$((i + 1))
done

i=$((flight_count - 1))
[ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "${preflight_entry_counts[$i]}" "${delete_states[$i]}"
trap '' INT TERM
[ -z "$delete_interrupt" ] || report_delete_failure "$delete_interrupt" "${preflight_entry_counts[$i]}" "${delete_states[$i]}"
if delete_report=$(render_delete_report_with_jq -1) && [ -n "$delete_report" ]; then
  publish_delete_report "$delete_report" || exit 1
else
  report_delete_failure REPORT_RENDER_FAILED "${preflight_entry_counts[$i]}" "${delete_states[$i]}"
fi
