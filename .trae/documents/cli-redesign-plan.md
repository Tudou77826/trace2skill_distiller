# CLI 改造计划

## Summary

本次改造目标是把当前“命令杂糅、语义不真、把内部实现细节直接暴露给用户”的 CLI，重构为围绕用户工作流组织的命令集，并把 `source` 与模型、并发数一样纳入持久化配置，运行时默认使用当前配置，不再鼓励每次命令行临时覆盖。

已确认的产品决策：

- 旧命令不做兼容层，直接替换。
- `source` 本次先做到“类型级单来源”（如 `opencode` / `chrys`），不做多实例 source 注册表。
- `source` 属于配置项，和模型 ID、并发数、输出格式一起作为当前运行设置长期保存。
- 默认不跨来源遍历；`project` 和 `session` 都只在当前配置的 source 范围内解释。

本次改造聚焦 CLI、配置模型、帮助文案、测试与 README；不扩展多来源注册表，不实现新的后台调度系统。

## Current State Analysis

### 现有 CLI 问题

- 顶层命令全部定义在 `src/trace2skill_distiller/cli/main.py`，当前包含 `init`、`distill`、`sessions`、`inspect`、`status`、`config`、`schedule`。
- `distill` 承担了筛选、预处理、聚类、蒸馏、输出、报告写入多重职责，且通过 `--step`、`--dry-run`、`--incremental` 暴露了大量流程细节。
- `sessions`、`inspect`、`distill` 都隐式读取当前 `config.source`，但 CLI 没把“source 是核心上下文”表达出来。
- `status` 和 `schedule status` 都叫“状态”，但语义分裂；`schedule` 整体仍是半成品。

### 现有配置问题

- 配置模型定义在 `src/trace2skill_distiller/core/config.py`，`DistillConfig` 当前已有 `fast_model`、`strong_model`、`source`、`filter`、`analysis`、`output`、`concurrency`、`scheduler`。
- 现有 `source` 结构已经是持久化配置的一部分，但 CLI 设计没有把它提升为“默认工作上下文”。
- `set_config_value()` 允许修改的 key 很有限，主要集中在模型、输出、并发和部分 source 子字段；缺少对用户工作流友好的当前设置管理。

### 已知行为与文案不一致

- `distill --from/--to` 已定义但未生效。
- `distill --dry-run` 仍会写 HTML report。
- `sessions --all` 实际仍先被 `--top` 截断。
- `status` 显示累计 cost，但状态持久化逻辑没有真实维护该值。
- `schedule start` 宣称按 cron 运行，但实现只支持“每天 HH:MM”。

### 测试与文档现状

- CLI 自动化测试很弱，`tests/test_cli.py` 目前只覆盖 `config show` 与 `.env` 安全加载。
- `tests/test_config.py` 已覆盖 `DistillConfig.load()`、`set_config_value()`、`init_default_config()`，适合作为配置模型改造的回归基础。
- `README.md` 仍以旧命令树为主，示例命令与计划中的新 CLI 不一致。

## Assumptions & Decisions

- 不保留旧命令兼容层；直接以新命令树替换旧入口。
- `source` 先只支持类型级选择，不做 `source list/use/add/remove` 多实例管理。
- 运行时默认使用配置中的 `source`、模型、并发和输出格式；命令行只保留少量临时覆盖参数。
- 默认不做跨 source 聚合和遍历。
- `schedule` 本次不继续增强，优先从正式 CLI 中移除，避免保留误导性能力。
- `status` 拆分语义后不保留；历史结果查看迁移到 `runs`，健康检查迁移到 `doctor`。

## Proposed Changes

### 1. 重构 CLI 命令树

#### 文件

- `src/trace2skill_distiller/cli/main.py`

#### 变更内容

- 将当前顶层命令树重构为：
  - `init`
  - `doctor`
  - `config show|set|edit`
  - `sessions list|show`
  - `inspect session|run`
  - `run`
  - `runs list|show`
- 删除旧的正式入口：
  - `distill`
  - `status`
  - `schedule`
  - 顶层 `sessions`
  - 顶层 `inspect <session_id>`
- `run` 成为唯一主执行入口，替代 `distill`。

#### 设计细节

- `run`
  - 保留 `--project`、`--session`、`--workers`。
  - 删除 `--from`、`--to`、`--incremental`、`--step`、`--dry-run`、`--format`、`--source`。
  - 新增：
    - `--mode preprocess|analyze|full`
    - `--output skill|knowledge`
    - `--preview`
- `sessions list`
  - 替代旧 `sessions`
  - `--top` 改名为 `--limit`
  - `--all` 改名为 `--include-low-quality`
- `sessions show <session-id>`
  - 新增单会话元信息查看入口，用于判断是否值得处理。
