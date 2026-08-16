#!/bin/bash
set -u
umask 077
reason=INTERNAL_ERROR
code=1
carrier_exit=null
started_at=; started_epoch=; finished_at=; finished_epoch=; duration_seconds=
run_dir=
run_dir_owned=0
result_valid=0; jq_path=
verdict=

regular_or_absent() { [ ! -e "$1" ] && [ ! -L "$1" ] || { [ -f "$1" ] && [ ! -L "$1" ]; }; }
log_time() { log_timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || log_timestamp=; case "$log_timestamp" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) printf '%s\n' "$log_timestamp" ;; *) printf '%s\n' '1970-01-01T00:00:00Z' ;; esac; }
log_field() { log_timestamp=$(log_time); (printf '%s  %-13s %s\n' "$log_timestamp" "$1" "$2") || :; }
log_event() { log_timestamp=$(log_time); (printf '%s  %s\n' "$log_timestamp" "$1") || :; }

finish() {
  trap - EXIT
  trap '' INT TERM
  if [ "$run_dir_owned" -eq 1 ]; then
    if finished_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null); then :; else finished_at=; fi
    if finished_epoch=$(date -u '+%s' 2>/dev/null); then case "$finished_epoch" in ''|*[!0-9]*) finished_epoch= ;; esac; else finished_epoch=; fi
    if [ -n "$started_epoch" ] && [ -n "$finished_epoch" ] && [ "$finished_epoch" -ge "$started_epoch" ]; then duration_seconds=$((finished_epoch - started_epoch)); else duration_seconds=; fi
    terminal_status=NOT_COMPLETE
    [ "$reason" = COMPLETE ] && terminal_status=COMPLETE
    status_tmp="$status_ref.tmp"
    status_target_valid=1
    if ! regular_or_absent "$status_ref" || ! regular_or_absent "$status_tmp"; then
      reason=INTERNAL_ERROR; code=1
      printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: invalid status target' >&2
      status_target_valid=0
    elif [ "$result_valid" -eq 1 ]; then
      "$jq_path" -n --argjson schema_version 1 --arg flight_id "$flight_id" --argjson attempt "$attempt" --arg stage "$stage" --arg status "$terminal_status" --arg reason_code "$reason" --arg carrier_exit "$carrier_exit" --arg run_dir "$run_dir" --arg result_ref "$result_ref" --arg sentinel_ref "$sentinel_ref" --arg stdout_ref "$stdout_ref" --arg stderr_ref "$stderr_ref" --arg last_message_ref "$last_message_ref" --arg started_at "$started_at" --arg finished_at "$finished_at" --arg duration_seconds "$duration_seconds" --arg work_target "$work_target" --arg sandbox "$sandbox" --arg brief_ref "$brief_ref" --slurpfile result "$result_ref" '{schema_version:$schema_version,flight_id:$flight_id,attempt:$attempt,stage:$stage,status:$status,reason_code:$reason_code,carrier_exit:(if $carrier_exit=="null" then null else ($carrier_exit|tonumber) end),run_dir:$run_dir,result_ref:$result_ref,completion_sentinel_ref:$sentinel_ref,log_refs:{stdout:$stdout_ref,stderr:$stderr_ref,last_message:$last_message_ref},verdict:$result[0].conclusion.verdict,started_at:(if $started_at=="" then null else $started_at end),finished_at:(if $finished_at=="" then null else $finished_at end),duration_seconds:(if $duration_seconds=="" then null else ($duration_seconds|tonumber) end),work_target:$work_target,sandbox:$sandbox,brief_ref:$brief_ref}' > "$status_tmp"
    else
      "$jq_path" -n --argjson schema_version 1 --arg flight_id "$flight_id" --argjson attempt "$attempt" --arg stage "$stage" --arg status "$terminal_status" --arg reason_code "$reason" --arg carrier_exit "$carrier_exit" --arg run_dir "$run_dir" --arg result_ref "$result_ref" --arg sentinel_ref "$sentinel_ref" --arg stdout_ref "$stdout_ref" --arg stderr_ref "$stderr_ref" --arg last_message_ref "$last_message_ref" --arg started_at "$started_at" --arg finished_at "$finished_at" --arg duration_seconds "$duration_seconds" --arg work_target "$work_target" --arg sandbox "$sandbox" --arg brief_ref "$brief_ref" '{schema_version:$schema_version,flight_id:$flight_id,attempt:$attempt,stage:$stage,status:$status,reason_code:$reason_code,carrier_exit:(if $carrier_exit=="null" then null else ($carrier_exit|tonumber) end),run_dir:$run_dir,result_ref:$result_ref,completion_sentinel_ref:$sentinel_ref,log_refs:{stdout:$stdout_ref,stderr:$stderr_ref,last_message:$last_message_ref},started_at:(if $started_at=="" then null else $started_at end),finished_at:(if $finished_at=="" then null else $finished_at end),duration_seconds:(if $duration_seconds=="" then null else ($duration_seconds|tonumber) end),work_target:$work_target,sandbox:$sandbox,brief_ref:$brief_ref}' > "$status_tmp"
    fi
    render_rc=$?; render_ok=0; mv_rc=0
    [ "$render_rc" -eq 0 ] && [ -s "$status_tmp" ] && render_ok=1
    if [ "$status_target_valid" -eq 1 ] && [ "$render_ok" -eq 0 ]; then
      reason=INTERNAL_ERROR; code=1
      printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: cannot render status' >&2
      "$jq_path" -n --argjson schema_version 1 --arg flight_id "$flight_id" --argjson attempt "$attempt" --arg stage "$stage" --arg reason_code INTERNAL_ERROR --arg carrier_exit "$carrier_exit" --arg run_dir "$run_dir" --arg result_ref "$result_ref" --arg sentinel_ref "$sentinel_ref" --arg stdout_ref "$stdout_ref" --arg stderr_ref "$stderr_ref" --arg last_message_ref "$last_message_ref" --arg started_at "$started_at" --arg finished_at "$finished_at" --arg duration_seconds "$duration_seconds" --arg work_target "$work_target" --arg sandbox "$sandbox" --arg brief_ref "$brief_ref" '{schema_version:$schema_version,flight_id:$flight_id,attempt:$attempt,stage:$stage,status:"NOT_COMPLETE",reason_code:$reason_code,carrier_exit:(if $carrier_exit=="null" then null else ($carrier_exit|tonumber) end),run_dir:$run_dir,result_ref:$result_ref,completion_sentinel_ref:$sentinel_ref,log_refs:{stdout:$stdout_ref,stderr:$stderr_ref,last_message:$last_message_ref},started_at:(if $started_at=="" then null else $started_at end),finished_at:(if $finished_at=="" then null else $finished_at end),duration_seconds:(if $duration_seconds=="" then null else ($duration_seconds|tonumber) end),work_target:$work_target,sandbox:$sandbox,brief_ref:$brief_ref}' > "$status_tmp"
      fallback_rc=$?
      if [ "$fallback_rc" -eq 0 ] && [ -s "$status_tmp" ]; then render_ok=1; else
        printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: cannot render failure status' >&2
      fi
    fi
    [ "$status_target_valid" -eq 1 ] && [ "$render_ok" -eq 1 ] && { mv -f "$status_tmp" "$status_ref" || mv_rc=1; }
    [ "$status_target_valid" -eq 0 ] && [ -f "$status_ref" ] && [ ! -L "$status_ref" ] && rm -f "$status_ref"
    if [ "$status_target_valid" -eq 1 ]; then
      if [ "$render_ok" -eq 0 ] || [ "$mv_rc" -ne 0 ] || [ ! -f "$status_ref" ] || [ -L "$status_ref" ]; then reason=INTERNAL_ERROR; code=1; printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: cannot commit status' >&2; [ -f "$status_ref" ] && [ ! -L "$status_ref" ] && rm -f "$status_ref"; else rm -f "$status_tmp"; fi
    fi
    final_status=NOT_COMPLETE; [ "$reason" = COMPLETE ] && final_status=COMPLETE
    verdict_log=${verdict:-unknown}; [ "$stage" = implementation ] && verdict_log=n/a
    duration_log=unknown; [ -n "$duration_seconds" ] && duration_log="${duration_seconds}s"
    log_field status "$final_status"
    log_field reason_code "$reason"
    log_field verdict "$verdict_log"
    log_field duration "$duration_log"
  else
    printf '%s\n' "run-codex-worker: $reason" >&2
  fi
  exit "$code"
}
trap finish EXIT

