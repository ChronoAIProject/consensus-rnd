#!/bin/bash
set -u

usage_error() { printf '%s\n' "read-codex-worker-status: USAGE_ERROR: $1" >&2; exit 64; }
internal_error() { printf '%s\n' "read-codex-worker-status: INTERNAL_ERROR: $1" >&2; exit 1; }
require_value() { [ "$2" -ge 2 ] || usage_error "missing value for $1"; }

manifest= seen_options='|'
while [ "$#" -gt 0 ]; do
  option=$1
  case "$option" in --manifest) target=manifest ;; --*) usage_error "unknown option $option" ;; *) usage_error "unexpected positional argument $option" ;; esac
  require_value "$option" "$#"
  case "$seen_options" in *"$option"*) usage_error "duplicate option $option" ;; esac
  printf -v "$target" '%s' "$2"
  seen_options="$seen_options$option|"
  shift 2
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
worker_count=$("$jq_path" -r '.workers | length' "$manifest") || internal_error "cannot count workers"
workers_json='[]'
i=0
while [ "$i" -lt "$worker_count" ]; do
  flight_id=$("$jq_path" -r --argjson i "$i" '.workers[$i].flight_id' "$manifest") || internal_error "cannot read flight_id"
  attempt=$("$jq_path" -r --argjson i "$i" '.workers[$i].attempt' "$manifest") || internal_error "cannot read attempt"
  if ! projection=$(bash "$runner" --project-paths --flight-id "$flight_id" --attempt "$attempt"); then
    internal_error "cannot project paths for worker $i"
  fi
  status_ref=$("$jq_path" -r '.status_ref' <<<"$projection") || internal_error "cannot read status_ref for worker $i"
  status_present=false
  status_document=null
  if [ -f "$status_ref" ] && [ ! -L "$status_ref" ]; then
    status_present=true
    if ! status_document=$("$jq_path" --compact-output -s 'if length == 1 then .[0] else error("invalid status document") end' "$status_ref"); then
      internal_error "cannot read status document for worker $i"
    fi
  fi
  if ! workers_json=$("$jq_path" --compact-output --null-input --argjson workers "$workers_json" --arg flight_id "$flight_id" --argjson attempt "$attempt" --argjson status_present "$status_present" --argjson status_document "$status_document" '$workers + [{flight_id:$flight_id,attempt:$attempt,status_present:$status_present,status_document:$status_document}]'); then
    internal_error "cannot render worker $i"
  fi
  i=$((i + 1))
done

"$jq_path" --null-input --argjson schema_version 1 --argjson workers "$workers_json" '{schema_version:$schema_version,workers:$workers}' || internal_error "cannot render status report"
