# OpenCode 数据探索笔记

> 探索时间: 2026-04-24
> 探索对象: 本机 OpenCode (v1.2.27) 的会话存储与导出机制

---

## 一、OpenCode CLI 概览

### 可执行文件位置
```
C:\Users\15802\AppData\Roaming\npm\opencode
```

### 版本
```
1.2.27
```

### 关键子命令

| 命令 | 用途 |
|------|------|
| `opencode [project]` | 启动 TUI |
| `opencode session list` | 列出所有 session |
| `opencode session delete <sessionID>` | 删除 session |
| `opencode export [sessionID]` | 导出 session 为 JSON（到 stdout） |
| `opencode import <file>` | 导入 session JSON |
| `opencode db path` | 打印数据库路径 |
| `opencode db [query]` | 打开 SQLite shell 或执行 SQL |
| `opencode db migrate` | JSON 数据迁移到 SQLite |
| `opencode stats` | Token 用量和成本统计 |

### 启动参数
```
-m, --model       指定模型 (provider/model 格式)
-c, --continue    继续上次 session
-s, --session     指定 session id 继续
--fork            fork session
--prompt          指定 prompt
--agent           指定 agent
```

---

## 二、SQLite 数据库

### 2.1 数据库路径
```
C:\Users\15802\.local\share\opencode\opencode.db
```

### 2.2 完整表结构

```sql
-- 核心表
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_id TEXT,           -- fork 关系
    slug TEXT,                -- 人类可读别名 (如 "quiet-cactus")
    directory TEXT,           -- 工作目录路径
    title TEXT,               -- session 标题
    version TEXT,             -- OpenCode 版本
    share_url TEXT,           -- 分享链接
    summary_additions INT,    -- git 增加行数
    summary_deletions INT,    -- git 删除行数
    summary_files INT,        -- 变更文件数
    summary_diffs TEXT,       -- diff 摘要 JSON
    revert TEXT,              -- 回滚标记
    permission TEXT,          -- 权限配置 JSON
    time_created INTEGER,
    time_updated INTEGER,
    time_compacting INTEGER,
    time_archived INTEGER,
    workspace_id TEXT
);

CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                 -- JSON: {role, time, summary, agent, model, tokens, cost, ...}
);

CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                 -- JSON: {type, text} | {type, tool, state, callID} | ...
);

-- 辅助表
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    worktree TEXT,
    vcs TEXT,                 -- 版本控制类型
    name TEXT,
    icon_url TEXT,
    icon_color TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    time_initialized INTEGER,
    sandboxes TEXT,
    commands TEXT
);

CREATE TABLE todo (
    session_id TEXT PRIMARY KEY,
    content TEXT,
    status TEXT,
    priority TEXT,
    position INTEGER,
    time_created INTEGER,
    time_updated INTEGER
);

CREATE TABLE session_share (
    session_id TEXT,
    id TEXT,
    secret TEXT,
    url TEXT,
    time_created INTEGER,
    time_updated INTEGER
);

CREATE TABLE workspace (
    id TEXT PRIMARY KEY,
    branch TEXT,
    project_id TEXT,
    type TEXT,
    name TEXT,
    directory TEXT,
    extra TEXT
);

CREATE TABLE account (
    id TEXT PRIMARY KEY,
    email TEXT,
    url TEXT,
    access_token TEXT,
    refresh_token TEXT,
    token_expiry INTEGER,
    time_created INTEGER,
    time_updated INTEGER
);

CREATE TABLE account_state (
    id INTEGER PRIMARY KEY,
    active_account_id TEXT,
    active_org_id TEXT
);

CREATE TABLE control_account (
    email TEXT,
    url TEXT,
    access_token TEXT,
    refresh_token TEXT,
    token_expiry INTEGER,
    active INTEGER,
    time_created INTEGER,
    time_updated INTEGER,
    PRIMARY KEY (email, url)
);

CREATE TABLE __drizzle_migrations (
    id SERIAL PRIMARY KEY,
    hash TEXT,
    created_at NUMERIC,
    name TEXT,
    applied_at TEXT
);
```

