# ACP Submit Template Pattern

Use this pattern when generating or modifying a shell template that submits ACP jobs. Keep personal identifiers, access tokens, concrete workspace names, cluster names, storage ids, and project paths outside the template by passing them through environment variables.

## Required Environment

```bash
SCO_WORKSPACE       # workspace name
SCO_AEC2            # AEC2 cluster name, or public when using public cluster flags
CONTAINER_IMAGE_URL # fully qualified image URL
STORAGE_MOUNT       # volume_id[:subdir]:container_path
REMOTE_PROJECT_DIR  # project path inside the mounted storage
```

Common optional environment:

```bash
SPEC="${SPEC:-<worker-spec>}"
WORKER_NODES="${WORKER_NODES:-1}"
QUOTA_TYPE="${QUOTA_TYPE:-reserved}"   # reserved or spot
PRIORITY="${PRIORITY:-normal}"         # normal, high, highest
DRY_RUN="${DRY_RUN:-0}"
```

## Task Families

Use task families instead of hard-coding project-specific job names:

- `*-smoke`: one or a few steps, tiny sample counts, external logging disabled.
- `*-pilot`: medium run, periodic save/eval, normal experiment tags.
- `*-full`: long run, same script as pilot with larger step counts.
- `*-diagnostics` or `*-analysis`: no training loop; set checkpoint names and sample/SVD/quant limits.
- `scripts/*` or `*.sh`: custom script path; derive a safe job name from the basename.

## Dispatch Shape

Task dispatch should set only:

- `JOB_NAME`
- `TARGET_SCRIPT`
- optional `ENTRY_ARG`
- extra exported environment variables

Keep command rendering centralized so all tasks share quoting and submission behavior.

```bash
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
```

## ACP Submission

Build a multi-line remote command and pass it as `--command`. Quote values with `printf %q`; never interpolate untrusted values raw into the remote command.

```bash
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
```

## Dry Run

Support `DRY_RUN=1` to print the exact remote command without calling `sco`. In controlled mode, prefer dry-run output and skip live submission.

See [acp_submit_template.sh](acp_submit_template.sh) for a compact sanitized shell skeleton.
