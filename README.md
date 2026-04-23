# Trace2Skill Distiller

Distill reusable coding skills from your OpenCode session history.

## What It Does

Every time you use OpenCode (or similar AI coding tools), a detailed session trace is recorded — tool calls, reasoning, errors, code changes. Trace2Skill Distiller analyzes these traces through a multi-level pipeline and extracts actionable rules:

- **ALWAYS** rules from successful patterns
- **NEVER / AVOID** rules from failure lessons
- **WHEN ... THEN** conditional rules from observed strategies

Rules are written to `SKILL.md` files that can be loaded back into your AI coding assistant.

## Pipeline

```
OpenCode Session History
         │
         ▼
  ┌─────────────────────────────────────────────┐
  │  Level 0: Smart Compression (code only)      │
  │                                              │
  │  bash outputs → command + key lines          │
  │  read calls  → file path + line count        │
  │  write/edit  → file path + diff skeleton     │
  │  glob/grep   → match count + first results   │
  │  reasoning   → conclusion only               │
  │                                              │
  │  1.7 MB raw → 74 KB compressed (4.3%)        │
  └──────────────────┬──────────────────────────┘
                     │
         ┌───────────┴──────────────┐
         ▼                          ▼
  ┌──────────────┐          ┌──────────────┐
  │  Level 1     │ quick    │  Level 1b    │
  │  Intent      │ LLM      │  Per-block   │
  │  Boundary    │──────────│  Extraction  │
  │  Detection   │          │              │
  └──────┬───────┘          └──────┬───────┘
         │                         │
         ▼                         ▼
  ┌──────────────────────────────────────────┐
  │  Level 2: Session Aggregation (quick LLM) │
  │  → Structured narrative: what happened,   │
  │    problems, decisions, lessons           │
  └──────────────────┬───────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────┐
  │  Step 2: Multi-Dimension Distillation     │
  │  (strong LLM)                             │
  │                                           │
  │  architecture · implementation            │
  │  debugging · testing                      │
  │                                           │
  │  → Skill rules with confidence scores     │
  └──────────────────┬───────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────┐
  │  Step 3: Merge & Write SKILL.md           │
  │  (dedup, conflict resolve, prioritize)    │
  └──────────────────────────────────────────┘
```

## Install

```bash
# Clone and install
git clone https://github.com/Tudou77826/trace2skill_distiller.git
cd trace2skill_distiller
uv sync

# Initialize (set LLM API credentials)
trace2skill init \
  --api-key "your-api-key" \
  --base-url "https://api.example.com/v1" \
  --fast-model "model-id" \
  --strong-model "model-id"
```

Requires Python >= 3.10. Uses [uv](https://github.com/astral-sh/uv) for package management.

## Usage

```bash
# Analyze a single session
trace2skill inspect <session-id>

# Distill skills from a specific project
trace2skill distill --project my-project

# Distill from a specific session
trace2skill distill --session <session-id>

# Only preprocess (no strong-LLM calls, cheaper)
trace2skill distill --project my-project --step 1

# Dry run (show rules without writing files)
trace2skill distill --project my-project --dry-run

# Incremental (only process new sessions)
trace2skill distill --project my-project --incremental

# Check distillation history
trace2skill status

# Scheduled processing (daemon mode)
trace2skill schedule start
```

## Configuration

Config stored at `~/.trace2skill/config.yaml`:

```yaml
models:
  fast:
    model: "gpt-4o-mini"
    max_tokens: 4096
  strong:
    model: "gpt-4o"
    max_tokens: 8192

opencode:
  db_path: "~/.local/share/opencode/opencode.db"

filter:
  min_messages: 5
  min_tools: 3

scheduler:
  enabled: false
  cron: "0 3 * * *"       # 3 AM daily
  strategy: "incremental"
```

API credentials are read from `~/.trace2skill/.env` or environment variables `TRACE2SKILL_API_KEY` / `TRACE2SKILL_BASE_URL`.

## Output

Skill files are written to `~/.trace2skill/skills/<project>/SKILL.md`:

```markdown
# Skill: Project Exploration & Tool Management

## 通用规则 (General)
- ALWAYS: 确认工具可用后再调用...
- WHEN tool unavailable THEN 自动切换到替代方案...

## 项目特定规则 (Project: my-app)
- ALWAYS: 读取语言核心入口文件以推断构建指令...

## 失败教训 (Lessons Learned)
- AVOID: 不要在 Bash 中使用 Windows 路径分隔符...

## 变更记录
- 2026-04-24: 初始创建...
```

## Architecture

```
src/trace2skill_distiller/
├── cli/main.py          # Click CLI entry point
├── config.py            # Pydantic config + env overrides
├── db.py                # OpenCode SQLite + export access
├── llm.py               # httpx LLM client (retry, JSON repair, token budget)
├── models.py            # Pydantic data models
├── pipeline/
│   ├── preprocess.py    # Level 0: smart compression
│   ├── extract.py       # Level 1 & 2: LLM-based extraction
│   └── __init__.py      # Pipeline orchestrator
└── engine/
    ├── distill.py       # Step 2: multi-dimension distillation
    └── merge.py         # Step 3: merge & write SKILL.md
```

## Reliability

- **No hard truncation** — tool outputs are semantically compressed (100x ratio)
- **JSON repair** — automatically fixes truncated LLM JSON output
- **Retry with backoff** — network errors retry up to 3 times
- **Error isolation** — single session/dimension failure doesn't block others
- **Token budget awareness** — estimates tokens before sending, compresses if needed
- **100K context** — designed for modern large-context models

## License

MIT
