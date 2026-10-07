# SCO CCI 实例管理台

使用一个 Python 进程管理多个 SenseCore CCI：独立查询状态、定时启停、周期续杯、人工操作和日志。GPU余量在独立线程采集，只用于图表展示，不决定实例能否启动。网页默认地址：<http://127.0.0.1:18766>。

仓库保留当前部署的真实配置。换机器或账号运行前，先检查 `config/` 中的可执行文件路径、Profile、订阅、Workspace、镜像和存储资源；账号凭据通过本机 `sco init` 配置，不在仓库中保存。

## 给接手本项目的 AI

先阅读 [AGENTS.md](AGENTS.md)、本README及当前配置，再修改代码。`sco-skill/` 是随仓库提供的技能包，实际需要安装的是其中的 **`sco-control/`**，不是整个项目。

在仓库根目录执行以下命令，将技能完整复制到用户技能目录（不使用软链接）：

```bash
mkdir -p "$HOME/.codex/skills"
# 先检查同名目录，已有安装时比对后更新，避免盲目覆盖。
ls -ld "$HOME/.codex/skills/sco-control"
# 目标不存在时执行：
cp -R sco-skill/sco-control "$HOME/.codex/skills/sco-control"
```

Codex重新加载技能后可用 `$sco-control` 调用。其他AI工具可将 `sco-skill/sco-control/SKILL.md` 及其references作为操作参考。Skill提供SCO操作说明；应用运行依赖SCO CLI及本机认证，不依赖AI会话。

## 文件职责

```text
app.py                         唯一服务入口、HTTP服务器与进程锁
controller/
  service.py                   实例注册、配置保存、独立GPU监控
  instance.py                  每实例状态采集、操作队列、续杯执行
  policy.py                    纯调度判断：时间窗口、周期、暂停状态
  runtime_api.py               只读CCI OpenAPI，获取平台容器运行时间
  config.py                    YAML校验与中文注释生成
  storage.py                   原子JSON快照与JSONL日志
  http.py                      网页与HTTP API
sco_client.py                  SCO CLI封装、CCI配置渲染与DNAT
config/
  defaults.yaml                当前SCO上下文、默认命令、存储、端口
  instances.yaml               当前实例列表及各实例调度设置
  gpu-monitor.yaml             GPU采样配置
sco-skill/sco-control/         供AI安装的Skill及命令参考
docs/acp-training.md           ACP训练任务的AI接手、提交和监控手册
templates/console.html         单页网页结构
static/console.js              实例卡片、编辑、日志抽屉、GPU图表
static/console.css             桌面与手机样式
launchd/*.plist.example        macOS后台服务模板
tests/                        离线测试
runtime/                      运行状态、指标及备份（不提交）
logs/                         操作和事件日志（不提交）
```

旧版本迁移已经完成，旧代码和配置备份保存在本机 `runtime/migration-v2/`。业务状态和日志无需数据库。

## 安装与启动

支持Python 3.10及以上；进程锁兼容Windows、macOS和Linux。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sco version
sco doctor
# 未认证时在自己的终端输入凭据：
sco init

# 前台运行；会执行配置中启用的调度
.venv/bin/python app.py --host 127.0.0.1 --port 18766
# 演练模式，仅查询平台，变更命令不执行
.venv/bin/python app.py --dry-run --port 18766
```

Windows PowerShell可使用Conda环境：

```powershell
conda create -n oneday python=3.12 pip -y
conda activate oneday
python -m pip install -r requirements.txt
sco version
sco doctor
# 未认证时交互输入Access Key；不要把密钥写入仓库。
sco init --profile default

