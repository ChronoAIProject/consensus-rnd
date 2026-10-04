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
    ((keys) == ["brief_ref", "stage", "work_target"] or
     (keys) == ["brief_ref", "sandbox", "stage", "work_target"] or
     (keys) == ["attempt", "brief_ref", "flight_id", "stage", "work_target"] or
     (keys) == ["attempt", "brief_ref", "flight_id", "sandbox", "stage", "work_target"]) and
    ((has("flight_id") | not) or
     ((.flight_id | type) == "string" and (.flight_id | test("^[0-9a-f]{24}$")))) and
    (.stage == "thinking" or .stage == "implementation" or .stage == "review" or .stage == "termination") and
    (.work_target | type) == "string" and (.work_target | startswith("/")) and (.work_target | test("[\\n\\r]") | not) and
    (.brief_ref | type) == "string" and (.brief_ref | startswith("/")) and (.brief_ref | test("[\\n\\r]") | not) and
    ((has("sandbox") | not) or .sandbox == "danger-full-access" or .sandbox == "workspace-write")
  ))
' "$manifest" >/dev/null 2>&1; then
  usage_error "invalid manifest"
fi
if ! "$jq_path" -e -s '
  .[0].workers | map(select(has("attempt"))) | all(
    (.attempt | type) == "number" and
    (.attempt | tostring | test("^[1-9][0-9]*$"))
  )
' "$manifest" >/dev/null 2>&1; then
  usage_error "manifest attempt must project as a positive decimal integer"
fi
if ! "$jq_path" -e -s '.[0].workers | map(select(has("flight_id"))) | group_by([.flight_id, .attempt]) | all(length == 1)' "$manifest" >/dev/null 2>&1; then
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
flight_ids=(); attempts=(); stages=(); work_targets=(); brief_refs=(); sandboxes=(); original_rows=()
if ! bash "$runner" --project-root >/dev/null; then internal_error "cannot project shared run root"; fi
i=0
while [ "$i" -lt "$worker_count" ]; do
  original_rows[$i]=$("$jq_path" -c --argjson i "$i" '.workers[$i]' "$manifest") || internal_error "cannot read worker"
  flight_ids[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].flight_id // ""' "$manifest") || internal_error "cannot read flight_id"
  attempts[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].attempt // ""' "$manifest") || internal_error "cannot read attempt"
  stages[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].stage' "$manifest") || internal_error "cannot read stage"
  work_targets[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].work_target' "$manifest") || internal_error "cannot read work_target"
  brief_refs[$i]=$("$jq_path" -r --argjson i "$i" '.workers[$i].brief_ref' "$manifest") || internal_error "cannot read brief_ref"
  sandboxes[$i]=$("$jq_path" -r --argjson i "$i" 'if .workers[$i] | has("sandbox") then .workers[$i].sandbox else "" end' "$manifest") || internal_error "cannot read sandbox"
  [ -f "${brief_refs[$i]}" ] && [ ! -L "${brief_refs[$i]}" ] || usage_error "brief_ref for worker $i must name a regular non-symlink file"
  if ! exec 3< "${brief_refs[$i]}"; then usage_error "brief_ref for worker $i is not readable"; fi
  exec 3<&-
  if [ -n "${flight_ids[$i]}" ] && ! bash "$runner" --project-paths --flight-id "${flight_ids[$i]}" --attempt "${attempts[$i]}" >/dev/null; then
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

# Private receipt captures remain available on every unpublished exit.
receipt_refs=(); recovery_json='[]'
i=0
while [ "$i" -lt "$worker_count" ]; do
  receipt_refs[$i]=$(mktemp "$report.receipt.$i.XXXXXX") || internal_error "cannot reserve receipt for worker $i"
  if ! recovery_json=$("$jq_path" -cn --argjson refs "$recovery_json" --argjson worker_index "$i" --arg receipt_ref "${receipt_refs[$i]}" '$refs + [{worker_index:$worker_index,receipt_ref:$receipt_ref}]'); then
    internal_error "cannot render receipt recovery references"
  fi
  i=$((i + 1))
done
"$jq_path" -cn --argjson receipt_refs "$recovery_json" '{schema_version:1,receipt_refs:$receipt_refs}' || internal_error "cannot publish receipt recovery references"

