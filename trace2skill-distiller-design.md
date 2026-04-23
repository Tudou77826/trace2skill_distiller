# Trace2Skill Distiller — 落地设计文档

> 基于 OpenCode 会话导出的 Trace2Skill 蒸馏系统

## 一、核心思路

**一个脚本，两个模型，三步蒸馏。**

```
OpenCode 历史 Session
        │
        ▼
  ┌─────────────┐     快速模型（Haiku / Qwen-Turbo）
  │  Step 1     │ ←── 解析轨迹，分类标注，提取结构化摘要
  │  轨迹预处理  │
  └──────┬──────┘
         │  T⁺ / T⁻ + 结构化摘要
         ▼
  ┌─────────────┐     大模型（Sonnet / Qwen-Plus）
  │  Step 2     │ ←── 深度分析成功/失败原因，蒸馏为 Skill 补丁
  │  蒸馏分析    │
  └──────┬──────┘
         │  ΔS (Skill Patch)
         ▼
  ┌─────────────┐     快速模型（去重 + 冲突检测）
  │  Step 3     │ ←── 合并补丁，写入 Skill 文件
  │  合并写入    │
  └─────────────┘
```

## 二、OpenCode 数据模型

### 2.1 Session Export JSON 结构

通过 `opencode export <sessionID>` 导出的完整结构：

```jsonc
{
  "info": {
    "id": "ses_xxx",
    "slug": "quiet-cactus",          // 人类可读的 session 别名
    "projectID": "global",
    "directory": "D:\\dev\\project",  // 工作目录 = 项目标识
    "title": "Session 标题",          // 自动生成或用户设定的标题
    "version": "1.2.27",
    "summary": {
      "additions": 0,                 // git 行数统计
      "deletions": 0,
      "files": 0
    },
    "permission": [...],              // 权限配置
    "time": {
      "created": 1776509825674,       // 创建时间戳
      "updated": 1776509846985        // 最后更新时间戳
    }
  },
  "messages": [
    {
      "info": {
        "role": "user" | "assistant",
        "time": { "created": ts, "completed": ts },
        "agent": "build",             // 使用的 agent 类型
        "modelID": "glm-5.1",         // 模型 ID
        "providerID": "zhipuai",      // 提供商
        "mode": "build",              // 运行模式
        "cost": 0,                    // 花费
        "tokens": {
          "total": 15958,
          "input": 7578,
          "output": 60,
          "reasoning": 26,
          "cache": { "read": 8320, "write": 0 }
        },
        "finish": "tool-calls" | "stop",
        "parentID": "msg_xxx",        // 父消息（对话链）
        "path": { "cwd": "...", "root": "/" },
        "id": "msg_xxx",
        "sessionID": "ses_xxx"
      },
      "parts": [
        // === Part 类型全集 ===

        // 1. 文本内容
        { "type": "text", "text": "...", "time": { "start": ts, "end": ts } },

        // 2. 推理过程
        { "type": "reasoning", "text": "...", "time": { "start": ts, "end": ts } },

        // 3. 工具调用 — 核心数据
        {
          "type": "tool",
          "callID": "call_xxx",
          "tool": "bash|write|read|edit|glob|grep|task|...",
          "state": {
            "status": "completed" | "error",
            "input": { /* 工具特定参数 */ },
            "output": "..." | { ... },
            "title": "...",            // 人类可读标题
            "metadata": { ... },
            "time": { "start": ts, "end": ts }
          }
        },

        // 4. 文件变更记录
        { "type": "patch", "hash": "git_hash", "files": ["path/to/file"] },

        // 5. 子任务
        {
          "type": "subtask",
          "prompt": "...",
          "description": "...",
          "agent": "spec-checker"
        },

        // 6. 步骤标记
        { "type": "step-start" },
        { "type": "step-finish", "reason": "stop", "cost": 0, "tokens": {...} }
      ]
    }
  ]
}
```

### 2.2 SQLite 直接查询（批量分析用）

数据库路径：`~/.local/share/opencode/opencode.db`

关键表：
- `session`: 86 条记录，按 `directory` 分组为不同项目
- `message`: 1309 条，`data` JSON 含 role/tokens/cost/model
- `part`: 3984 条，`data` JSON 含 type/tool/state/input/output