- `inspect session <session-id>`
  - 承接旧 `inspect` 能力，明确这是“看预处理结果”。
- `inspect run <run-id>`
  - 新增查看某次运行详情的入口。
- `doctor`
  - 新增配置/来源/模型健康检查入口。
- `runs list|show`
  - 新增运行历史查看入口，替代旧 `status` 的模糊职责。

#### 为什么这样改

- 让命令按照用户任务组织：配置、找输入、看中间结果、执行、看历史。
- 把旧 CLI 中混乱的“执行流程开关”收敛到更清晰的产品语义。
- 明确 `source` 来自当前配置，不再鼓励临时覆盖。

### 2. 调整配置模型，让 source 成为默认运行上下文

#### 文件

- `src/trace2skill_distiller/core/config.py`

#### 变更内容

- 保留当前 `DistillConfig.source` 作为唯一当前来源配置，不引入多来源注册表。
- 统一配置语义：`source`、`fast_model`、`strong_model`、`concurrency.workers`、`output.format` 都属于“当前运行设置”。
- 将 CLI 友好的 key 命名补齐到新的命令树需求上：
  - 保留并继续支持：
    - `source.type`
    - `source.opencode.db_path`
    - `source.opencode.export_command`
    - `source.chrys.sessions_dir`
    - `concurrency.workers`
    - `output.format`
  - 新增/调整 `config set` 暴露范围：
    - `filter.min_messages`
    - `filter.min_tools`
    - `analysis.clustering_max_topics`
    - 如果 `doctor` 需要额外开关，再补相应键
- `init_default_config()` 继续创建最小可运行配置，但帮助文案和输出要强调“当前 source 已写入配置，以后默认一直使用”。

#### 设计细节

- 不新增 `current.profile`、`profiles.*` 等更复杂模型。
- `output.format` 的配置值可以内部仍沿用 `skill_md|knowledge_md`，但 CLI 展示层使用 `skill|knowledge`，在 CLI 层做映射。
- 环境变量覆盖保留现状，不在本次引入新的 profile 机制。

#### 为什么这样改

- 当前仓库已经具备以配置保存 `source` 的基础，不需要额外引入更重的抽象。
- 先把“默认上下文来自配置”落地，降低用户每次重复输入参数的负担。

### 3. 重新定义 run 的执行语义

#### 文件

- `src/trace2skill_distiller/cli/main.py`
- `src/trace2skill_distiller/orchestrator/pipeline.py`
- 如有需要：`src/trace2skill_distiller/output/output_facade.py`

#### 变更内容

- `run --mode` 对应 pipeline 停止点：
  - `preprocess` -> 跑到旧 step 1，输出 trajectory 预览产物
  - `analyze` -> 跑到旧 step 2，展示 topic 与规则，不写技能文件
  - `full` -> 完整跑完并写输出
- `run --preview`
  - 必须做到真正零持久化：
    - 不写技能文件
    - 不写 trajectory 文件
    - 不写 HTML report
    - 不更新状态
- `run --output skill|knowledge`
  - 在 CLI 层映射到内部 `skill_md|knowledge_md`
- 去掉无效参数和误导性行为：
  - 移除 `--from/--to`
  - 移除 `--incremental`
  - 移除 `--step`
  - 移除 `--dry-run`
- 如果 `pipeline.py` 当前存在“report 写两次”问题，一并在执行阶段修复。

#### 为什么这样改

- 让用户看到的选项只表达“我要跑到哪一步”和“我要不要只预览”，而不是内部 step 编号。
- 消除 `preview/dry-run` 语义不真问题。

### 4. 把 source 从“可选覆盖参数”改成“默认配置上下文”

#### 文件

- `src/trace2skill_distiller/cli/main.py`
- `src/trace2skill_distiller/core/config.py`
- `src/trace2skill_distiller/mining/sources/__init__.py`

#### 变更内容

- 删除 `init` 和 `distill`/`run` 上的 `--source` 临时覆盖选项。
- `sessions list`、`sessions show`、`inspect session`、`run` 全部默认读取当前配置中的 `source`。
- 在 `config show` 中显式展示“当前 source 类型 + 当前 source 路径”。
- 在 `doctor` 输出中显式打印当前 source，并检查对应路径是否存在/可访问。
- `create_source()` 保持按 `SourceConfig.type` 分派，不需要改底层接口，只需要把 source 的“当前上下文”角色在 CLI 中表达清楚。

#### 为什么这样改

- 符合已确认的产品方向：source 和模型、并发一样属于长期运行设置。
- 避免不同 coding 软件里同名 project 导致结果混淆。

### 5. 新增 doctor 与 runs，删除 status 和 schedule

#### 文件

