# ACP Jobs

ACP is the main path for training/batch jobs.

## Create

```bash
sco acp jobs create --workspace-name "$WORKSPACE" --aec2-name "$AEC2" --job-name "$JOB" \
  --priority normal \
  --storage-mount "$VOLUME_ID:/data" \
  --container-image-url "$IMAGE" \
  --training-framework pytorch \
  --worker-nodes "$WORKER_NODES" \
  --worker-spec "$SPEC" \
  --quota-type reserved \
  --command "$COMMAND"
```

CLI-required: `--workspace-name`, `--aec2-name`, `--job-name`, `--container-image-url`, `--training-framework`, `--worker-spec`. The CLI defaults to one worker node and `--command="sleep inf"`; for actual training always pass an explicit real workload via `--command` instead of relying on that default.

Useful optional flags: `--priority normal|high|highest`, `--storage-mount volume_id[:subdir]:path`, `--env key:value,...`, `--follow`, `--quota-type spot|reserved`, `--enable-fault-tolerance`, `--retry-times`.

Quota type maps to resource class: `reserved` is standard/reserved quota and is the default; `spot` is idle/preemptible quota. Use `--quota-type spot` only when the user explicitly wants idle resources and accepts possible interruption.

For public clusters, `--aec2-name public` also needs `--az` and `--vpc-id`.

## Query And Control

```bash
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 10
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 100 -o json
sco acp jobs describe --workspace-name "$WORKSPACE" -o json "$JOB_ID"
sco acp jobs get-workers --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs stream-logs --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs stream-logs --workspace-name "$WORKSPACE" "$JOB_ID" --worker-name "$WORKER" --follow
sco acp jobs exec --workspace-name "$WORKSPACE" --worker-name "$WORKER" "$JOB_ID"
sco acp jobs stop --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs start --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs delete --workspace-name "$WORKSPACE" "$JOB_ID"
```

`create` returns a platform job name such as `pt-xxxxxxxx`; use that name (not the display name or UID) for `describe`, `get-workers`, and `stream-logs`. Use `get-workers` to find the complete worker name before `exec` or worker-specific logs. Submission alone does not establish that a worker is running.

When operating within this repository, read `docs/acp-training.md` for the tested AFS mount, Conda initialization, PowerShell quoting workaround, and current-environment checks before submitting.

Some SCO component builds do not accept server-side `--user-name` or `--state` filters for `jobs list`. When a filter is unavailable, list first and filter client-side:

```bash
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 100 -o table | awk 'NR<=4 || /'"$USER_ID"'/'
```