pids=(); runner_exit_codes=()
i=0
while [ "$i" -lt "$worker_count" ]; do
  runner_args=(--stage "${stages[$i]}" --work-target "${work_targets[$i]}")
  [ -z "${flight_ids[$i]}" ] || runner_args+=(--flight-id "${flight_ids[$i]}" --attempt "${attempts[$i]}")
  [ -z "${sandboxes[$i]}" ] || runner_args+=(--sandbox "${sandboxes[$i]}")
  bash "$runner" "${runner_args[@]}" > "${receipt_refs[$i]}" < "${brief_refs[$i]}" &
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

collect_receipt() {
  receipt_error=RECEIPT_MISSING
  projection=null
  [ -s "${receipt_refs[$i]}" ] || return 1
  receipt_error=RECEIPT_INVALID
  if ! receipt_json=$("$jq_path" -ce -s '
    if length == 1 and (.[0] | type) == "object" and
       (.[0].flight_id | type) == "string" and (.[0].flight_id | test("^[0-9a-f]{24}$")) and
       (.[0].attempt | type) == "number" and (.[0].attempt | tostring | test("^[1-9][0-9]*$"))
    then .[0] else error("invalid launch receipt") end
  ' "${receipt_refs[$i]}"); then return 1; fi
  resolved_id=$("$jq_path" -r '.flight_id' <<< "$receipt_json") || return 1
  resolved_attempt=$("$jq_path" -r '.attempt' <<< "$receipt_json") || return 1
  if [ -n "${flight_ids[$i]}" ]; then
    [ "$resolved_id" = "${flight_ids[$i]}" ] && [ "$resolved_attempt" = "${attempts[$i]}" ] || return 1
  else
    [ "$resolved_attempt" = 1 ] || return 1
  fi
  receipt_error=RECEIPT_PROJECTION_FAILED
  if ! expected_projection=$(bash "$runner" --project-paths --flight-id "$resolved_id" --attempt "$resolved_attempt"); then return 1; fi
  receipt_error=RECEIPT_PROJECTION_MISMATCH
  "$jq_path" -en --argjson receipt "$receipt_json" --argjson expected "$expected_projection" '$receipt == $expected' >/dev/null || return 1
  projection=$expected_projection
  receipt_error=
}

workers_json='[]'; resolved_rows='[]'; receipt_collection_failed=0
i=0
while [ "$i" -lt "$worker_count" ]; do
  if collect_receipt; then
    if ! resolved_rows=$("$jq_path" -cn --argjson rows "$resolved_rows" --argjson original "${original_rows[$i]}" --argjson projection "$projection" '$rows + [$original + {flight_id:$projection.flight_id,attempt:$projection.attempt}]'); then
      internal_error "cannot render resolved worker $i"
    fi
  else
    any_failed=1
    receipt_collection_failed=1
    printf '%s\n' "run-codex-worker-batch: $receipt_error: worker $i; receipt_ref=${receipt_refs[$i]}" >&2
  fi
  if ! workers_json=$("$jq_path" --compact-output --null-input --argjson workers "$workers_json" --argjson runner_exit_code "${runner_exit_codes[$i]}" --argjson projection "$projection" --arg receipt_error "$receipt_error" '$workers + [{flight_id:$projection.flight_id,attempt:$projection.attempt,runner_exit_code:$runner_exit_code,run_dir:$projection.run_dir,status_ref:$projection.status_ref,receipt_error:(if $receipt_error == "" then null else $receipt_error end)}]'); then
    internal_error "cannot render report worker $i"
  fi
  i=$((i + 1))
done

reserved_report_target "$report" || internal_error "invalid reserved report target at publication"
[ -f "$report_tmp" ] && [ ! -L "$report_tmp" ] && [ -w "$report_tmp" ] || internal_error "invalid report temporary file at publication"
if ! "$jq_path" --null-input --argjson schema_version 1 --argjson interrupted "$interrupted" --argjson workers "$workers_json" --argjson resolved_rows "$resolved_rows" '{schema_version:$schema_version,all_workers_waited:true,interrupted:$interrupted,workers:$workers,resolved_manifest:{schema_version:1,workers:$resolved_rows}}' > "$report_tmp"; then
  internal_error "cannot render report"
fi
[ -s "$report_tmp" ] || internal_error "rendered report is empty"
mv -f "$report_tmp" "$report" || internal_error "cannot publish report"
[ -f "$report" ] && [ ! -L "$report" ] || internal_error "published report has invalid type"
report_reserved=0
trap - EXIT
if [ "$receipt_collection_failed" -eq 0 ]; then
  rm -f -- "${receipt_refs[@]}" || internal_error "cannot remove published receipt captures"
fi

[ "$any_failed" -eq 0 ] && [ "$interrupted" = false ] && exit 0
exit 1