### 2.3 数据量统计（本机实际）

| 表 | 行数 |
|----|------|
| session | 86 |
| message | 1309 |
| part | 3984 |
| project | 5 |
| todo | 40 |
| session_share | 0 |
| workspace | 0 |
| account / control_account | 0 |

---

## 三、Session Export JSON 格式详解

通过 `opencode export <sessionID>` 导出到 stdout 的完整 JSON 结构。
注意：stderr 会输出 "Exporting session: xxx" 提示信息，需要分离处理（`2>/dev/null`）。

### 3.1 顶层结构

```json
{
  "info": { /* session 元信息 */ },
  "messages": [ /* 消息数组 */ ]
}
```

### 3.2 info 字段

```json
{
  "id": "ses_25fc34d75ffeG7098HV0F7WG2b",
  "slug": "quiet-cactus",
  "projectID": "global",
  "directory": "D:\\dev\\workspace-ai\\opencode-writeSpec",
  "title": "Write helper.ts with add function",
  "version": "1.2.27",
  "summary": {
    "additions": 0,
    "deletions": 0,
    "files": 0
  },
  "permission": [
    {
      "permission": "question",
      "pattern": "*",
      "action": "deny"
    },
    {
      "permission": "plan_enter",
      "pattern": "*",
      "action": "deny"
    },
    {
      "permission": "plan_exit",
      "pattern": "*",
      "action": "deny"
    }
  ],
  "time": {
    "created": 1776509825674,
    "updated": 1776509846985
  }
}
```

### 3.3 message 结构

每条 message 包含 `info` 和 `parts` 两部分。

#### User Message (info.data)

```json
{
  "role": "user",
  "time": { "created": 1776509825845 },
  "summary": { "diffs": [] },
  "agent": "build",
  "model": {
    "providerID": "zhipuai-coding-plan",
    "modelID": "glm-5.1"
  },
  "id": "msg_da03cb335001PiNyg8dw4bP3z2",
  "sessionID": "ses_25fc34d75ffeG7098HV0F7WG2b"
}
```

#### Assistant Message (info.data)

```json
{
  "role": "assistant",
  "time": {
    "created": 1776509825906,
    "completed": 1776509832887
  },
  "parentID": "msg_da03cb335001PiNyg8dw4bP3z2",
  "modelID": "glm-5.1",
  "providerID": "zhipuai-coding-plan",
  "mode": "build",
  "agent": "build",
  "path": {
    "cwd": "D:\\dev\\workspace-ai\\opencode-writeSpec",
    "root": "/"
  },
  "cost": 0,
  "tokens": {
    "total": 15958,
    "input": 7578,
    "output": 60,
    "reasoning": 26,
    "cache": {
      "read": 8320,
      "write": 0
    }
  },
  "finish": "tool-calls",
  "id": "msg_da03cb372001zaVtD9GVPwCckj",
  "sessionID": "ses_25fc34d75ffeG7098HV0F7WG2b"
}
```

#### Assistant Message（出错时）

```json
{
  "role": "assistant",
  "error": {
    "name": "APIError",
    "data": {
      "message": "身份验证失败。",
      "statusCode": 401,
      "isRetryable": false,
      "responseHeaders": { ... }
    }
  },
  ...
}
```

### 3.4 part 类型全集

#### type: "text"
```json
{
  "type": "text",
  "text": "文件 `src/utils/helper.ts` 已创建完成。",
  "time": {
    "start": 1776509846971,
    "end": 1776509846971
  },
  "id": "prt_xxx",
  "sessionID": "ses_xxx",
  "messageID": "msg_xxx"
}
```

#### type: "reasoning"
```json
{
  "type": "reasoning",
  "text": "The user wants me to create a file at src/utils/helper.ts with specific content.",
  "time": {
    "start": 1776509831288,
    "end": 1776509832571
  },
  "id": "prt_xxx",
  "sessionID": "ses_xxx",
  "messageID": "msg_xxx"
}
```

