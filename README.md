# Trace2Skill Distiller

**从 AI 编程会话中自动提炼可复用的技能知识。**

分析你在 [OpenCode](https://github.com/opencode-ai/opencode) 中的编程会话记录，用 LLM 提取可操作的实践经验和技能规则，写入 `SKILL.md` 供 AI 编程助手自动发现和复用。

## 设计理念

AI 编程助手每天都在帮你写代码、调 Bug、做调研——但交互经验随会话结束而消散。同样的坑踩两次、同样的配置步骤重复摸索。

本项目灵感来自 **Trace2Skill** ([Ni et al., 2026](https://arxiv.org/abs/2603.25158))：让 LLM 分析执行轨迹，从中蒸馏可迁移的技能。核心发现：**用自然语言存储技能（不需要微调），小模型提取的技能就能让大模型性能提升数十个百分点。**

## 处理流水线

```
OpenCode 会话历史
       │
       ▼
┌──────────────────────────────────────────────┐
│  数据采集 (mining/)                           │
│                                               │
│  L0  智能压缩 — bash→命令+关键行, read→路径+行数 │
│  L1  快速LLM — 意图边界检测 + 逐块结构化提取   │
│  L2  快速LLM — 会话级聚合 → TrajectorySummary │
└───────────────────┬──────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  数据分析 (analysis/)                         │
│                                               │
│  Step 1.5  主题聚类 — 按技术主题分组相似轨迹    │
│  Step 2    技能蒸馏 — 强LLM提炼技能规则+正文   │
└───────────────────┬──────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  结果输出 (output/)                           │
│                                               │
│  SKILL.md 技能文件 + _index.md 索引            │
│  HTML 蒸馏报告 + 增量状态持久化                │
└──────────────────────────────────────────────┘
```

### 双模型架构

| 角色 | 用途 | 调用频率 |
|------|------|----------|
| **fast** | 预处理、意图检测、聚类 | 每会话 3-5 次 |
| **strong** | 技能蒸馏、内容合并 | 每主题 1 次 |

便宜模型做大量预处理，只在蒸馏阶段用强模型，控制成本。

## 架构

四模块独立架构，通过 Protocol 接口解耦，依赖方向严格单向：

```
              ┌─────────────┐
              │    core/     │  配置 + 枚举 + 工具（零依赖）
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │    llm/      │  LLM 接驳（Provider 协议 + 高层 Client）
              └──────┬──────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌───────────┐ ┌───────────┐ ┌───────────┐
  │ mining/   │ │ analysis/ │ │  output/   │
  │ 数据采集  │ │ 数据分析  │ │  结果输出  │
  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
        │             │             │
        └─────────────┼─────────────┘
                      ▼
              ┌───────────────┐
              │ orchestrator/ │  组合四大模块
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │    cli/       │  Click 薄壳
              └───────────────┘
```

**依赖规则**：
- `core` → 无依赖
- `llm` → core
- `mining` → llm, core
- `analysis` → mining.types, llm, core
- `output` → analysis.types, mining.types, llm, core
- 无循环，无反向依赖

### 扩展点

每个模块通过 Protocol 定义接口，替换实现只需实现对应 Protocol：

| 模块 | Protocol | 当前实现 | 可扩展为 |
|------|----------|----------|----------|
| LLM 接驳 | `LLMProvider` | `OpenAICompatibleProvider` (httpx) | Anthropic、Azure、自定义 HTTP |
| 数据采集 | `SessionSource` | `OpenCodeSource` (SQLite) | Claude Code JSONL、JSON 文件、API |
| 聚类策略 | `ClusterStrategy` | `SemanticClusterStrategy` (LLM) | Embedding 向量聚类、关键词匹配 |
| 蒸馏策略 | `DistillationStrategy` | `LLMDistillationStrategy` | 代码审查分析、架构决策提取 |
| 技能格式 | `SkillFormatter` | `SkillMdFormatter` (SKILL.md) | JSON、Confluence Wiki |
| 报告展示 | `ReportPresenter` | `HtmlReportPresenter` | 终端 Rich、Markdown |

## 安装

```bash
git clone https://github.com/Tudou77826/trace2skill_distiller.git
cd trace2skill_distiller
uv sync

# 初始化配置
trace2skill init \
  --api-key "your-api-key" \
  --base-url "https://api.example.com/v1" \
  --fast-model "gpt-4o-mini" \
  --strong-model "gpt-4o"
```

要求 Python >= 3.10，使用 [uv](https://github.com/astral-sh/uv) 管理依赖。

## 使用

```bash
# 预览单个会话的预处理结果
trace2skill inspect <session-id>

# 从指定项目提炼技能
trace2skill distill --project my-project

# 指定单个会话
trace2skill distill --session <session-id>

# 只做预处理（不调用强模型）
trace2skill distill --project my-project --step 1

# 试运行（展示结果但不写文件）
trace2skill distill --project my-project --dry-run

# 增量处理（只处理上次之后的新会话）
trace2skill distill --project my-project --incremental

# 查看提炼历史和已生成的技能
trace2skill status

# 定时任务（每天凌晨自动提炼）
trace2skill schedule start
```

每次蒸馏完成后生成 HTML 报告至 `~/.trace2skill/reports/`，包含会话筛选、主题分布、规则统计、LLM 开销等。

## 技能类型

蒸馏时 LLM 自动判断类型并选择对应的 Markdown 格式：

| 类型 | 说明 | 输出格式 |
|------|------|----------|
| `procedure` | 操作流程（安装、部署、配置） | 分步流程 + 注意事项 |
| `knowledge` | 业务理解（架构调研、项目结构） | 核心概念 + 关键关系 + 要点 |
| `checklist` | 注意事项（调试、安全） | MUST / WHEN→THEN / NEVER |
| `troubleshooting` | 调试排障 | 问题 → 排查路径 → 解决方案 |
| `reference` | 工具参考（配置项、API） | 配置表 + 示例 + 注意事项 |

## 输出示例

技能文件写入 `~/.trace2skill/skills/<project>/<topic-id>/SKILL.md`：

```markdown
---
name: opencode-setup
description: Install oh-my-opencode CLI with appropriate model flags.
    Use when deploying opencode, configuring user subscriptions,
    or enabling z.ai.codingplan sync.
---

# oh-my-opencode 自动化安装与订阅配置

## 步骤
1. **检查前置工具**：运行 `opencode --version`，确认版本兼容
2. **收集用户订阅信息**：获取是否订阅 Gemini、Claude、OpenAI 等模型
3. **构建安装命令**：根据结果拼装命令及标志
4. **执行安装**：运行命令，观察返回信息
5. **验证**：使用 `oh-my-opencode status` 检查状态

## 注意事项
- 订阅标志必须显式设置（yes/no），否则可能导致运行时错误
```

`description` 字段遵循 `[What it does]. Use when [scenario].` 格式，英文编写含触发词，AI Agent 可据此自动发现和匹配。

## 配置

`~/.trace2skill/config.yaml`：

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
  cron: "0 3 * * *"

skill_output_dir: "~/.trace2skill/skills"
max_rules_per_skill: 15
clustering_max_topics: 8
```

环境变量覆盖（或写入 `~/.trace2skill/.env`）：

```bash
TRACE2SKILL_API_KEY=sk-xxx
TRACE2SKILL_BASE_URL=https://api.example.com/v1
TRACE2SKILL_FAST_MODEL=gpt-4o-mini
TRACE2SKILL_STRONG_MODEL=gpt-4o
TRACE2SKILL_VERIFY_SSL=true
TRACE2SKILL_PROXY=
```

## 项目结构

```
src/trace2skill_distiller/
├── core/                            # 共享基础
│   ├── config.py                    # DistillConfig + 子配置 (LLMConfig, MiningConfig, ...)
│   ├── types.py                     # Label, SkillType, RuleType 枚举
│   └── utils.py                     # estimate_tokens, truncate_to_token_budget
│
├── llm/                             # 模块 1：LLM 接驳
│   ├── base.py                      # Protocol: LLMProvider
│   ├── client.py                    # LLMClient (重试/JSON修复/token追踪)
│   ├── types.py                     # LLMResponse, LLMUsageStats
│   └── providers/
│       └── openai_compatible.py     # httpx 实现 (代理/SSL/自定义header)
│
├── mining/                          # 模块 2：数据采集
│   ├── types.py                     # Session, TrajectorySummary, CleanedSession
│   ├── sources/
│   │   ├── base.py                  # Protocol: SessionSource
│   │   └── opencode.py             # OpenCode SQLite + CLI export
│   ├── preprocess/
│   │   ├── compress.py              # L0 智能压缩 (纯规则)
│   │   ├── extract.py               # L1/L2 LLM 提取
│   │   └── pipeline.py             # run_pipeline / run_batch
│   └── mining_facade.py            # Protocol MiningLayer + DefaultMiningLayer
│
├── analysis/                        # 模块 3：数据分析
│   ├── types.py                     # TopicCluster, TopicSkill, SkillRule
│   ├── clustering/
│   │   ├── base.py                  # Protocol: ClusterStrategy
│   │   └── semantic.py             # LLM 语义聚类
│   ├── distillation/
│   │   ├── base.py                  # Protocol: DistillationStrategy
│   │   └── llm_distill.py          # LLM 技能蒸馏
│   └── analysis_facade.py          # Protocol AnalysisLayer + DefaultAnalysisLayer
│
├── output/                          # 模块 4：结果输出
│   ├── types.py                     # DistillReport, RunState, ShapingResult
│   ├── formatters/
│   │   ├── base.py                  # Protocol: SkillFormatter
│   │   └── skill_md.py             # SKILL.md 格式 (YAML frontmatter)
│   ├── presenters/
│   │   ├── base.py                  # Protocol: ReportPresenter
│   │   └── html_report.py          # HTML 报告
│   ├── state.py                     # StateManager (增量状态持久化)
│   └── output_facade.py            # Protocol OutputLayer + DefaultOutputLayer
│
├── orchestrator/
│   └── pipeline.py                  # DistillPipeline (组合四大模块)
│
└── cli/
    └── main.py                      # Click CLI (distill/inspect/status/schedule)
```

## 可靠性

- **语义压缩而非硬截断** — 工具输出压缩 100 倍，保留关键信息
- **JSON 自修复** — 自动修复 LLM 输出的截断 JSON
- **指数退避重试** — 网络错误最多重试 3 次
- **错误隔离** — 单个会话/主题失败不阻塞其他处理
- **Token 预算感知** — 发送前估算 token 数，超预算自动压缩
- **增量处理** — 已处理的会话不重复消耗 token

## 致谢

- **Trace2Skill** — Ni et al., "Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills", 2026. ([arXiv:2603.25158](https://arxiv.org/abs/2603.25158))

## License

MIT