Part 类型分布：
| 类型 | 数量 | 说明 |
|------|------|------|
| step-start/finish | 2295 | 步骤边界标记 |
| tool | 744 | 工具调用（最核心） |
| reasoning | 582 | 模型推理过程 |
| text | 297 | 文本输出 |
| patch | 64 | git 文件变更 |
| subtask | 2 | 子任务委派 |

工具分布：
| 工具 | 次数 | 输入 key |
|------|------|---------|
| read | 197 | filePath [, offset, limit] |
| bash | 196 | command, description [, timeout, workdir] |
| write | 115 | filePath, content |
| edit | 68 | filePath, oldString, newString |
| glob | 66 | pattern [, path] |
| todowrite | 31 | todos |
| grep | 17 | pattern [, path, include, output_mode] |
| task | 16 | prompt, subagent_type, description [, ...] |

## 三、系统架构

### 3.1 目录结构

```
~/.trace2skill/
├── config.yaml                 # 配置文件
├── cache/                      # 导出缓存
│   └── sessions/               # opencode export 的 JSON 原始文件
│       ├── ses_xxx.json
│       └── ...
├── distilled/                  # 蒸馏产物
│   ├── trajectories/           # 结构化轨迹摘要（Step 1 产出）
│   │   ├── ses_xxx.summary.json
│   │   └── ...
│   └── patches/                # Skill 补丁（Step 2 产出）
│       ├── 2026-04-24_patch-001.json
│       └── ...
├── skills/                     # Skill 仓库
│   ├── coding-patterns/
│   │   └── SKILL.md
│   ├── error-recovery/
│   │   └── SKILL.md
│   └── ...
└── logs/                       # 运行日志
    └── distill-2026-04-24.log
```

### 3.2 配置文件

```yaml
# ~/.trace2skill/config.yaml

models:
  fast:                          # 轨迹预处理 + 合并去重
    provider: "zhipuai"
    model: "glm-4-flash"        # 快速模型
    max_tokens: 4096

  strong:                        # 深度蒸馏分析
    provider: "zhipuai"
    model: "glm-5.1"            # 大模型
    max_tokens: 8192

opencode:
  db_path: "~/.local/share/opencode/opencode.db"
  export_command: "opencode export"

distillation:
  # 轨迹筛选
  min_messages: 5                # 最少消息数（过滤简单问候）
  min_tools: 3                   # 最少工具调用数
  projects: []                   # 空 = 所有项目，或指定 ["open-desk", "my-swam"]

  # 分析维度
  analysis_dimensions:
    - architecture               # 架构决策
    - implementation             # 实现细节
    - debugging                  # 调试策略
    - testing                    # 测试方法

  # Skill 输出
  skill_output_dir: "~/.trace2skill/skills"
  max_rules_per_skill: 15        # 每个 Skill 最多规则数（防膨胀）

  # 成功/失败判定
  success_signals:
    - "finish == 'stop'"          # 正常结束
    - "patch.files.length > 0"   # 有文件变更
    - "summary.additions > 0"    # 有新增代码
  failure_signals:
    - "error is not null"         # 有错误
    - "finish == 'error'"        # 异常结束
    - "state.status == 'error'"  # 工具执行出错
```

## 四、三步蒸馏 — 详细设计

### Step 1: 轨迹预处理（快速模型）

**输入**：原始 session export JSON
**输出**：结构化轨迹摘要 `trajectory.summary.json`
**模型**：快速模型

#### 1.1 轨迹筛选

从 SQLite 批量扫描 session 表，用简单规则过滤掉不值得分析的 session：

```python
def should_distill(session_meta) -> bool:
    """过滤掉无价值 session"""
    msg_count = count_messages(session_meta.id)
    tool_count = count_tools(session_meta.id)

    if msg_count < config.min_messages:
        return False
    if tool_count < config.min_tools:
        return False
    if session_meta.title in ["Greeting", "Agent", "New session"]:
        return False  # 无标题的泛 session
    return True
```

#### 1.2 轨迹结构化提取

