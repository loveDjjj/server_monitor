# AOSS And CCR Images

The SCO docs expose container registry operations through `sco ccr`. Treat user mentions of AOSS image management as CCR image/build/namespace/label work unless they provide a separate AOSS CLI.

## List And Inspect

```bash
sco ccr images list
sco ccr images list -p
sco ccr namespaces list
sco ccr namespaces get "$NAMESPACE"
sco ccr labels list
sco ccr labels get "$IMAGE_NAME" -n "$NAMESPACE"
```

If a read-only CCR list command hangs, stop it and fall back to `--help` plus known image URLs from the user's templates. Do not run build commands as a test.

## Build Images

```bash
sco ccr builds create -n "$NAMESPACE" -f ./Dockerfile -t "$TAG"
sco ccr builds create -n "$NAMESPACE" -f ./Dockerfile -t "$TAG" -c ./context
sco ccr builds create -n "$NAMESPACE" -f ./Dockerfile -t "$TAG" -c "$AOSS_CONTEXT_URL"
sco ccr builds list -n "$NAMESPACE"
sco ccr builds get -n "$NAMESPACE" "$BUILD_ID"
sco ccr builds log -n "$NAMESPACE" "$BUILD_ID"
```

## Labels

```bash
sco ccr labels update "$IMAGE_NAME" -n "$NAMESPACE" -c gpu,cpu,dcu
sco ccr labels update "$IMAGE_NAME" -n "$NAMESPACE" -p acp,ams
sco ccr labels update "$IMAGE_NAME" -n "$NAMESPACE" --os ubuntu20 --language python3.10
```

Use fully qualified image URLs for ACP/CCI submissions, for example:

```text
<registry>/<namespace>/<image>:<tag>
```

For AOSS-backed build contexts, pass the object URL through `AOSS_CONTEXT_URL` or another runtime variable. Do not commit signed URLs, bucket names, or upload paths if they identify a user, project, or tenant.
