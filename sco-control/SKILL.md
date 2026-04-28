---
name: sco-control
description: Use this skill when controlling SenseCore SCO CLI resources and jobs, especially CCI cloud container instances, ACP training jobs, AEC2 cluster/spec queries, AFS storage, AOSS/CCR container images, and shell-template based job submission. It is optimized for frequent query, submit, inspect, log, exec, and storage/image management workflows.
---

# SCO Control

Use this skill to operate `sco` pragmatically from shell templates and known command patterns. Prefer the embedded patterns below before reading references. Load reference files only when the user asks for a less common flag, a YAML example, or a precise command variant.

## Operating Rules

- Treat query/inspect as the default first step: list resources, describe the target, then run mutating commands only when intent is explicit.
- For destructive operations (`delete`, quota/ACL updates, stop running jobs/apps), show the exact command and ask unless the user already explicitly requested execution.
- Prefer `-o json` or `-o yaml` when a command supports it and the next step needs parsing.
- Do not assume a single-user workspace. ACP `jobs list` can return other users' jobs; filter client-side by the runtime user id or by job display-name patterns.
- Keep secrets out of chat and committed files. If a template includes credentials, leave them as environment variables and do not echo them back.
- In controlled/sandboxed environments, build commands and files first; skip live `sco` tests unless the user asks.

## Priority Workflow

1. Establish context:
   ```bash
   sco config profiles list
   sco config list
   sco aec2 clusters list
   sco afs volume list
   sco ccr images list
   ```
2. Query compute capacity:
   ```bash
   sco aec2 clusters list-workerspec --workspace-name "$WORKSPACE" --aec2-name "$AEC2"
   sco aec2 clusters describe --name "$AEC2"
   ```
3. Submit or manage jobs:
   - ACP batch/training jobs: use `sco acp jobs create`, or the shell-template pattern in [run_sco_pattern.md](references/run_sco_pattern.md).
   - For a reusable sanitized ACP submitter, adapt [acp_submit_template.sh](references/acp_submit_template.sh).
   - CCI long-running/debug containers: generate a YAML config and use `sco cci apps create`.
4. Inspect:
   ```bash
   sco acp jobs list --workspace-name "$WORKSPACE" --page-size 10
   sco acp jobs describe --workspace-name "$WORKSPACE" -o json "$JOB_ID"
   sco acp jobs get-workers --workspace-name "$WORKSPACE" "$JOB_ID"
   sco acp jobs stream-logs --workspace-name "$WORKSPACE" "$JOB_ID"
   sco cci apps list --workspace-name "$WORKSPACE"
   sco cci apps describe "$APP" --workspace-name "$WORKSPACE" -o yaml
   ```

## Command Map

ACP jobs, highest priority:
```bash
sco acp jobs create --workspace-name "$WORKSPACE" --aec2-name "$AEC2" --job-name "$JOB" \
  --container-image-url "$IMAGE" --training-framework pytorch --worker-nodes "$NODES" \
  --worker-spec "$SPEC" --priority normal --quota-type reserved --storage-mount "$VOL:/data" \
  --command "$COMMAND"
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 20 -o table
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 100 -o json
sco acp jobs describe --workspace-name "$WORKSPACE" -o json "$JOB_ID"
sco acp jobs get-workers --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs stream-logs --workspace-name "$WORKSPACE" "$JOB_ID" --follow
sco acp jobs exec --workspace-name "$WORKSPACE" --worker-name "$WORKER" "$JOB_ID"
sco acp jobs stop --workspace-name "$WORKSPACE" "$JOB_ID"
sco acp jobs start --workspace-name "$WORKSPACE" "$JOB_ID"
```

If the user gives a user id, use it only as a runtime filter, for example:

```bash
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 100 -o table | awk 'NR<=4 || /'"$USER_ID"'/'
```

CCI apps, highest priority:
```bash
sco cci apps create "$APP" --workspace-name "$WORKSPACE" --config ./cci-app.yaml --ports 1002,1003
sco cci apps list --workspace-name "$WORKSPACE" --keyword "$USER_ID" --page-size 20
sco cci apps list --workspace-name "$WORKSPACE" --aec2-name "$AEC2" --state RUNNING
sco cci apps describe "$APP" --workspace-name "$WORKSPACE" -o yaml
sco cci apps update "$APP" --workspace-name "$WORKSPACE" --replicas 1
sco cci apps stop "$APP" --workspace-name "$WORKSPACE"
sco cci apps start "$APP" --workspace-name "$WORKSPACE"
sco cci apps delete "$APP" --workspace-name "$WORKSPACE"
```

CCI `--keyword` fuzzy-matches `user_name`, `display_name`, or `name`, so use the runtime user id only as a query value, not as a skill default.

AEC2 queries:
```bash
sco aec2 clusters list
sco aec2 clusters describe --name "$AEC2"
sco aec2 clusters list-workerspec --workspace-name "$WORKSPACE" --aec2-name "$AEC2"
```

If AEC2 reports `SUBSCRIPTION_NAME_REQUIRED`, rerun with a configured profile or explicit global `--subscription`/`--resource-group` values from the user's environment.

AFS storage:
```bash
sco afs volume list
sco afs volume list -i "$VOLUME_ID"
sco afs volume ls -i "$VOLUME_ID" -d /
sco afs dir-quota list -i "$VOLUME_ID"
sco afs dir-quota create -i "$VOLUME_ID" -d /path --files 100000 --capacity 1024
sco afs dir-quota update -i "$VOLUME_ID" -d /path --files 100000 --capacity 2048
sco afs dir-acl list -i "$VOLUME_ID" -d /path
sco afs dir-acl create -i "$VOLUME_ID" -d /path -p rw -u all
```

AOSS/CCR images:
```bash
sco ccr images list
sco ccr images list -p
sco ccr namespaces list
sco ccr namespaces get "$NAMESPACE"
sco ccr builds create -n "$NAMESPACE" -f ./Dockerfile -t "$TAG" -c ./context
sco ccr builds list -n "$NAMESPACE"
sco ccr builds log -n "$NAMESPACE" "$BUILD_ID"
sco ccr labels list
sco ccr labels update "$IMAGE_NAME" -n "$NAMESPACE" -c gpu,cpu -p acp,ams
```

## Built-In Defaults From Submit Templates

When the user asks to create or adapt a submission template, preserve this shape unless they override it:

- default spec variable: `SPEC`
- default worker nodes: `1`
- default image variable: `CONTAINER_IMAGE_URL`
- default storage mount format: `volume_id:/data`
- workspace and AEC2 cluster are environment variables, not hard-coded constants
- command construction: `cd "$REMOTE_PROJECT_DIR"`, export env vars, echo task metadata, then `bash "$TARGET_SCRIPT" "$ENTRY_ARG"`
- submit command: `sco acp jobs create --training-framework=pytorch --priority=normal --quota-type=reserved --storage-mount=... --command=...`

For task-switching guidance, read [run_sco_pattern.md](references/run_sco_pattern.md). For a shell skeleton, use [acp_submit_template.sh](references/acp_submit_template.sh).

## Reference Loading

- Read [acp.md](references/acp.md) for ACP create/list/log/exec details or command flags.
- Read [cci.md](references/cci.md) for CCI YAML structure and container-app management.
- Read [aec2-afs.md](references/aec2-afs.md) for workerspec, cluster lookup, storage mount, quota, and ACL patterns.
- Read [images.md](references/images.md) for CCR/AOSS image listing, builds, namespaces, and labels.
- If vendor SCO docs are available locally, read them only as a last resort for rare flags.
