#!/bin/bash
set -u
umask 077

usage_error() { printf '%s\n' "run-codex-worker-batch: USAGE_ERROR: $1" >&2; exit 64; }
internal_error() { printf '%s\n' "run-codex-worker-batch: INTERNAL_ERROR: $1" >&2; exit 1; }
reserved_report_target() { [ -f "$1" ] && [ ! -L "$1" ] && [ -w "$1" ]; }
require_value() { [ "$2" -ge 2 ] || usage_error "missing value for $1"; }

manifest= report= seen_options='|'
while [ "$#" -gt 0 ]; do
  option=$1
  case "$option" in
    --manifest) target=manifest ;;
    --report) target=report ;;
    --*) usage_error "unknown option $option" ;;
    *) usage_error "unexpected positional argument $option" ;;
  esac
  require_value "$option" "$#"
  case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
  printf -v "$target" '%s' "$2"
  seen_options="$seen_options$option|"
  shift 2
done
for required_option in --manifest --report; do
  case "$seen_options" in *"$required_option"*) ;; *) usage_error "missing $required_option" ;; esac
done
case "$manifest" in /*) ;; *) usage_error "--manifest must be an absolute path" ;; esac
case "$manifest" in *$'\n'*|*$'\r'*) usage_error "--manifest must not contain LF or CR" ;; esac
case "$report" in /*) ;; *) usage_error "--report must be an absolute path" ;; esac
case "$report" in *$'\n'*|*$'\r'*) usage_error "--report must not contain LF or CR" ;; esac
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
    (.flight_id | contains("..") | not) and
    (.stage == "thinking" or .stage == "implementation" or .stage == "review") and
    (.work_target | type) == "string" and (.work_target | startswith("/")) and (.work_target | test("[\\n\\r]") | not) and
    (.brief_ref | type) == "string" and (.brief_ref | startswith("/")) and (.brief_ref | test("[\\n\\r]") | not) and
    ((has("sandbox") | not) or .sandbox == "danger-full-access" or .sandbox == "workspace-write")
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
[ ! -e "$report" ] && [ ! -L "$report" ] || usage_error "report target must be absent"
report_parent=${report%/*}; [ -n "$report_parent" ] || report_parent=/
[ -d "$report_parent" ] && [ -w "$report_parent" ] || usage_error "report parent is unavailable"

worker_count=$("$jq_path" -r '.workers | length' "$manifest") || internal_error "cannot count workers"
flight_ids=(); attempts=(); stages=(); work_targets=(); brief_refs=(); sandboxes=(); projections=()
i=0
while [ "$i" -lt "$worker_count" ]; do
  flight_ids[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].flight_id' "$manifest") || internal_error "cannot read flight_id"
  attempts[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].attempt' "$manifest") || internal_error "cannot read attempt"
  stages[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].stage' "$manifest") || internal_error "cannot read stage"
  work_targets[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].work_target' "$manifest") || internal_error "cannot read work_target"
  brief_refs[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].brief_ref' "$manifest") || internal_error "cannot read brief_ref"
  sandboxes[$i]=$("$jq_path" -r --argjson i "$i" 'if .workers[$i] | has("sandbox") then .workers[$i].sandbox else "" end' "$manifest") || internal_error "cannot read sandbox"
  [ -f "${brief_refs[$i]}" ] && [ ! -L "${brief_refs[$i]}" ] || usage_error "brief_ref for worker $i must name a regular non-symlink file"
  if ! exec 3< "${brief_refs[$i]}"; then usage_error "brief_ref for worker $i is not readable"; fi
  exec 3<&-
  if ! projections[$i]=$(bash "$runner" --project-paths --flight-id "${flight_ids[$i]}" --attempt "${attempts[$i]}"); then
    internal_error "cannot project paths for worker $i"
  fi
  i=$((i + 1))
done

report_reserved=0
release_unpublished_report() {
  [ "$report_reserved" -eq 0 ] || rm -f -- "$report"
}
trap release_unpublished_report EXIT

interrupted=false
interrupt_wait_status=0
launch_complete=0
record_interrupt() {
  if [ "$interrupted" = false ]; then
    interrupted=true
    interrupt_wait_status=$1
  fi
  [ "$launch_complete" -eq 0 ] || trap '' INT TERM
}
trap 'record_interrupt 130' INT
trap 'record_interrupt 143' TERM

if ! (set -o noclobber; : > "$report") 2>/dev/null; then
  usage_error "report target cannot be reserved exclusively"
fi
report_reserved=1

if ! report_tmp=$(mktemp "$report.tmp.XXXXXX"); then
  internal_error "cannot reserve report temporary file"
fi
[ -f "$report_tmp" ] && [ ! -L "$report_tmp" ] && [ -w "$report_tmp" ] || internal_error "invalid report temporary file"

pids=(); runner_exit_codes=()
i=0
while [ "$i" -lt "$worker_count" ]; do
  if [ -n "${sandboxes[$i]}" ]; then
    bash "$runner" --flight-id "${flight_ids[$i]}" --attempt "${attempts[$i]}" --stage "${stages[$i]}" --work-target "${work_targets[$i]}" --sandbox "${sandboxes[$i]}" < "${brief_refs[$i]}" &
  else
    bash "$runner" --flight-id "${flight_ids[$i]}" --attempt "${attempts[$i]}" --stage "${stages[$i]}" --work-target "${work_targets[$i]}" < "${brief_refs[$i]}" &
  fi
  pids[$i]=$!
  i=$((i + 1))
done
launch_complete=1
[ "$interrupted" = false ] || trap '' INT TERM

any_failed=0
i=0
while [ "$i" -lt "$worker_count" ]; do
  while :; do
    wait "${pids[$i]}"
    wait_rc=$?
    if [ "$interrupt_wait_status" -ne 0 ] && [ "$wait_rc" -eq "$interrupt_wait_status" ]; then
      interrupt_wait_status=0
      continue
    fi
    runner_exit_codes[$i]=$wait_rc
    break
  done
  [ "${runner_exit_codes[$i]}" -eq 0 ] || any_failed=1
  i=$((i + 1))
done
trap '' INT TERM

workers_json='[]'
i=0
while [ "$i" -lt "$worker_count" ]; do
  if ! workers_json=$("$jq_path" --compact-output --null-input --argjson workers "$workers_json" --arg flight_id "${flight_ids[$i]}" --argjson attempt "${attempts[$i]}" --argjson runner_exit_code "${runner_exit_codes[$i]}" --argjson projection "${projections[$i]}" '$workers + [{flight_id:$flight_id,attempt:$attempt,runner_exit_code:$runner_exit_code,run_dir:$projection.run_dir,status_ref:$projection.status_ref}]'); then
    internal_error "cannot render report worker $i"
  fi
  i=$((i + 1))
done

reserved_report_target "$report" || internal_error "invalid reserved report target at publication"
[ -f "$report_tmp" ] && [ ! -L "$report_tmp" ] && [ -w "$report_tmp" ] || internal_error "invalid report temporary file at publication"
if ! "$jq_path" --null-input --argjson schema_version 1 --argjson interrupted "$interrupted" --argjson workers "$workers_json" '{schema_version:$schema_version,all_workers_waited:true,interrupted:$interrupted,workers:$workers}' > "$report_tmp"; then
  internal_error "cannot render report"
fi
[ -s "$report_tmp" ] || internal_error "rendered report is empty"
mv -f "$report_tmp" "$report" || internal_error "cannot publish report"
[ -f "$report" ] && [ ! -L "$report" ] || internal_error "published report has invalid type"
report_reserved=0
trap - EXIT

[ "$any_failed" -eq 0 ] && [ "$interrupted" = false ] && exit 0
exit 1
