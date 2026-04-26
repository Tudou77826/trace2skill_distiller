# Trace2Skill Distiller

**从 AI 编程会话中自动提炼可复用的技能知识。**

Trace2Skill Distiller 分析你在 [OpenCode](https://github.com/opencode-ai/opencode) 中的编程会话记录，从中提取可操作的实践经验和技能规则，写入 `SKILL.md` 文件供 AI 编程助手自动发现和复用。

## 为什么需要它？

AI 编程助手每天都在帮你写代码、调 Bug、做调研——但你和 AI 的交互经验会随着会话结束而消散。同样的坑踩两次、同样的配置步骤重复摸索，这些都可以避免。

Trace2Skill Distiller 的设计灵感来源于 **Trace2Skill** ([Ni et al., 2026](https://arxiv.org/abs/2603.25158))——一篇来自 2026 年的研究论文，提出了从智能体执行轨迹中蒸馏可迁移技能的方法论：

> 核心思想：让多个并行子智能体（sub-agents）分析多样化的执行轨迹池，从每条轨迹中提取局部经验教训（trajectory-local lessons），再通过归纳推理层次化地整合为统一、无冲突的技能目录（skill directory）。

Trace2Skill 论文的关键发现：

- **无需参数更新** — 技能以自然语言形式存储，不需要微调模型
- **跨模型迁移** — 用小模型（Qwen3.5-35B）提取的技能，可以让大模型（Qwen3.5-122B）在 WikiTableQuestions 上提升 **57.65 个百分点**
- **层次化整合** — 通过归纳推理消除局部经验之间的冲突，形成全局一致的技能体系

本项目将论文中的学术方法落地到日常编程场景：自动收集你在 OpenCode 中的会话轨迹，用 LLM 提取哪些做法有效、哪些做法有问题，提炼成结构化的技能文档供 AI 助手自动发现和复用。

## 工作原理

```
OpenCode 会话历史
       │
       ▼
┌──────────────────────────────────────────────┐
│  Level 0: 智能压缩 (纯规则，无 LLM)           │
│                                               │
│  bash 输出  → 命令 + 关键行                    │
│  read 调用  → 文件路径 + 行数                   │
│  write/edit → 文件路径 + diff 骨架              │
│  reasoning  → 仅保留结论                       │
│                                               │
│  1.7 MB 原始数据 → 74 KB 压缩后 (4.3%)          │
└───────────────────┬──────────────────────────┘
                    │
        ┌───────────┴──────────────┐
        ▼                          ▼
 ┌──────────────┐           ┌──────────────┐
 │  Level 1a    │  快速     │  Level 1b    │
 │  意图边界    │──LLM─────│  逐块提取    │
 │  检测        │           │              │
 └──────┬───────┘           └──────┬───────┘
        │                          │
        ▼                          ▼
 ┌────────────────────────────────────────────┐
 │  Level 2: 会话聚合 (快速 LLM)               │
 │  → 结构化叙事：发生了什么、遇到的问题、      │
 │    关键决策、经验教训                        │
 └───────────────────┬────────────────────────┘
                     │
                     ▼
 ┌────────────────────────────────────────────┐
 │  Step 1.5: 主题聚类 (快速 LLM)              │
 │  → 按技术主题分组相似轨迹                    │
 │  → 例如：JWT 认证 / 部署配置 / 调试模式     │
 └───────────────────┬────────────────────────┘
                     │
                     ▼
 ┌────────────────────────────────────────────┐
 │  Step 2: 技能蒸馏 (强 LLM)                  │
 │                                             │
 │  1. 判断技能类型 (procedure/knowledge/      │
 │     checklist/troubleshooting/reference)    │
 │  2. 生成英文 description (含触发词)          │
 │  3. 按类型生成对应格式的 Markdown 正文       │
 │  4. 提取统计用规则                           │
 └───────────────────┬────────────────────────┘
                     │
                     ▼
 ┌────────────────────────────────────────────┐
 │  Step 3: 写入 SKILL.md                      │
 │  (YAML frontmatter + LLM 生成的动态正文)    │
 └────────────────────────────────────────────┘
```

### 双模型架构

| 模型角色 | 用途 | 调用频率 |
|----------|------|----------|
| **fast** (快速模型) | 预处理、意图检测、聚类 | 每个会话 3-5 次 |
| **strong** (强模型) | 技能蒸馏、内容生成 | 每个主题 1 次 |

使用便宜模型做大量预处理，只在最终蒸馏阶段使用强模型，控制成本。

## 技能类型

蒸馏时 LLM 会自动判断技能属于哪种类型，并选择最适合的 Markdown 格式：

| 类型 | 说明 | 输出格式 |
|------|------|----------|
| `procedure` | 操作流程（安装、部署、配置） | 分步流程 + 注意事项 |
| `knowledge` | 业务理解（架构调研、项目结构） | 核心概念 + 关键关系 + 要点 |
| `checklist` | 注意事项（调试、安全） | MUST / WHEN→THEN / NEVER 清单 |
| `troubleshooting` | 调试排障（特定问题的解决路径） | 问题 → 排查路径 → 解决方案 |
| `reference` | 工具参考（配置项、API 用法） | 配置表 + 示例 + 注意事项 |

## 安装

```bash
# 克隆并安装
git clone https://github.com/Tudou77826/trace2skill_distiller.git
cd trace2skill_distiller
uv sync

# 初始化配置（设置 LLM API 凭证）
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

# 只做预处理（不调用强模型，更省）
trace2skill distill --project my-project --step 1

# 试运行（展示结果但不写文件）
trace2skill distill --project my-project --dry-run

# 增量处理（只处理上次之后的新会话）
trace2skill distill --project my-project --incremental

# 按日期范围筛选
trace2skill distill --project my-project --from 2025-01-01 --to 2025-03-31

# 查看提炼历史和已生成的技能
trace2skill status

# 定时任务（守护进程模式，每天凌晨自动提炼）
trace2skill schedule start
```

每次蒸馏完成后，除了写入 SKILL.md 技能文件，还会在 `~/.trace2skill/reports/` 下生成一份 HTML 蒸馏报告，包含会话筛选结果、主题聚类分布、提炼规则统计、LLM 调用开销等详细信息，方便在浏览器中直观查看本次蒸馏的全貌。

## 输出示例

技能文件写入 `~/.trace2skill/skills/<project>/<topic-id>/SKILL.md`：

### checklist 类型

```markdown
---
name: test-execution
description: Execute test suite in ultra-work mode with explicit confirmation
    and real-time feedback. Use when running tests in ultra-work, debugging
    test execution, or needing user-visible status.
---

# Ultra-Work 模式下执行测试套件检查清单

## MUST
- 明确激活 ultra-work 模式
- 在启动测试套件前向用户发送确认提示并等待回复
- 在每个测试步骤完成后提供实时状态更新

## WHEN → THEN
- **When** 用户发送 run test suite 请求 → **Then** 弹出确认对话框
- **When** 测试执行出现异常 → **Then** 立刻报告错误并提供上下文

## NEVER
- 永远不要在未得到用户确认的情况下直接启动测试
```

### procedure 类型

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
- 安装完成后建议重新启动终端以刷新环境变量
```

### description 自动发现

YAML frontmatter 中的 `description` 字段遵循固定格式：

```
[What it does]. Use when [scenario 1], [scenario 2], or [scenario 3].
```

- 英文编写，包含具体触发词
- AI Agent 可据此自动匹配和触发技能
- 限 200 字符

## 配置

配置文件位于 `~/.trace2skill/config.yaml`：

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
  export_command: "opencode export"

filter:
  min_messages: 5       # 最低消息数门槛
  min_tools: 3          # 最低工具调用数门槛

scheduler:
  enabled: false
  cron: "0 3 * * *"     # 每天凌晨 3 点
  strategy: "incremental"
  min_new_sessions: 3   # 新会话不足 3 个则跳过

skill_output_dir: "~/.trace2skill/skills"
max_rules_per_skill: 15
clustering_min_size: 1
clustering_max_topics: 8
```

API 凭证通过 `~/.trace2skill/.env` 或环境变量配置：

```bash
TRACE2SKILL_API_KEY=sk-xxx
TRACE2SKILL_BASE_URL=https://api.example.com/v1
TRACE2SKILL_FAST_MODEL=gpt-4o-mini
TRACE2SKILL_STRONG_MODEL=gpt-4o
TRACE2SKILL_VERIFY_SSL=true
TRACE2SKILL_PROXY=              # 可选代理
```

## 项目结构

```
src/trace2skill_distiller/
├── cli/main.py              # Click CLI 入口 (distill/inspect/status/schedule)
├── config.py                # Pydantic 配置 + 环境变量覆盖
├── db.py                    # OpenCode SQLite 数据读取
├── llm.py                   # httpx LLM 客户端 (重试/JSON修复/token预算)
├── models.py                # Pydantic 数据模型
├── pipeline/
│   ├── preprocess.py        # Level 0: 智能压缩
│   └── extract.py           # Level 1 & 2: LLM 提取
└── engine/
    ├── cluster.py           # Step 1.5: 主题聚类
    ├── distill.py           # Step 2: 技能蒸馏 (类型判断 + 动态body生成)
    ├── merge.py             # Step 3: 写入 SKILL.md (frontmatter + body)
    └── report.py            # HTML 蒸馏报告生成
```

## 可靠性设计

- **语义压缩而非硬截断** — 工具输出智能压缩 100 倍，保留关键信息
- **JSON 自修复** — 自动修复 LLM 输出的截断 JSON
- **指数退避重试** — 网络错误最多重试 3 次
- **错误隔离** — 单个会话/主题失败不阻塞其他处理
- **Token 预算感知** — 发送前估算 token 数，超预算自动压缩
- **增量处理** — 已处理的会话不会重复消耗 token

## 致谢

本项目的设计受到以下学术工作的启发：

- **Trace2Skill** — Ni et al., "Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills", 2026. ([arXiv:2603.25158](https://arxiv.org/abs/2603.25158))

## License

MIT