对通过筛选的 session，用 `opencode export` 导出完整 JSON，然后调用快速模型做结构化提取：

```python
TRAJECTORY_EXTRACTION_PROMPT = """
你是一个开发轨迹分析器。分析以下 OpenCode 会话记录，提取结构化摘要。

## 会话原始数据
{session_json}

## 输出格式（严格 JSON）
{{
  "session_id": "会话 ID",
  "project": "项目名（从 directory 提取）",
  "task_intent": "用一句话描述用户想做什么",
  "task_domain": "任务领域（如：前端开发、后端API、配置部署、架构设计）",

  "trajectory": [
    {{
      "step": 1,
      "action": "探索代码库 | 编写代码 | 修改代码 | 执行命令 | 调试修复 | 文件搜索",
      "tools_used": ["read", "bash"],
      "key_decision": "这一步做了什么关键决策",
      "outcome": "成功 | 失败 | 部分成功"
    }}
  ],

  "code_changes": [
    {{
      "file": "文件路径",
      "operation": "create | modify | delete",
      "summary": "改了什么，为什么改"
    }}
  ],

  "tool_patterns": [
    {{
      "pattern": "工具使用模式描述",
      "tools": ["tool1", "tool2"],
      "frequency": 3,
      "context": "在什么场景下使用"
    }}
  ],

  "success_indicators": ["什么信号表明这次开发是成功的"],
  "failure_indicators": ["什么信号表明遇到了问题"],
  "key_insights": ["从这次开发中学到的关键洞察"]
}}

只输出 JSON，不要其他内容。
"""
```

#### 1.3 自动标注 T⁺ / T⁻

基于多信号融合自动判定轨迹质量：

```python
def label_trajectory(session_json, summary) -> str:
    """自动标注轨迹为 success/partial/failure"""
    signals = []

    # 信号1: patch（文件变更记录）
    patches = [p for msg in session_json["messages"]
               for p in msg["parts"] if p["type"] == "patch"]
    if patches:
        signals.append(("has_patch", 1.0))

    # 信号2: 最后一条 assistant 消息的 finish 状态
    last_assistant = [m for m in session_json["messages"]
                      if m["info"]["role"] == "assistant"][-1]
    if last_assistant["info"].get("finish") == "stop":
        signals.append(("clean_stop", 0.7))

    # 信号3: 有无 error
    has_error = any(m["info"].get("error") for m in session_json["messages"])
    if has_error:
        signals.append(("has_error", -1.0))

    # 信号4: 工具执行成功率
    tool_parts = [p for m in session_json["messages"]
                  for p in m["parts"] if p["type"] == "tool"]
    error_tools = [t for t in tool_parts
                   if t["state"]["status"] == "error"]
    if tool_parts:
        success_rate = 1 - len(error_tools) / len(tool_parts)
        signals.append(("tool_success_rate", success_rate - 0.5))

    # 信号5: summary 中的 key_insights（由快速模型判断）
    if summary.get("key_insights"):
        signals.append(("has_insights", 0.3))

    # 综合评分
    score = sum(weight for _, weight in signals)

    if score >= 0.8:
        return "success"
    elif score >= 0.3:
        return "partial"
    else:
        return "failure"
```

### Step 2: 蒸馏分析（大模型）

**输入**：一批 T⁺ / T⁻ 结构化轨迹摘要
**输出**：Skill 补丁（ΔS）
**模型**：大模型

#### 2.1 批量分析

将多个同领域的轨迹打包发给大模型，按分析维度拆分为并行任务：