# 正常模式会执行配置中启用的调度；首次验证建议先使用演练模式。
python app.py --dry-run --host 127.0.0.1 --port 18766
python app.py --host 127.0.0.1 --port 18766
```

若PowerShell尚不能识别 `conda activate`，先执行 `conda init powershell` 并重新打开终端。SCO默认安装到
`$HOME\.sco\bin\sco.exe`；安装器需要将该目录加入用户 `PATH`，项目配置也可直接填写其绝对路径。

同一个数据目录只允许运行一个管理进程。先停止后台服务再前台运行。`--root /path/to/project` 可指定完整配置、页面与数据目录；仅换端口不意味着隔离数据。

ACP训练任务的已验证挂载、Conda激活和PowerShell提交步骤见 [ACP训练任务手册](docs/acp-training.md)。ACP与本管理台的CCI调度相互独立。

## 网页使用

实例页右上角“实例数 · 采样间隔 · 编辑”可修改所有实例的状态采样间隔（10–3600秒），保存后立即生效。GPU余量采样独立设置，当前为10秒。启动和重启等待Running / Ready的上限为20分钟，期间每5秒确认状态；失败暂停机制仍然有效。

### 添加已有实例

点击“添加实例”，填写 **Workspace + 实例名称**。程序查询平台，自动读取UID、显示名称、集群、规格和镜像；验证成功即保存并开始监控，无需重启。默认关闭定时启停、周期续杯和DNAT管理。添加不会创建云端容器。

`key` 是本地唯一标识，隔离状态和日志；`workspace + name` 定位云端应用；`uid` 由平台返回，替换实例后会变化。

### 调度和计时

- 每个实例的每日启动、每日停止、周期续杯分别有开关。
- 周期范围1–230分钟，到期停止；平台确认已停止后等待5秒直接启动，不探测GPU余量。
- 启停都开启时，定时停止时段优先，不会被自动续杯拉起。
- 周期修改立即按本轮运行时间判断，不重置计时；缩短到已运行时长以下会触发续杯。
- 修改定时时间仅作用于下一次未来边界，不补执行过去时间点。
- 计时优先取运行中主容器的 `last_started_time`，其次取 `life_span`。API失败显示过期时间并暂缓续杯，不能用应用创建时间或本地服务启动时间代替。

### 失败暂停与恢复

启动、停止或重启失败后，保存失败暂停状态，不会反复提交。监控继续进行，下一次对应定时事件可以重新尝试。

卡片出现 **“恢复自动运行”** 时，点击会清除失败暂停和人工暂停，立即重新读取平台状态并按当前配置调度。恢复**不重置本轮计时**：例如周期54分钟、已运行70分钟，恢复后即开始一次续杯；停止时段则保持停止，周期关闭则不会强制续杯。

恢复本身也进入操作队列，执行中不能重复点击。旧失败记录保留在日志中；若后续操作再次失败，重新暂停，需再次人工恢复或等待新定时事件。

### 编辑和镜像

各实例点击编辑后可保存或放弃，自动刷新不覆盖草稿。名称和调度参数保存后立即生效；镜像保存为待应用，下一次启动或人工重启时应用。普通stop/start保留DNAT；镜像替换可能删除并创建同名实例，程序备份配置并恢复新UID的DNAT。

启动命令、AFS、Secret、端口等只在 `defaults.yaml` 配置。中文字段注释在网页保存时会重新生成。

### 日志和进度

每个实例独立记录：

```text
runtime/instances/<key>/state.json
logs/instances/<key>/events.jsonl
logs/instances/<key>/operations.jsonl
runtime/gpu/<cluster-key>/metrics.jsonl
```

操作阶段包括排队、停止提交、等待停止、5秒等待、启动提交、等待Ready及最终结果。平台拒绝或超时会显示错误。管理进程重启会把未结束操作标记为中断，重新查询平台；不会把命令已提交当成操作完成。

## macOS launchd

模板 `launchd/com.sco.cci-manager.plist.example` 中先替换 `/ABSOLUTE/PROJECT/PATH` 为当前仓库绝对路径，再安装：

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp launchd/com.sco.cci-manager.plist.example "$HOME/Library/LaunchAgents/com.sco.cci-manager.plist"
# 编辑安装后的路径，再校验
plutil -lint "$HOME/Library/LaunchAgents/com.sco.cci-manager.plist"
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.sco.cci-manager.plist"
launchctl enable gui/$(id -u)/com.sco.cci-manager
launchctl print gui/$(id -u)/com.sco.cci-manager
launchctl kickstart -k gui/$(id -u)/com.sco.cci-manager
launchctl bootout gui/$(id -u)/com.sco.cci-manager
```

原macOS部署沿用标签 `com.oneday.sco-cci-manager`，管理它时将命令中的标签替换为该值。Windows不使用launchd。不要同时加载两个标签管理同一目录；重载前确认没有执行中的操作。停止本地服务不会停止云端CCI。Mac睡眠、关机或退出登录期间无法准时调度。

## API与测试

```bash
curl -s http://127.0.0.1:18766/api/status | jq
curl -s http://127.0.0.1:18766/api/instances/h100/operations | jq
curl -s http://127.0.0.1:18766/api/instances/h100/events | jq
curl -s 'http://127.0.0.1:18766/api/gpu/debug-cluster/metrics?hours=8' | jq

# 实例key按实际配置替换。以下操作会影响自动调度或云端实例。
curl -X POST http://127.0.0.1:18766/api/instances/h100/actions/resume -H 'Content-Type: application/json' -d '{}'
# actions下还支持 start、stop、restart。

.venv/bin/python -m unittest discover -s tests -v
node --check static/console.js
git diff --check
```

测试不操作真实云端资源。浏览器轮询本地缓存，云端采样有独立步长；人工操作执行中会临时查询确认结果。GPU采样失败画断点，不填零。该网页没有多用户登录鉴权，默认仅绑定本机回环地址。

## 提交范围

按项目约定提交实际 `config/*.yaml` 和 `sco-skill/`。不提交SCO密钥、`.env`、日志、运行状态、备份、虚拟环境和Python缓存。`.github/workflows/check.yml` 会运行离线测试和JavaScript语法检查。上传前执行 `git status --short`，核对新增文件和删除文件后再提交。