- `src/trace2skill_distiller/cli/main.py`
- 可能需要读取现有状态与报告：`src/trace2skill_distiller/output/state.py`

#### 变更内容

- 新增 `doctor`
  - 检查配置文件存在性
  - 检查 `.env` 中必要值
  - 检查当前 source 路径/目录
  - 视实现复杂度决定是否加“轻量模型连通性检查”；若复杂度过高，可先只做静态检查
- 新增 `runs list`
  - 展示历史 run id、时间、输出目录、已处理会话数、已生成报告
- 新增 `runs show <run-id>`
  - 展示某次运行的 report/state 摘要
- 删除 `status`
- 删除 `schedule` 整组命令及其帮助文案入口

#### 实施注意

- 如果当前 `state.json` 数据不足以支撑 `runs list/show`，本次实现应优先复用 HTML report 文件命名和状态文件中的最小字段，先做“可用的基础版”而不是引入复杂新的运行元数据存储。
- 若发现 `runs show` 需要稳定的 run 元数据结构，可在执行阶段补充 `state.py` 或 report 生成逻辑，但不要扩展成完整任务调度系统。

### 6. 更新帮助文案与 README

#### 文件

- `src/trace2skill_distiller/cli/main.py`
- `README.md`

#### 变更内容

- 重写根命令 docstring，反映新的用户路径：
  - `init`
  - `config show`
  - `sessions list`
  - `inspect session`
  - `run`
  - `runs list`
- 所有命令 help 文案遵循产品语义，不再提“step 1/1.5/2/3”“skill_md”等内部实现词。
- README 的“使用”与“配置管理”部分全面替换为新命令示例。
- README 明确说明：
  - 当前 source 写在配置里
  - `project` 只在当前 source 范围内解释
  - 默认不跨多种 coding 软件遍历

### 7. 补齐测试

#### 文件

- `tests/test_cli.py`
- `tests/test_config.py`
- 必要时新增：
  - `tests/test_cli_run.py`
  - 或继续扩展 `tests/test_cli.py`

#### 变更内容

- 为 CLI 新命令树补充最低限度的回归测试：
  - 根 help 包含新命令，不包含旧命令
  - `config show` 能展示当前 source
  - `config set source.type chrys` / `config set concurrency.workers 4` 能落盘
  - `sessions list` 调用当前配置的 source，不暴露 `--source`
  - `inspect session <id>` 仍可正常调用预处理入口
  - `run --mode preprocess|analyze|full` 能正确映射到底层执行参数
  - `run --preview` 不触发写文件逻辑
- 为配置层补充测试：
  - `DistillConfig.load()` 正确加载 source
  - `set_config_value()` 支持新增暴露键
  - `init_default_config()` 初始化结果包含默认 source

#### 为什么这样改

- 当前 CLI 测试覆盖太弱，若不补测试，这次命令树重构很容易继续出现“help 说一套，行为做一套”。

## Implementation Order

1. 先改 `core/config.py`
   - 明确配置键暴露范围
   - 完成 CLI 需要的配置读写支持
2. 再改 `cli/main.py`
   - 重建命令树
   - 删除旧命令
   - 接入新的 `run`、`doctor`、`runs`
3. 按需改 `orchestrator/pipeline.py`
   - 对齐 `mode` / `preview` 语义
   - 修正现有副作用与重复写 report 问题
4. 更新 README
5. 补全/更新 CLI 与配置测试
6. 执行诊断和测试验证

## Verification Steps

### 自动化验证

- 运行 CLI 与配置相关测试：
  - `tests/test_cli.py`
  - `tests/test_config.py`
- 若新增独立 CLI 测试文件，一并执行。

### 手工验证

- `trace2skill --help`
  - 只出现新命令树，不出现 `distill`、`status`、`schedule`
- `trace2skill config show`
  - 能看到当前 source、模型、并发
- `trace2skill config set source.type chrys`
  - 配置文件正确更新
- `trace2skill sessions list`
  - 默认基于配置中的 source 查询
- `trace2skill inspect session <id>`
  - 只做查看，不写产物
- `trace2skill run --mode preprocess`
  - 只到预处理阶段
- `trace2skill run --mode analyze`
  - 展示主题和规则，不写技能文件
- `trace2skill run --preview`
  - 不写任何输出文件、不写报告、不更新状态
- `trace2skill runs list`
  - 能看到已有运行历史或给出清晰空状态

### 完成标准

- CLI 顶层命令和帮助文案与新设计一致。
- `source` 成为持久化默认上下文，不再要求用户每次运行时声明。
- `run` 替代旧 `distill`，`preview` 和 `mode` 语义真实可靠。
- 旧的误导性入口 `status`、`schedule` 被移除。
- 测试覆盖关键新行为，README 与 CLI 一致。