#### type: "tool" — 核心数据
```json
{
  "type": "tool",
  "callID": "call_d38f1858491a49079f2bac61",
  "tool": "bash",
  "state": {
    "status": "completed",
    "input": {
      "command": "ls src/utils 2>/dev/null || mkdir -p src/utils",
      "description": "Check if directory exists"
    },
    "output": "'ls' is not recognized as an internal or external command...",
    "title": "dev\\workspace-ai\\opencode-writeSpec",
    "metadata": { "count": 0, "truncated": false },
    "time": { "start": 1776509832887, "end": 1776509833000 }
  },
  "id": "prt_xxx",
  "sessionID": "ses_xxx",
  "messageID": "msg_xxx"
}
```

#### type: "patch" — git 文件变更记录
```json
{
  "type": "patch",
  "hash": "694a9c4e6c7b19e07aa3f46c53ea83bcdfbedf12",
  "files": [
    "D:/dev/workspace-ai/open-desk/AGENTS.md"
  ]
}
```

#### type: "subtask" — 子任务委派
```json
{
  "type": "subtask",
  "prompt": "检查以下代码文件是否遵守项目 .sdd/doc/ 目录下的规范...",
  "description": "Spec 合规校验 (1 个文件)",
  "agent": "spec-checker"
}
```

#### type: "step-start" / "step-finish"
```json
// step-start: 无额外字段
{ "type": "step-start" }

// step-finish:
{
  "type": "step-finish",
  "reason": "stop",
  "cost": 0,
  "tokens": {
    "total": 16227,
    "input": 150,
    "output": 13,
    "reasoning": 0,
    "cache": { "read": 16064, "write": 0 }
  }
}
```

---

## 四、工具调用（tool part）详细 Input/Output Schema

### 4.1 工具分布统计

| 工具 | 调用次数 | 说明 |
|------|---------|------|
| read | 197 | 读取文件 |
| bash | 196 | 执行 shell 命令 |
| write | 115 | 写入文件 |
| edit | 68 | 编辑文件（oldString → newString） |
| glob | 66 | 文件模式匹配 |
| todowrite | 31 | 更新 todo 列表 |
| grep | 17 | 内容搜索 |
| task | 16 | 子任务委派（Agent） |
| background_output | 11 | 获取后台任务输出 |
| question | 5 | 向用户提问 |
| profile_update | 5 | 更新用户画像 |
| lsp_diagnostics | 3 | LSP 诊断 |
| skill_mcp | 3 | 调用 MCP 工具 |
| webfetch | 2 | 网页抓取 |
| chrome-devtools_new_page | 2 | 浏览器自动化 |
| phase0_ping | 2 | 心跳 |
| web-search-prime_webSearchPrime | 1 | 网页搜索 |
| skill | 1 | 调用 skill |
| opscopilot_server_list | 1 | OpsCopilot 服务器列表 |
| profile_query | 1 | 查询用户画像 |
| invalid | 1 | 无效调用 |

### 4.2 各工具 Input/Output 格式

#### bash
```jsonc
// 基本形式
{
  "input": {
    "command": "ls -la",
    "description": "List files in current directory"
  },
  "output": "total 32\ndrwxr-xr-x ..."  // string
}

// 带超时
{
  "input": {
    "command": "npm test",
    "description": "Run tests",
    "timeout": 30000
  }
}

// 带工作目录
{
  "input": {
    "command": "npm run build",
    "description": "Build project",
    "workdir": "/path/to/project"
  }
}
```

#### read
```jsonc
// 基本形式
{
  "input": { "filePath": "D:\\dev\\project\\src\\index.ts" },
  "output": "<path>D:\\dev\\project\\src\\index.ts</path>\n<type>file</type>\n<content>1: ..."  // string, XML-like format
}

// 带行号范围
{
  "input": { "filePath": "D:\\dev\\project\\src\\index.ts", "offset": 10, "limit": 50 }
}

// 仅 limit
{
  "input": { "filePath": "D:\\dev\\project\\src\\index.ts", "limit": 100 }
}
```

#### write
```jsonc
{
  "input": {
    "filePath": "D:\\dev\\project\\src\\utils\\helper.ts",
    "content": "export function add(a: number, b: number): number {\n  return a + b\n}\n"
  },
  "output": "Wrote file successfully."  // string
}
```

