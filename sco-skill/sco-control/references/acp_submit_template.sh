#!/usr/bin/env bash
set -euo pipefail

# Sanitized ACP submission skeleton. Fill all site/user-specific values through
# environment variables; do not commit personal ids, secrets, volume ids, or
# concrete workspace/cluster names into this file.

: "${SCO_WORKSPACE:?set SCO_WORKSPACE}"
: "${SCO_AEC2:?set SCO_AEC2}"
: "${CONTAINER_IMAGE_URL:?set CONTAINER_IMAGE_URL}"
: "${STORAGE_MOUNT:?set STORAGE_MOUNT, e.g. volume_id:/data}"
: "${REMOTE_PROJECT_DIR:?set REMOTE_PROJECT_DIR}"

SPEC="${SPEC:-<worker-spec>}"
WORKER_NODES="${WORKER_NODES:-1}"
QUOTA_TYPE="${QUOTA_TYPE:-reserved}" # reserved or spot
PRIORITY="${PRIORITY:-normal}"
TASK="${1:-${SCO_TASK:-smoke}}"
DRY_RUN="${DRY_RUN:-0}"
TARGET_SCRIPT="${TARGET_SCRIPT:-}"
ENTRY_ARG="${ENTRY_ARG:-}"
JOB_NAME="${JOB_NAME:-}"

declare -a EXTRA_EXPORTS=()

set_target() {
  local job="$1"
  local script="$2"
  local entry_arg="${3:-}"
  JOB_NAME="${JOB_NAME:-${job}}"
  TARGET_SCRIPT="${TARGET_SCRIPT:-${script}}"
  ENTRY_ARG="${ENTRY_ARG:-${entry_arg}}"
}

add_export() {
  EXTRA_EXPORTS+=("$1=$2")
}

configure_task() {
  case "${TASK}" in
    smoke)
      set_target "example_smoke" "scripts/run_smoke.sh"
      add_export WANDB "0"
      add_export TRAIN_STEPS "${TRAIN_STEPS:-1}"
      add_export SAVE_STEPS "${SAVE_STEPS:-1}"
      ;;
    pilot)
      set_target "example_pilot" "scripts/run_train.sh"
      add_export TRAIN_STEPS "${TRAIN_STEPS:-50000}"
      add_export SAVE_STEPS "${SAVE_STEPS:-10000}"
      add_export EVAL_STEPS "${EVAL_STEPS:-5000}"
      ;;
    full)
      set_target "example_full" "scripts/run_train.sh"
      add_export TRAIN_STEPS "${TRAIN_STEPS:-400000}"
      add_export SAVE_STEPS "${SAVE_STEPS:-10000}"
      add_export EVAL_STEPS "${EVAL_STEPS:-5000}"
      ;;
    diagnostics)
      set_target "example_diagnostics" "scripts/run_diagnostics.sh"
      add_export CKPT_NAMES "${CKPT_NAMES:-latest.pth}"
      add_export SAMPLE_COUNT "${SAMPLE_COUNT:-1024}"
      ;;
    *.sh | scripts/*)
      set_target "example_$(basename "${TASK}" .sh)" "${TASK}"
      ;;
    *)
      echo "Unknown SCO task: ${TASK}" >&2
      exit 2
      ;;
  esac
}

shell_quote() {
  printf "%q" "$1"
}

configure_task

command=$(
  printf 'set -euo pipefail\n'
  printf 'cd %s\n' "$(shell_quote "${REMOTE_PROJECT_DIR}")"
  printf 'export ROOT_DIR=%s\n' "$(shell_quote "${REMOTE_PROJECT_DIR}")"
  for item in "${EXTRA_EXPORTS[@]}"; do
    key="${item%%=*}"
    value="${item#*=}"
    printf 'export %s=%s\n' "${key}" "$(shell_quote "${value}")"
  done
  printf 'echo "[SCO] TASK=%s"\n' "$(shell_quote "${TASK}")"
  printf 'echo "[SCO] JOB_NAME=%s"\n' "$(shell_quote "${JOB_NAME}")"
  printf 'echo "[SCO] TARGET_SCRIPT=%s"\n' "$(shell_quote "${TARGET_SCRIPT}")"
  if [[ -n "${ENTRY_ARG}" ]]; then
    printf 'bash %s %s\n' "$(shell_quote "${TARGET_SCRIPT}")" "$(shell_quote "${ENTRY_ARG}")"
  else
    printf 'bash %s\n' "$(shell_quote "${TARGET_SCRIPT}")"
  fi
)

if [[ "${DRY_RUN}" != "0" ]]; then
  echo "[SCO dry-run] Command that would be submitted:"
  printf '%s\n' "${command}"
  exit 0
fi

sco acp jobs create \
  --workspace-name="${SCO_WORKSPACE}" \
  --aec2-name="${SCO_AEC2}" \
  --job-name="${JOB_NAME}" \
  --container-image-url="${CONTAINER_IMAGE_URL}" \
  --training-framework=pytorch \
  --worker-nodes="${WORKER_NODES}" \
  --worker-spec="${SPEC}" \
  --priority="${PRIORITY}" \
  --quota-type="${QUOTA_TYPE}" \
  --storage-mount="${STORAGE_MOUNT}" \
  --command="${command}"