```python
DISTILLATION_PROMPT = """
你是一个高级软件工程师，擅长从开发轨迹中提炼可复用的最佳实践。

## 背景
你正在分析一批 {project} 项目的 OpenCode 开发轨迹，目标是提炼出可复用的「Skill 规则」，
让 AI 编程助手在未来的类似任务中做得更好。

## 分析维度: {dimension}

## 成功轨迹 (T⁺)
以下是开发成功的轨迹摘要，它们包含了有效的开发模式：

{t_plus_summaries}

## 失败/问题轨迹 (T⁻)
以下是遇到问题的轨迹摘要，它们包含了需要避免的模式：

{t_minus_summaries}

## 任务
基于以上轨迹，提炼出 {dimension} 维度的 Skill 规则补丁。

每条规则格式：
- **ALWAYS**: 总是应该这样做（来自 T⁺ 的共识模式）
- **WHEN ... THEN**: 当满足某条件时，执行某动作（来自 T⁺ 的条件模式）
- **NEVER**: 永远不要这样做（来自 T⁻ 的失败教训）
- **AVOID**: 尽量避免这样做（来自 T⁻ 的问题模式）

## 输出格式（严格 JSON）
{{
  "dimension": "{dimension}",
  "project": "{project}",
  "rules": [
    {{
      "id": "rule_xxx",
      "type": "ALWAYS | WHEN_THEN | NEVER | AVOID",
      "condition": "触发条件（WHEN_THEN 类型必填，其他为空）",
      "action": "应该做什么 / 不应该做什么",
      "evidence": {{
        "from_success": ["支持此规则的 T⁺ 证据"],
        "from_failure": ["反向验证此规则的 T⁻ 证据"]
      }},
      "confidence": 0.0-1.0,
      "scope": "general | project-specific | language-specific"
    }}
  ],
  "deprecated_rules": [
    {{
      "original_rule": "被推翻的旧规则",
      "reason": "为什么这条旧规则不再适用",
      "replacing_rule": "rule_id"
    }}
  ]
}}

只输出 JSON。
"""
```

#### 2.2 四维度并行分析

```python
DIMENSIONS = {
    "architecture": {
        "focus": "项目结构探索、模块划分、依赖管理、文件组织",
        "tools_of_interest": ["glob", "read", "grep"]
    },
    "implementation": {
        "focus": "代码编写策略、工具选择、编辑模式（write vs edit）",
        "tools_of_interest": ["write", "edit", "bash"]
    },
    "debugging": {
        "focus": "问题定位、错误处理、修复策略、重试模式",
        "tools_of_interest": ["bash", "read", "edit"]
    },
    "testing": {
        "focus": "测试策略、验证方法、质量保障",
        "tools_of_interest": ["bash", "write", "edit"]
    }
}

# 对每个维度并行调用大模型
for dimension in DIMENSIONS:
    # 筛选与该维度相关的轨迹（包含相关工具调用）
    relevant_trajectories = filter_by_tools(
        all_summaries,
        DIMENSIONS[dimension]["tools_of_interest"]
    )
    t_plus = [t for t in relevant_trajectories if t["label"] == "success"]
    t_minus = [t for t in relevant_trajectories if t["label"] in ("failure", "partial")]

    patches[dimension] = call_strong_model(
        DISTILLATION_PROMPT.format(
            dimension=dimension,
            project=project,
            t_plus_summaries=format_summaries(t_plus),
            t_minus_summaries=format_summaries(t_minus)
        )
    )
```

### Step 3: 合并写入（快速模型）

**输入**：多个维度的 Skill 补丁
**输出**：更新后的 SKILL.md 文件
**模型**：快速模型（去重 + 冲突检测）

#### 3.1 跨维度合并

```python
MERGE_PROMPT = """
你是一个 Skill 仓库管理员，负责合并来自不同分析维度的 Skill 补丁。

## 当前 Skill 文件
{current_skill_md}

## 待合并的新规则补丁
{patches_json}

## 合并规则
1. **去重**: 语义相同的规则只保留一条，保留 confidence 最高的版本
2. **冲突检测**: 如果新旧规则矛盾，保留 confidence 更高的，并在 deprecated_rules 中记录
3. **优先级排序**: ALWAYS > WHEN_THEN > NEVER > AVOID
4. **数量控制**: 最多保留 {max_rules} 条规则，移除 confidence 最低的
5. **分类归档**: 按 scope 分组（general → project-specific → language-specific）

## 输出
直接输出合并后的完整 SKILL.md 内容，格式如下：

# Skill: {skill_name}

## 概述
一行描述这个 Skill 的用途。

## 通用规则 (General)
- ALWAYS: ...
- WHEN ... THEN: ...

## 项目特定规则 (Project: xxx)
- ALWAYS: ...

## 失败教训 (Lessons Learned)
- NEVER: ...
- AVOID: ...

## 变更记录
- {date}: 新增 rule_xxx（来自 {dimension} 分析，confidence: 0.xx）
- {date}: 移除 rule_yyy（被 rule_zzz 替代）
"""
```