注意：write 的 content 字段可能非常长（实测最长 24788 字符）。

#### edit
```jsonc
{
  "input": {
    "filePath": "D:\\dev\\project\\src\\index.ts",
    "oldString": "const old = 'value'",
    "newString": "const new = 'value'"
  },
  "output": "..."  // string
}
```

#### glob
```jsonc
// 基本形式
{
  "input": { "pattern": "**/*.ts" },
  "output": "src/index.ts\nsrc/utils/helper.ts\n..."  // string, 换行分隔
}

// 带路径
{
  "input": { "pattern": "*.json", "path": "D:\\dev\\project\\src" }
}
```

#### grep
```jsonc
// 基本形式
{
  "input": {
    "pattern": "function add",
    "path": "D:\\dev\\project\\src"
  }
}

// 完整形式
{
  "input": {
    "pattern": "export",
    "path": "D:\\dev\\project\\src",
    "include": "*.ts",
    "output_mode": "content",
    "head_limit": 20
  }
}

// 最简形式
{
  "input": {
    "pattern": "TODO",
    "include": "*.ts"
  }
}
```

#### task（子任务/Agent 调用）
```jsonc
// 基本形式
{
  "input": {
    "prompt": "分析项目结构...",
    "subagent_type": "Explore",
    "description": "探索代码库"
  }
}

// 完整形式
{
  "input": {
    "subagent_type": "Explore",
    "load_skills": true,
    "run_in_background": true,
    "description": "Explore frontend patterns",
    "prompt": "..."
  }
}

// 带 category
{
  "input": {
    "category": "code-review",
    "description": "...",
    "load_skills": true,
    "prompt": "...",
    "run_in_background": false,
    "subagent_type": "code-reviewer"
  }
}
```

#### todowrite
```jsonc
{
  "input": {
    "todos": [
      { "subject": "创建项目结构", "status": "completed" },
      { "subject": "实现核心功能", "status": "in_progress" },
      { "subject": "编写测试", "status": "pending" }
    ]
  }
}
```

#### question
```jsonc
{
  "input": {
    "questions": [
      {
        "question": "使用哪种框架？",
        "header": "Framework",
        "options": [
          { "label": "React", "description": "..." },
          { "label": "Vue", "description": "..." }
        ]
      }
    ]
  }
}
```

#### profile_update
```jsonc
{
  "input": {
    "type": "coding_preference",
    "content": "偏好 TypeScript，使用函数式风格",
    "confidence": 0.85
  }
}
```

#### skill_mcp（MCP 工具调用）
```jsonc
{
  "input": {
    "mcp_name": "web-search-prime",
    "tool_name": "webSearchPrime",
    "arguments": { "search_query": "..." }
  }
}
```

---

## 五、Session 分布统计

### 5.1 按项目分组

| 项目目录 | Session 数 | 新增行 | 删除行 |
|---------|-----------|--------|--------|
| open-desk | 44 | +4855 | -530 |
| opencode-selfPro | 9 | 0 | 0 |
| OpsCopilot | 7 | 0 | 0 |
| opencode-writeSpec | 7 | 0 | 0 |
| AI-Video-Transcriber | 5 | 0 | 0 |
| 15802 (home dir) | 5 | 0 | 0 |
| my-agent | 4 | 0 | 0 |
| toPlayList | 1 | 0 | 0 |
| word2md | 1 | 0 | 0 |
| my-swam | 1 | 0 | 0 |
| download | 1 | 0 | 0 |

### 5.2 按消息数排序（Top 15）

