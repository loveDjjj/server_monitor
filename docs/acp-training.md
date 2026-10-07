# ACP 训练任务：AI 提交手册

本文记录 2026-10-07 在 `share-cluster` 实测成功的 ACP 启动方法。下次可直接对 AI 说：

> 阅读 `docs/acp-training.md`、`AGENTS.md` 和当前 `config/defaults.yaml`，用 SCO skill 提交 ACP 训练。训练入口是 `<实际脚本及参数>`，预计运行 `<时长>`；提交后检查 Worker、GPU 和训练日志，告诉我平台任务名和状态。

AI 还应阅读 `sco-skill/sco-control/SKILL.md` 和 `references/acp.md`。以下是历史验证值，提交前须核对本机 Profile、镜像、挂载、规格及账号配额；不得根据文档重置用户 CCI 配置，也不得把 GPU 余量作为 CCI 启动条件。

## 已验证的环境

| 配置 | 2026-10-07 验证值 |
| --- | --- |
| SCO Profile / Workspace | `default` / `share-space` |
| ACP 集群 / Worker 规格 | `share-cluster` / `n6ls.iu.i40.4.16c256g`（4 张 N6lS-80GB、16 vCPU、256 GiB） |
| 镜像 | `registry.cn-sh-01g.sensecore.cn/ccr-zhicheng-06/oneday-conda-09-21:oneday-container-20260921060318` |
| CCI 同源 AFS 卷 | `01a04263-91e5-7603-bc01-c67e503da6b5:/data` |
| Conda 注册脚本 / 环境 | `/data/260010081/conda-env.sh` / `longlive-rag` |
| 调度 | 1 个 Worker、`normal`、`reserved` |

真实训练脚本、参数、数据集、输出目录、检查点策略和预计时长**没有通用默认值**，应按本次训练确认。不要猜测 `train.py` 的位置，不要以 `sleep` 或无限 GPU 计算代替训练。`reserved` 曾调度成功，但集群空卡不等于个人配额，也不保证任务不会因其他原因终止。

验证任务 `pt-gy1zfuum`（直接调用环境 Python，120 秒）和 `pt-k7oyhx49`（Conda 激活，40 秒）都已 `SUCCEEDED` 且结束；后者日志确认 `torch 2.8.0+cu128`、4 张可见 GPU 和四张卡的实际矩阵运算。它们不是仍在运行的训练任务。

## 提交前

1. 读取最新 `config/defaults.yaml`，确认 Profile、Workspace、镜像与 AFS ID。确认目标训练文件在挂载的 `/data` 下，并核对运行命令、参数、数据与输出路径。不确定则先向用户确认。
2. 用 `sco aec2 clusters list-workerspec --workspace-name share-space --aec2-name share-cluster` 核对规格。查询当前账号是否已有同一训练；共享 Workspace 里可能有别人的任务，不操作别人的任务。
3. 如果训练依赖额外 Secret、端口或网络权限，不要假定 CCI 的 SSH Secret 会自动挂载到 ACP；按实际需要另行核对。

## Windows PowerShell 提交

下面模板中的 `REPLACE_WITH_REAL_TRAIN_ENTRY_AND_ARGUMENTS` 必须替换为本次真实训练入口和参数。Windows 直接传含嵌套引号的 `python -c` 曾把 ACP 启动脚本截断；经过实测，Base64 编码多行 Bash 脚本可避免原生命令转义问题。**Base64 不是加密，脚本中不要放密钥。**

```powershell
$sco = 'C:\Users\260010081\.sco\bin\sco.exe'
$jobName = 'oneday-train-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$image = 'registry.cn-sh-01g.sensecore.cn/ccr-zhicheng-06/oneday-conda-09-21:oneday-container-20260921060318'
$mount = '01a04263-91e5-7603-bc01-c67e503da6b5:/data'

$script = @'
set -eo pipefail
source /data/260010081/conda-env.sh
eval "$(conda shell.bash hook)"
conda activate longlive-rag
cd /data/260010081/LongLive-RAG
exec python -u REPLACE_WITH_REAL_TRAIN_ENTRY_AND_ARGUMENTS
'@

if ($script -match 'REPLACE_WITH_REAL_TRAIN_ENTRY_AND_ARGUMENTS') {
    throw '先填写真实训练入口及参数；不要提交占位命令。'
}
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
$remoteCommand = 'echo ' + $encoded + ' | base64 -d | bash'

& $sco acp jobs create `
    --workspace-name share-space `
    --aec2-name share-cluster `
    --job-name $jobName `
    --container-image-url $image `
    --training-framework pytorch `
    --worker-spec n6ls.iu.i40.4.16c256g `
    --worker-nodes 1 `
    --priority normal `
    --quota-type reserved `
    --storage-mount $mount `
    --command $remoteCommand
if ($LASTEXITCODE -ne 0) { throw 'ACP 提交失败；检查 CLI 报错，不要盲目重试。' }
```

实测初始化顺序是 `source conda-env.sh`、`eval "$(conda shell.bash hook)"`、`conda activate longlive-rag`。使用 `set -eo pipefail`；此前启用 `set -euo pipefail` 的 Conda 验证失败。CCI 的 `sleep 4h` 和 SSH 服务不是 ACP 训练入口，不要复制过来。

## 确认、监控和停止

CLI 返回 `job pt-xxxxxxxx submitted successfully` **只表示提交成功**。记录返回的 `pt-...` 平台任务名（不是 `$jobName` 展示名，也不是 UID），跟踪到 `RUNNING`、`SUCCEEDED` 或 `FAILED`；`STARTING` 不算已运行。

```powershell
$job = 'pt-xxxxxxxx'  # 替换为 create 返回的平台任务名
& $sco acp jobs describe --workspace-name share-space -o json $job
& $sco acp jobs get-workers --workspace-name share-space $job
& $sco acp jobs stream-logs --workspace-name share-space --follow $job
```

检查 Worker 为 `Running`，并在日志中确认 GPU、训练进度和检查点；仅看 `cuda_devices=4` 不能证明正在训练。快速失败时 SCO 可能只返回调度事件，应查看平台的 **pytorch 容器日志**。按 `Ctrl+C` 退出日志跟随不会停止云端任务。运行中进入容器，先由 `get-workers` 取完整 Worker 名称：

```powershell
& $sco acp jobs exec --workspace-name share-space --worker-name '<完整 Worker 名称>' $job
```

只在明确要求停止**自己的目标任务**时执行并确认结果：

```powershell
& $sco acp jobs stop --workspace-name share-space $job
& $sco acp jobs describe --workspace-name share-space -o json $job
```

`SUCCEEDED` 表示程序正常退出，`FAILED` 应结合容器日志排查；它们都不是仍在占卡的任务。本仓库 CCI 管理台目前**不负责 ACP 自动续跑**，ACP 由 SCO CLI 和平台状态独立管理。AI 完成后要回报平台任务名、镜像/环境、规格及配额、Worker 状态、训练证据，以及任务是否仍在运行。
