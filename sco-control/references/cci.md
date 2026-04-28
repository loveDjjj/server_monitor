# CCI Apps

CCI is best for long-running interactive/debug containers or services. Creation uses a YAML file.

## Commands

```bash
sco cci apps create "$APP" --workspace-name "$WORKSPACE" --config ./cci-app.yaml --ports 1002,1003
sco cci apps list --workspace-name "$WORKSPACE"
sco cci apps list --workspace-name "$WORKSPACE" --keyword "$USER_ID" --page-size 20
sco cci apps list --workspace-name "$WORKSPACE" --aec2-name "$AEC2" --state RUNNING
sco cci apps describe "$APP" --workspace-name "$WORKSPACE" -o yaml
sco cci apps update "$APP" --workspace-name "$WORKSPACE" --replicas 1
sco cci apps update "$APP" --workspace-name "$WORKSPACE" --ports 3001,3002
sco cci apps stop "$APP" --workspace-name "$WORKSPACE"
sco cci apps start "$APP" --workspace-name "$WORKSPACE"
sco cci apps delete "$APP" --workspace-name "$WORKSPACE"
```

`--keyword` fuzzy-searches `user_name`, `display_name`, and `name`, which is the safest way to ask "show my CCI apps" without hard-coding a user id.

If `apps list` fails with a workspace schema decode error while resolving `--workspace-name`, the CLI can reach SRM but the installed CCI/SRM component is incompatible with the returned workspace schema. Upgrade SCO components first. If list is still blocked, use an already known app name with `describe`; do not run mutating commands to debug this.

## Minimal YAML

```yaml
display_name: my-app
resource_pool:
  name: production-cluster
  available_zone: <zone>
replicas: 1
template:
  containers:
    - name: main
      image_path: <registry>/<namespace>/<image>:<tag>
      resource_request:
        cpu: "12"
        nvidia.com/gpu: "1"
        memory: 120GiB
      command:
        - sleep
        - inf
      volume_mounts:
        - type: PV_AFS
          id: <afs-volume-id>
          mount_path: /data
          subdir: /
          zone: <zone>
          display_name: shared-storage-volume
  init_containers: []
  resource_spec:
    name: <resource-spec>
scheduling:
  priority: NORMAL
  quota_type: RESERVED
  instance_affinity: POD_AFFINITY
```

`command` must be YAML list style when it contains spaces or shell fragments. For shell commands, use:

```yaml
command:
  - sh
  - -c
  - "cd /data/project && sleep inf"
```