## 五、完整工作流

### 5.1 一键蒸馏命令

```bash
# 蒸馏指定项目的所有历史 session
python distill.py --project open-desk

# 蒸馏指定时间范围
python distill.py --project open-desk --from 2026-03-01 --to 2026-04-24

# 蒸馏指定 session
python distill.py --session ses_3388c7884ffe80iEJGWdgeBqW6

# 只运行 Step 1（预处理，不消耗大模型 token）
python distill.py --project open-desk --step 1

# 指定目标 Skill
python distill.py --project open-desk --target-skill coding-patterns

# 干跑（不实际写入，只输出分析报告）
python distill.py --project open-desk --dry-run
```

### 5.2 交互流程

```
$ python distill.py --project open-desk

╭─────────────────────────────────────────────────────╮
│  Trace2Skill Distiller v0.1                         │
│  Project: open-desk                                 │
╰─────────────────────────────────────────────────────╯

📊 Scanning OpenCode sessions for 'open-desk'...
   Found 44 sessions, 341+ messages, 744+ tool calls

🔍 Step 1: Pre-filtering with fast model...
   ✅ 8 sessions pass quality threshold (≥5 msgs, ≥3 tools)
   ❌ 36 sessions skipped (too simple / greeting / error-only)

📋 Session candidates:
   #  Session ID             Title                            Msgs  Tools  Label
   1  ses_3388...             Creating AGENTS.md documentation  341   73    T+
   2  ses_2a17...             ULTRAWORK MODE ENABLED!          89    42    T+
   3  ses_25fb...             Spec 合规校验                      46    18    T+
   4  ses_2664...             探索模块获取和方法信息              9     5     T±
   5  ...

   Continue with distillation? [Y/n] y

📝 Step 1: Extracting trajectory summaries (fast model)...
   [████████████████████████████████] 8/8 sessions processed

🏷️  Auto-labeling T+/T-...
   T+ (success): 5 sessions
   T- (partial): 2 sessions
   T- (failure): 1 session

🔥 Step 2: Distilling skill patches (strong model)...
   Running 4 analysis dimensions in parallel:

   ┌─ architecture ─────────────────────────────────────┐
   │  Analyzing 5 T+ and 3 T- trajectories...           │
   │  Found 12 rules (4 ALWAYS, 3 WHEN_THEN, 5 NEVER)  │
   └────────────────────────────────────────────────────┘

   ┌─ implementation ───────────────────────────────────┐
   │  Analyzing 5 T+ and 2 T- trajectories...           │
   │  Found 8 rules (3 ALWAYS, 2 WHEN_THEN, 3 AVOID)   │
   └────────────────────────────────────────────────────┘

   ┌─ debugging ────────────────────────────────────────┐
   │  Analyzing 3 T+ and 3 T- trajectories...           │
   │  Found 6 rules (2 ALWAYS, 1 NEVER, 3 AVOID)       │
   └────────────────────────────────────────────────────┘

   ┌─ testing ──────────────────────────────────────────┐
   │  Not enough relevant trajectories, skipping        │
   └────────────────────────────────────────────────────┘

   Total: 26 candidate rules distilled

🔄 Step 3: Merging into skill file (fast model)...
   Deduplication: 26 → 19 rules (7 duplicates merged)
   Conflict check: 0 conflicts found
   Priority sort: 6 ALWAYS → 5 WHEN_THEN → 4 NEVER → 4 AVOID

📄 Skill file updated: ~/.trace2skill/skills/open-desk/SKILL.md

╭─────────────────────────────────────────────────────╮
│  Distillation Complete                              │
│                                                     │
│  Sessions analyzed:  8 (5 T+, 3 T-)                │
│  Rules distilled:    19                             │
│  Tokens used:        fast: 45K | strong: 120K      │
│  Estimated cost:     ¥0.xx                          │
│  Output:             ~/.trace2skill/skills/open-desk│
│                                                     │
│  Next: Review SKILL.md and run with --apply         │
╰─────────────────────────────────────────────────────╯
```