| 消息数 | 项目 | 标题 |
|--------|------|------|
| 452 | my-swam | OpenCode Agent 蜂群机制设计 |
| 341 | open-desk | Creating AGENTS.md documentation |
| 115 | word2md | 项目功能测试 |
| 89 | open-desk | ULTRAWORK MODE ENABLED! |
| 46 | open-desk | 探索项目结构 (@explore subagent) |
| 19 | 15802 | opencode 安装帮助 |
| 18 | open-desk | AGENTS.md build and style guide |
| 10 | open-desk | Explore OpenDesk architecture |
| 9 | open-desk | Agent |
| 9 | open-desk | 探索模块获取和方法信息 (@explore subagent) |
| 9 | open-desk | Explore frontend UI patterns (@explore subagent) |
| 8 | open-desk | 探索前端结构和状态管理 (@explore subagent) |
| 8 | OpsCopilot | Explore MCP configuration (@explore subagent) |
| 7 | 15802 | 项目功能测试 |
| 7 | opencode-writeSpec | 创建 src/test.ts 文件 |

### 5.3 Part 类型分布

| Part 类型 | 数量 | 说明 |
|-----------|------|------|
| step-start | 1150 | 步骤开始标记 |
| step-finish | 1145 | 步骤结束标记 |
| tool | 744 | 工具调用（**最核心的分析数据**） |
| reasoning | 582 | 模型推理过程 |
| text | 297 | 文本内容 |
| patch | 64 | git 文件变更记录 |
| subtask | 2 | 子任务委派 |

---

## 六、关键发现与注意事项

### 6.1 Windows 兼容性问题

1. **bash 输出乱码**：Windows 上中文路径/内容在 Python 读取时需要 `encoding='utf-8'`，否则 GBK 解码失败
2. **`ls` 不可用**：OpenCode agent 在 Windows 上调用 `bash` 工具时，`ls` 命令不被识别（需要用 `dir` 或在 Git Bash 中运行）
3. **路径分隔符**：数据库中路径使用 Windows 反斜杠 `\`，export JSON 中混合使用 `/` 和 `\`

### 6.2 export 命令行为

1. **stdout/stderr 分离**：export 输出到 stdout，但 "Exporting session: xxx" 提示信息输出到 stderr
2. **退出码**：成功导出后返回退出码 49（不确定是 bug 还是特性），需要用 `2>/dev/null` 重定向 stderr 后 > 重定向 stdout 到文件
3. **无 sessionID 时**：会进入交互式选择模式

### 6.3 数据完整性

1. **summary 字段多数为 0**：大部分 session 的 `summary_additions/deletions/files` 都是 0，说明 git diff 统计不是默认启用的
2. **patch 数据稀疏**：3984 个 part 中只有 64 个 patch 记录（约 1.6%），可能需要在 session 结束时显式触发
3. **write 的 content 可能很大**：实测单个 write 工具的 content 字段可达 25K 字符，超长 session 的 export JSON 可达 1.5MB

### 6.4 有价值的分析信号

1. **finish 字段**：assistant 消息的 `finish` 值（"stop" | "tool-calls" | "error"）是判断会话是否正常结束的关键信号
2. **reasoning 部分**：582 条推理记录是理解 agent 决策过程的宝库
3. **error 字段**：包含完整的错误结构（name, data.statusCode, data.message），可用于失败归因
4. **tokens 统计**：每条 assistant 消息都有完整的 token 使用明细（input/output/reasoning/cache），可用于成本分析
5. **subtask**：虽然只有 2 条，但记录了 agent 间的委派关系（prompt + agent 类型）

### 6.5 超长 Session 特征

以 ses_3388c7884ffe80iEJGWdgeBqW6（蜂群机制设计）为例：
- 452 条消息
- 1,581,633 bytes JSON (~1.5MB)
- 工具调用分布：write(73) > bash(35) > read(16) > todowrite(11) > edit(9) > glob(1)
- 总 token 消耗：17,122,231
- 直接发送给 LLM 会超出上下文窗口，必须分块处理

---

## 七、结论

OpenCode 的 session 数据结构清晰、信息丰富，天然适合作为 Trace2Skill 的轨迹数据源：

1. **工具调用链**完整记录了 agent 的操作序列
2. **reasoning** 部分记录了决策思考过程
3. **patch** 和 **summary** 提供了结果评判信号
4. **tokens/cost** 可用于蒸馏的成本效益分析

唯一需要注意的技术点是超长 session 的分块处理和 Windows 路径/编码兼容性。