interrupt() { reason=INTERRUPTED; code=1; finish; }
trap interrupt INT TERM
usage_error() { reason=USAGE_ERROR; code=64; printf '%s\n' "run-codex-worker: USAGE_ERROR: $1" >&2; return 1; }
require_value() { [ "$2" -ge 2 ] || usage_error "missing value for $1"; }
positive_integer() {
  integer_value=$1
  case "$integer_value" in ''|*[!0-9]*) return 1 ;; esac
  while [ "${integer_value#0}" != "$integer_value" ]; do integer_value=${integer_value#0}; done
  [ -n "$integer_value" ]
}
normalize_tmp_base() {
  tmp_base=${TMPDIR:-/tmp}
  while [ "$tmp_base" != / ] && [ "${tmp_base%/}" != "$tmp_base" ]; do tmp_base=${tmp_base%/}; done
  case "$tmp_base" in /*) ;; *) reason=RUN_DIR_UNAVAILABLE; return 1 ;; esac
  case "$tmp_base" in *$'\n'*|*$'\r'*) reason=RUN_DIR_UNAVAILABLE; return 1 ;; esac
}
derive_project_paths() {
  consensus_root="$tmp_base/consensus-rnd"
  sshx_root="$consensus_root/sshx"
  run_parent="$sshx_root/$flight_id"
  run_dir="$run_parent/attempt-$attempt"
  brief_ref="$run_dir/brief.md"; stdout_ref="$run_dir/worker.stdout.log"; stderr_ref="$run_dir/worker.stderr.log"
  last_message_ref="$run_dir/last-message.txt"; result_ref="$run_dir/result.json"; sentinel_ref="$run_dir/completion.sentinel"
  carrier_exit_ref="$run_dir/carrier.exit"; status_ref="$run_dir/status.json"
}
emit_project_paths() {
  if ! projection_json=$("$jq_path" --compact-output --null-input --argjson schema_version 1 --arg flight_id "$flight_id" --argjson attempt "$attempt" --arg run_dir "$run_dir" --arg brief_ref "$brief_ref" --arg result_ref "$result_ref" --arg sentinel_ref "$sentinel_ref" --arg carrier_exit_ref "$carrier_exit_ref" --arg status_ref "$status_ref" --arg stdout_ref "$stdout_ref" --arg stderr_ref "$stderr_ref" --arg last_message_ref "$last_message_ref" '{schema_version:$schema_version,flight_id:$flight_id,attempt:$attempt,run_dir:$run_dir,brief_ref:$brief_ref,result_ref:$result_ref,completion_sentinel_ref:$sentinel_ref,carrier_exit_ref:$carrier_exit_ref,status_ref:$status_ref,log_refs:{stdout:$stdout_ref,stderr:$stderr_ref,last_message:$last_message_ref}}'); then
    reason=INTERNAL_ERROR
    return 1
  fi
  [ -n "$projection_json" ] || { reason=INTERNAL_ERROR; return 1; }
  printf '%s\n' "$projection_json" || { reason=INTERNAL_ERROR; return 1; }
}
emit_project_flight() {
  attempts_json='[]'
  shopt -s nullglob
  for projected_run_dir in "$run_parent"/attempt-*; do
    attempt_name=${projected_run_dir##*/}
    projected_attempt=${attempt_name#attempt-}
    projected_attempt_json=null
    case "$projected_attempt" in ''|0|0*|*[!0-9]*) ;; *) projected_attempt_json=$projected_attempt ;; esac
    attempt=$projected_attempt
    derive_project_paths
    if ! attempts_json=$("$jq_path" --compact-output --null-input --argjson attempts "$attempts_json" --argjson attempt "$projected_attempt_json" --arg run_dir "$run_dir" --arg status_ref "$status_ref" '$attempts + [{attempt:$attempt,run_dir:$run_dir,status_ref:$status_ref}]'); then
      reason=INTERNAL_ERROR
      return 1
    fi
  done
  shopt -u nullglob
  if ! projection_json=$("$jq_path" --compact-output --null-input --argjson schema_version 1 --arg flight_id "$flight_id" --arg flight_dir "$run_parent" --arg sshx_root "$sshx_root" --argjson attempts "$attempts_json" '{schema_version:$schema_version,flight_id:$flight_id,flight_dir:$flight_dir,sshx_root:$sshx_root,attempts:$attempts}'); then
    reason=INTERNAL_ERROR
    return 1
  fi
  [ -n "$projection_json" ] || { reason=INTERNAL_ERROR; return 1; }
  printf '%s\n' "$projection_json" || { reason=INTERNAL_ERROR; return 1; }
}
main() {
  flight_id= attempt= stage= work_target= sandbox=; project_paths=0; project_flight=0; seen_options='|'
  while [ "$#" -gt 0 ]; do
    option=$1
    case "$option" in
      --project-paths)
        case "$seen_options" in *"$option"*) usage_error "duplicate option $option"; return 1 ;; esac
        project_paths=1; seen_options="$seen_options$option|"; shift; continue
        ;;
      --project-flight)
        case "$seen_options" in *"$option"*) usage_error "duplicate option $option"; return 1 ;; esac
        project_flight=1; seen_options="$seen_options$option|"; shift; continue
        ;;
      --flight-id) target=flight_id ;; --attempt) target=attempt ;; --stage) target=stage ;;
      --work-target) target=work_target ;; --sandbox) target=sandbox ;;
      --*) usage_error "unknown option $option"; return 1 ;; *) usage_error "unexpected positional argument $option"; return 1 ;;
    esac
    require_value "$option" "$#" || return 1
    case "$seen_options" in *"$option"*) usage_error "duplicate option $option"; return 1 ;; esac
    printf -v "$target" '%s' "$2"; seen_options="$seen_options$option|"
    shift 2
  done
  case "$seen_options" in *'--flight-id'*) ;; *) usage_error "missing --flight-id"; return 1 ;; esac
  [ $((project_paths + project_flight)) -le 1 ] || { usage_error "query modes are mutually exclusive"; return 1; }
  if [ "$project_flight" -eq 0 ]; then
    case "$seen_options" in *'--attempt'*) ;; *) usage_error "missing --attempt"; return 1 ;; esac
  fi
  case "$flight_id" in ''|.|*'..'*|*[!A-Za-z0-9._-]*) usage_error "invalid --flight-id"; return 1 ;; esac
  if [ "$project_flight" -eq 0 ]; then
    positive_integer "$attempt" || { usage_error "--attempt must be a positive integer"; return 1; }
    attempt=$integer_value
  fi
  if [ "$project_paths" -eq 1 ]; then
    for run_option in --stage --work-target --sandbox; do
      case "$seen_options" in *"$run_option"*) usage_error "--project-paths cannot be combined with $run_option"; return 1 ;; esac
    done
  elif [ "$project_flight" -eq 1 ]; then
    for other_option in --attempt --stage --work-target --sandbox; do
      case "$seen_options" in *"$other_option"*) usage_error "--project-flight cannot be combined with $other_option"; return 1 ;; esac
    done
  else
    for required_option in --stage --work-target; do
      case "$seen_options" in *"$required_option"*) ;; *) usage_error "missing $required_option"; return 1 ;; esac
    done
  fi
  case "$seen_options" in *'--sandbox'*) ;; *) sandbox=danger-full-access ;; esac
  if [ "$project_paths" -eq 0 ] && [ "$project_flight" -eq 0 ]; then
    case "$stage" in thinking|implementation|review) ;; *) usage_error "invalid --stage"; return 1 ;; esac
    case "$work_target" in /*) ;; *) usage_error "--work-target must be an absolute path"; return 1 ;; esac
    case "$work_target" in *$'\n'*|*$'\r'*) usage_error "--work-target must not contain LF or CR"; return 1 ;; esac
    case "$sandbox" in danger-full-access|workspace-write) ;; *) usage_error "invalid --sandbox"; return 1 ;; esac
  fi
  if ! jq_path=$(command -v jq 2>/dev/null) || [ ! -x "$jq_path" ]; then reason=PARSER_UNAVAILABLE; return 1; fi
  normalize_tmp_base || return 1
  derive_project_paths
  if [ "$project_flight" -eq 1 ]; then
    emit_project_flight || return 1
    trap - EXIT
    return 0
  fi
  if [ "$project_paths" -eq 1 ]; then
    emit_project_paths || return 1
    trap - EXIT
    return 0
  fi
  [ -d "$tmp_base" ] && [ -w "$tmp_base" ] || {
    reason=RUN_DIR_UNAVAILABLE
    printf '%s\n' 'run-codex-worker: RUN_DIR_UNAVAILABLE: TMPDIR must resolve to an existing writable directory' >&2
    return 1
  }
  ensure_dir() {
    ensure_path=$1
    [ ! -L "$ensure_path" ] || return 1
    if [ -e "$ensure_path" ]; then [ -d "$ensure_path" ]; elif mkdir "$ensure_path" 2>/dev/null; then [ -d "$ensure_path" ] && [ ! -L "$ensure_path" ]; else [ -d "$ensure_path" ] && [ ! -L "$ensure_path" ]; fi
  }
  ensure_dir "$consensus_root" && ensure_dir "$sshx_root" && ensure_dir "$run_parent" || { reason=RUN_DIR_UNAVAILABLE; return 1; }
  if [ -e "$run_dir" ] || [ -L "$run_dir" ]; then reason=RUN_DIR_COLLISION; return 1; fi
  if ! mkdir "$run_dir"; then
    if [ -e "$run_dir" ] || [ -L "$run_dir" ]; then reason=RUN_DIR_COLLISION; else reason=RUN_DIR_UNAVAILABLE; fi
    return 1
  fi
  run_dir_owned=1
  cat > "$brief_ref" || return 1
  stage_verdict_specs='thinking=propose|revise|reject|abstain;review=approve|comment|reject;implementation='
  allowed_verdict_spec=${stage_verdict_specs#*"$stage="}; allowed_verdict_spec=${allowed_verdict_spec%%;*}
  allowed_verdict_text=${allowed_verdict_spec:-no-verdict-required}
  if ! skeleton_json=$("$jq_path" --compact-output --null-input --arg stage "$stage" --arg verdict "${allowed_verdict_spec%%|*}" --arg log_ref "$stdout_ref" '{conclusion:(if $stage == "implementation" then {} else {verdict:$verdict} end),log_ref:$log_ref}'); then
    reason=INTERNAL_ERROR
    printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: cannot render minimum envelope shape' >&2
    return 1
  fi
  if [ -z "$skeleton_json" ]; then
    reason=INTERNAL_ERROR
    printf '%s\n' 'run-codex-worker: INTERNAL_ERROR: rendered minimum envelope shape is empty' >&2
    return 1
  fi
  cat >> "$brief_ref" <<EOF || return 1

## Runner-owned artifact contract

The attempt directory is $run_dir. Publish the worker-owned artifacts there.
The runner never creates, repairs, copies, normalizes, or touches them.
- Result envelope: $result_ref
- Completion sentinel: $sentinel_ref
- Stage: $stage; allowed verdict: $allowed_verdict_text
The envelope must be strict JSON whose top-level value is an object containing exactly the keys "conclusion" and "log_ref".
"conclusion" must be a JSON object, not a string. "log_ref" must be a non-empty JSON string.
For the "thinking" and "review" stages, "conclusion.verdict" must be a JSON string and one of the values on the allowed verdict line above. The "implementation" stage does not require a verdict.
Minimum structurally accepted envelope shape: $skeleton_json
This example is only the minimum structurally accepted shape; the worker must populate "conclusion" with the complete structured result required by its task brief and must not submit this skeletal example unchanged.
The example uses this attempt's worker stdout log for "log_ref"; the worker may replace it with another non-empty reference.
Publish result.json.tmp then atomically rename it to result.json, then do the same for completion.sentinel.
EOF
  : > "$stdout_ref" && : > "$stderr_ref" && : > "$last_message_ref" || return 1
  if started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null); then :; else started_at=; fi
  if started_epoch=$(date -u '+%s' 2>/dev/null); then case "$started_epoch" in ''|*[!0-9]*) started_epoch= ;; esac; else started_epoch=; fi
  log_field flight_id "$flight_id"; log_field attempt "$attempt"; log_field stage "$stage"
  log_field work_target "$work_target"; log_field sandbox "$sandbox"; log_field run_dir "$run_dir"
  log_field brief "$brief_ref"; log_field stdout_log "$stdout_ref"; log_field stderr_log "$stderr_ref"
  log_field last_message "$last_message_ref"; log_field result "$result_ref"; log_field sentinel "$sentinel_ref"
  log_field carrier_exit "$carrier_exit_ref"; log_field status_file "$status_ref"
  if ! codex_path=$(command -v codex 2>/dev/null) || [ ! -x "$codex_path" ]; then reason=LAUNCH_FAILED; return 1; fi
  log_event 'carrier starting'
  "$codex_path" exec --json -C "$work_target" --sandbox "$sandbox" --skip-git-repo-check -o "$last_message_ref" - \
    < "$brief_ref" > "$stdout_ref" 2> "$stderr_ref"
  carrier_exit=$?
  log_event "carrier exited rc=$carrier_exit"
  regular_or_absent "$carrier_exit_ref" && regular_or_absent "$carrier_exit_ref.tmp" || return 1
  printf '%s\n' "$carrier_exit" > "$carrier_exit_ref.tmp" && mv -f "$carrier_exit_ref.tmp" "$carrier_exit_ref" && [ -f "$carrier_exit_ref" ] && [ ! -L "$carrier_exit_ref" ] || return 1
  [ "$carrier_exit" -eq 0 ] || { reason=CARRIER_EXIT_NONZERO; return 1; }
  [ -f "$result_ref" ] && [ ! -L "$result_ref" ] || { reason=RESULT_MISSING; return 1; }
  if ! "$jq_path" -e -s --arg stage "$stage" '
    . as $doc | if length != 1 or ($doc[0]|type) != "object" or ($doc[0]|keys) != ["conclusion","log_ref"] or ($doc[0].conclusion|type) != "object" or ($doc[0].log_ref|type) != "string" or ($doc[0].log_ref|length) == 0 or ($stage != "implementation" and ($doc[0].conclusion.verdict|type) != "string") then error("invalid envelope") else true end
  ' "$result_ref" >/dev/null 2>&1; then reason=ENVELOPE_INVALID; return 1; fi
  if [ -n "$allowed_verdict_spec" ] && ! verdict=$("$jq_path" -er --arg allowed "$allowed_verdict_spec" ' .conclusion.verdict as $verdict | if (($allowed|split("|")) | index($verdict)) != null then $verdict else error("invalid verdict") end ' "$result_ref" 2>/dev/null); then reason=VERDICT_INVALID; return 1; fi
  if [ -n "$allowed_verdict_spec" ]; then result_valid=1; fi
  [ -f "$sentinel_ref" ] && [ ! -L "$sentinel_ref" ] || { reason=SENTINEL_MISSING; return 1; }
  reason=COMPLETE
  code=0
}
  main "$@"
