# SCO Control Skill

Codex skill for operating SenseCore SCO CLI workflows with a compact, layered command guide.

The skill focuses on high-frequency SCO operations:

- ACP training job submit, inspect, logs, worker lookup, and quota selection
- CCI app listing, describing, and YAML creation patterns
- AEC2 cluster/spec queries
- AFS volume, directory, quota, and ACL management
- CCR/AOSS-style container image listing, build, namespace, and label workflows

The bundled references are intentionally small. They capture practical command patterns and known local CLI behavior without vendoring the full SCO documentation.

## Installation

Install the skill by copying or linking the `sco-control` directory into your Codex skills directory.

Copy:

```bash
mkdir -p ~/.codex/skills
cp -R sco-control ~/.codex/skills/sco-control
```

Symlink for local development:

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/sco-control" ~/.codex/skills/sco-control
```

Restart Codex or reload skills if your client requires it. Invoke explicitly with:

```text
Use $sco-control to list my CCI apps and inspect my ACP jobs.
```

## Usage Notes

This skill assumes the `sco` CLI is already installed and authenticated in the user's environment.

For read-only discovery, prefer:

```bash
sco config list
sco acp jobs list --workspace-name "$WORKSPACE" --page-size 20 -o table
sco cci apps list --workspace-name "$WORKSPACE" --keyword "$USER_ID" --page-size 20
sco afs volume list
sco ccr images list
```

For ACP quota selection:

```bash
--quota-type reserved  # standard/reserved quota, default
--quota-type spot      # idle/preemptible quota
```

For CCI, `--keyword` fuzzy-matches `user_name`, `display_name`, and `name`, so use runtime user ids or app naming patterns as query values. Do not hard-code personal ids in the skill.

AFS content lives in `sco-control/references/aec2-afs.md` and covers volume listing, directory listing, quota rules, ACL rules, ACP mount strings, and CCI `PV_AFS` mounts.

AOSS-related image build context handling lives in `sco-control/references/images.md`. The SCO CLI commands are expressed through `sco ccr`; object-store build contexts should be passed through runtime variables such as `AOSS_CONTEXT_URL`, not committed as signed or tenant-specific URLs.

## Repository Layout

```text
sco-control/
├── SKILL.md
├── agents/openai.yaml
└── references/
    ├── acp.md
    ├── acp_submit_template.sh
    ├── aec2-afs.md
    ├── cci.md
    ├── images.md
    └── run_sco_pattern.md
```

## Safety

The skill treats list/describe/log operations as the default. Mutating operations such as create, update, delete, start, stop, quota changes, ACL changes, and image builds should be run only after explicit user intent.

Sensitive local files, unpacked vendor documentation, and personal shell templates are excluded from version control by `.gitignore`. The included `acp_submit_template.sh` is a sanitized reference template; it intentionally uses environment variables for workspace, cluster, storage, image, and project path values.