## 六、关键技术决策

### 6.1 为什么两阶段模型而不是一个模型

| 方面 | 快速模型（Step 1+3） | 大模型（Step 2） |
|------|---------------------|----------------|
| 调用次数 | 多（每个 session 一次） | 少（每个维度一次） |
| 任务复杂度 | 结构化提取（JSON → JSON） | 深度推理（轨迹 → 洞察） |
| Token 消耗 | 低（每次 ~2K input） | 高（每次 ~8K input） |
| 延迟要求 | 批量可并行 | 按维度并行即可 |
| 总成本占比 | ~15% | ~85% |

分离的好处：Step 1 可以先跑一遍积累摘要，攒够一批再统一跑 Step 2，节省大模型调用。

### 6.2 为什么用 `opencode export` 而不是直接查 SQLite

1. **格式稳定性**：`export` 命令输出的是稳定的公共 API 格式，SQLite schema 可能随版本变化
2. **完整性**：export 已经做好了 message/part 的 JOIN 组装，直接查需要自己做
3. **缓存友好**：export 的 JSON 可以缓存到 `cache/sessions/`，避免重复导出

但在 Step 1 的筛选阶段，先用 SQLite 做快速过滤（只查 session 表的元数据），再对选中的 session 调 `opencode export`。

### 6.3 Skill 输出格式选择

输出为 Markdown（SKILL.md），而不是 JSON，因为：
- 可以直接被 Claude Code / OpenCode 的 rules 系统加载
- 人类可读可编辑
- 与现有 `.claude/rules/` 和 `AGENTS.md` 生态兼容

### 6.4 处理超长 Session

蜂群机制设计那个 session 有 452 条消息、1.5MB JSON。直接发给模型会超 token 限制。

**分治策略**：
```python
def chunk_session(session_json, max_tokens=6000):
    """将超长 session 按对话轮次分块"""
    messages = session_json["messages"]

    # 按用户消息切分对话轮次
    turns = split_into_turns(messages)

    # 每个轮次独立生成摘要
    summaries = []
    for turn in turns:
        if estimate_tokens(turn) > max_tokens:
            # 单轮次就超长，按工具调用压缩
            turn = compress_tool_outputs(turn)
        summaries.append(
            call_fast_model(EXTRACT_TURN_PROMPT, turn)
        )

    return summaries
```

## 七、实现计划

### P0: 最小可用版本（1 个脚本）

```bash
# 单文件 Python 脚本
~/.trace2skill/distill.py
```

依赖：
- `python >= 3.8`
- `sqlite3`（标准库）
- OpenCode CLI（用于 export）
- LLM API（通过环境变量配置 API Key）

功能：
1. 解析 SQLite 找到目标 session
2. 调 `opencode export` 导出 JSON
3. 快速模型提取摘要 + 自动标注
4. 大模型蒸馏规则
5. 合并输出 SKILL.md

### P1: 缓存与增量

- 缓存已导出的 session JSON 和摘要
- 增量蒸馏：只处理新增 session
- 摘要去重：相似 session 合并分析

### P2: 闭环验证

- 将蒸馏出的 Skill 应用回 OpenCode（写入 AGENTS.md 或 rules）
- 追踪使用新 Skill 后的 session 质量
- 自动触发下一轮蒸馏

## 八、Token 消耗估算

以 open-desk 项目为例（44 sessions，8 个值得分析）：

| 步骤 | 调用次数 | 每次输入 token | 每次输出 token | 总 token |
|------|---------|--------------|--------------|---------|
| Step 1 摘要提取 | 8 | ~3K | ~1K | ~32K |
| Step 1 标注 | 8 | ~1K | ~0.3K | ~10K |
| Step 2 蒸馏(×4维度) | 4 | ~6K | ~2K | ~32K |
| Step 3 合并 | 1 | ~3K | ~2K | ~5K |
| **合计** | | | | **~79K** |

快速模型处理 ~47K，大模型处理 ~32K。

以 GLM-4-Flash（免费/极低成本）+ GLM-5.1 计算总成本约 ¥0.05-0.2 每次。
