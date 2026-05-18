# Workflow Controller 完整设计

本文档定义 `agent-harness-cli` 从单次 artifact 验收工具升级为主会话 workflow controller 的完整设计。目标场景是长流程 agentic 任务，例如研究、写作、数据分析、代码迁移和多轮评审。

当前 CLI 的核心形态是：

```text
task.json + external check commands + report store + paginated viewer
```

完整 workflow controller 在此基础上增加：

```text
workflow graph + state store + node checks + transition policy + hook decisions
```

该设计只使用当前 Codex 主会话推进任务。harness 负责状态、验收、转移、报告和 Stop hook continuation；主会话负责根据 hook 提示继续产出 artifacts。

## 设计目标

完整设计必须支持：

- 一个任务由多个可验收节点组成，而不是只有一次最终验收。
- 节点可以是普通阶段、判断门、路由器、并行组、汇合点、子流程、人工确认点或终态。
- 每个节点声明 artifacts、checks、通过标准、失败路由和 transition policy。
- Codex Stop hook 每次触发时根据 state 验收当前 active 节点集合。
- 当前节点未通过时，默认保持 active 并返回 `decision: "block"`，要求主会话修复当前节点。
- 当前节点通过但 workflow 未完成时，harness 更新 state，仍返回 `decision: "block"`，要求主会话执行下一步。
- workflow 完成前 Stop hook 始终 block；完成后才允许 Codex 正常结束。
- transition 不局限于单个目标节点，可以表达 repeat、advance、branch、fan_out、join、wait_for_user、complete、fail 和 handoff。
- 当多个 transition 同时可行时，workflow 可以让模型在受控选项中选择下一步。
- 所有状态推进都必须写入 report 和 history，支持人类审查、Agent 继续工作和问题复现。

## 非目标

该设计不把 CLI 变成：

- 通用 agent runtime。
- 远程任务调度平台。
- 内置研究方法库。
- 内置 LLM judge 平台。
- 多 Agent 编排器。
- 项目测试、review、lint 或领域判断的替代品。

CLI 负责机械骨架：状态、验收、报告、转移和 hook 输出。领域逻辑仍然属于 workspace：workflow spec、artifacts、check scripts、checklists、rubrics 和 fixtures。

## 核心模型

完整 workflow controller 由四层组成：

```text
artifact contract
  当前节点应该产出什么，保存在哪里，格式是什么，是否必需。

acceptance checks
  如何判断当前节点是否可接受，哪些失败阻塞，哪些失败用于路由。

transition policy
  根据 check 结果、score、metadata、state 和人工输入，决定下一步动作。

state store
  记录 workflow 当前 active 节点、历史、锁、人工等待、模型选择和完成状态。
```

边界规则：

- check 判断事实，不直接推进 state。
- transition 根据事实和 state 决定下一步。
- 主会话负责执行 hook continuation 中要求的工作。
- state 是唯一任务进度来源。
- report 是 Agent、人类和 hook 共享的可审计上下文。

## Workflow Graph

workflow spec 使用 graph，而不是线性 stage list。每个 node 是一个可验收或可控制的 workflow 单元。

基础结构：

```json
{
  "id": "research_workflow",
  "description": "Research a topic and produce a paper draft.",
  "version": 1,
  "initial": ["goal_analysis"],
  "nodes": [
    {
      "id": "goal_analysis",
      "type": "stage",
      "title": "研究目标分析",
      "artifacts": [],
      "checks": [],
      "transitions": []
    }
  ],
  "defaults": {
    "report_dir": "reports",
    "state_path": ".agent-harness/state.json",
    "stop_hook_blocks_until_completed": true
  }
}
```

节点类型：

```text
stage
  普通工作阶段。通常要求 artifacts 和 checks。

gate
  判断门。重点是对已有信息做验收或路由，可能不创建新 artifact。

router
  显式路由节点。根据 check metadata、state 或用户输入选择后续路径。

parallel
  并行展开节点。激活多个后续节点。实际执行仍由主会话逐项完成。

join
  汇合节点。等待多个 upstream 节点完成后再推进。

subworkflow
  子流程节点。引用另一个 workflow spec，并把其完成状态作为当前节点结果。

human_approval
  人工确认节点。harness 进入 waiting 状态，直到收到明确批准或修改意见。

terminal
  终态。可以是 completed、failed、cancelled 或 handed_off。
```

节点必须有稳定 `id`。`title` 用于报告和 hook prompt。`type` 决定默认执行语义，但不替代 checks。

节点可以声明 `decision_policy`，用于控制多个可行 transition 的选择方式：

```json
{
  "decision_policy": {
    "mode": "model_choice",
    "require_reason": true
  }
}
```

`decision_policy.mode` 支持：

```text
auto_first
  按 transitions 顺序选择第一个匹配项。

model_choice
  当多个 transition 可行时，harness 不直接推进 state，而是进入 choosing 状态，让主会话从受控选项中选择。

user_choice
  当多个 transition 可行时，进入 waiting 状态，请求用户选择。

router_command
  调用 workspace 自定义 router command，由该命令在候选 transition 中选择。
```

## Artifact Contract

artifact contract 描述节点期望看到的产物：

```json
{
  "name": "sources",
  "path": "sources.json",
  "type": "json",
  "required": true,
  "producer": "literature_collection",
  "description": "Structured list of collected sources."
}
```

通用字段：

```text
name         稳定名称，供 checks 和 prompts 引用。
path         workspace 相对路径或绝对路径。
type         markdown、json、csv、directory、image、pdf、docx 等。
required     是否必须存在。
producer     产出该 artifact 的节点。
consumers     依赖该 artifact 的后续节点。
min_bytes     可选的基础存在性要求。
schema        可选的 JSON schema 路径。
```

原则：

- 任何实质节点都应绑定可观察 artifact。
- 不应只用自然语言说明“已完成”。
- artifact 是主会话与 checks 之间的交付边界。
- 跨节点依赖应通过 artifact 名称和路径显式表达。

## Check Contract

check 仍然是外部命令。harness 为每个 check 写入 input JSON，并要求 check 在 stdout 输出结构化 check-result JSON。

check 输入包含 workflow 上下文：

```json
{
  "root": "/workspace/project",
  "task_path": "/workspace/project/workflow.json",
  "workflow": {},
  "state": {},
  "node": {},
  "check": {},
  "artifacts": {}
}
```

check result 保持当前契约，并允许补充 metadata：

```json
{
  "check": "source_count",
  "passed": false,
  "score": 0.5,
  "severity": "error",
  "summary": "Only 6 sources were found; at least 12 are required.",
  "reasons": [
    {
      "file": "sources.json",
      "message": "The source list is too small.",
      "suggestion": "Collect at least 6 more relevant sources.",
      "requires_user_input": false,
      "evidence": {
        "actual_sources": 6,
        "min_sources": 12
      }
    }
  ],
  "metadata": {
    "actual_sources": 6,
    "min_sources": 12,
    "source_categories": ["paper", "policy"]
  }
}
```

check 设计规则：

- 一个 check 文件只关注一个行为 concern。
- 优先确定性检查，再使用 checklist 或 LLM-assisted judge。
- LLM judge 的调用逻辑留在 workspace check 脚本中。
- check 不直接改 state，不决定最终转移。
- 所有 check 命令必须只在 stdout 输出 check-result JSON，日志写 stderr。

## Transition Policy

transition 描述验收后如何改变 workflow state。

完整 action 集合：

```text
repeat
  保持当前节点 active，要求补齐或修复。

advance
  当前节点完成，激活一个目标节点。

branch
  根据条件选择一个目标节点。

fan_out
  当前节点完成，同时激活多个目标节点。

join
  等待多个 upstream 节点全部完成，再激活目标节点。

wait_for_user
  进入人工等待状态，并给出需要用户回答的问题或确认事项。

complete
  标记 workflow 完成。

fail
  标记 workflow 无法自动继续，通常需要人工处理。

handoff
  标记 workflow 交接给外部系统或人类。
```

transition 示例：

```json
{
  "id": "ready_for_processing",
  "when": "passed && checks.source_count.score >= 1.0",
  "action": "advance",
  "to": "data_processing",
  "label": "进入数据处理",
  "prompt": "信息收集已通过。请进入数据处理阶段，产出 processed_data.csv 和 data_processing_notes.md。"
}
```

transition 可以携带选择菜单所需的字段：

```json
{
  "id": "collect_more_sources",
  "when": "passed",
  "action": "advance",
  "to": "literature_collection",
  "label": "补充更多来源",
  "prompt": "如果当前资料覆盖仍偏窄，请回到资料收集阶段。",
  "commands": [
    "agent-harness choose collect_more_sources --state .agent-harness/state.json --reason \"资料覆盖仍偏窄\""
  ]
}
```

字段语义：

```text
label
  给模型或用户看的短选项名。

prompt
  选择该 transition 后给下一轮主会话的执行提示。

commands
  harness 在 hook prompt 或 status 输出中展示的建议命令。实现也可以由 CLI 自动生成，不要求 workflow 手写。

requires_reason
  覆盖 node decision_policy.require_reason，用于强制本 transition 必须带选择理由。
```

条件表达式应支持：

```text
passed
failed
checks.<name>.passed
checks.<name>.score
checks.<name>.metadata.<key>
state.completed contains "<node_id>"
state.active contains "<node_id>"
artifact.<name>.exists
user.approved
```

失败转移默认关闭。blocking check 失败时，节点应停留在 active 状态并要求修复。只有节点或 transition 显式声明 `allow_failure_transition: true` 时，`when: "failed"` 才能改变 state。这类失败转移适合 gate、router 或 review 节点，其语义是“判断结果需要路由”，不是“验收崩溃但继续推进”。

失败路由示例：

```json
{
  "id": "collect_more_information",
  "when": "failed",
  "allow_failure_transition": true,
  "action": "advance",
  "to": "literature_collection",
  "prompt": "信息完整性不足。请回到相关研究信息收集阶段补齐缺口。"
}
```

transition 选择规则：

```text
1. 运行当前 active 节点的 checks。
2. 计算 node outcome：passed、failed、warning_only、waiting。
3. 评估所有 transitions 的 when，得到候选 transitions。
4. 过滤未被允许的失败转移。
5. 根据 node.decision_policy 选择 transition 或进入 choosing/waiting 状态。
6. 如果已有合法选择，应用 action 生成 state patch。
7. 写入 report.transition、report.options 和 state.history。
8. 生成 hook decision。
```

`model_choice` 语义：

```text
如果候选 transition 数量为 1:
  可以直接应用该 transition，除非 node 声明 always_require_choice。

如果候选 transition 数量大于 1:
  不直接推进 active。
  state.status 进入 choosing。
  state.choices 记录候选 transition 和 prompt。
  hook 返回 decision=block，列出可选项和建议命令。
  主会话必须运行 agent-harness choose <transition-id> 记录选择。
  下一次 step 校验选择后再推进 state。
```

模型选择不等于直接改 state。模型只能提交受控选择，harness 负责校验并在下一次 `step` 中应用 transition。

如果没有匹配 transition：

```text
passed node
  标记配置错误，block，并提示补充 transition。

failed node
  保持 active，block，并提示修复失败 checks。

waiting node
  保持 waiting，block，并提示需要的输入。
```

## State Store

state 文件默认位于：

```text
.agent-harness/state.json
```

完整 state 结构：

```json
{
  "workflow_id": "research_workflow",
  "workflow_version": 1,
  "status": "running",
  "active": ["literature_collection"],
  "completed": ["goal_analysis"],
  "blocked": [],
  "waiting": [],
  "choosing": [],
  "failed": [],
  "choices": {},
  "artifacts": {},
  "locks": {},
  "last_step": {
    "step_id": "20260518-001",
    "node_ids": ["goal_analysis"],
    "report_id": "20260518-001-goal-analysis",
    "outcome": "passed"
  },
  "history": [
    {
      "step_id": "20260518-001",
      "node": "goal_analysis",
      "report_id": "20260518-001-goal-analysis",
      "outcome": "passed",
      "transition": "ready_for_sources",
      "active_before": ["goal_analysis"],
      "active_after": ["literature_collection"],
      "created_at": "2026-05-18T10:00:00Z"
    }
  ]
}
```

状态语义：

```text
running
  workflow 正在进行，Stop hook 必须 block。

waiting
  workflow 需要人工输入或外部条件，Stop hook 仍 block，并给出明确问题。

choosing
  workflow 已完成当前验收，但需要模型或用户在多个受控 transition 中选择下一步。

completed
  workflow 完成，Stop hook 允许结束。

failed
  workflow 无法自动继续，Stop hook block 并要求人工介入。

cancelled
  用户取消 workflow，Stop hook 不再推进。
```

一致性要求：

- state 写入使用临时文件和原子 replace。
- `step` 每次生成 `step_id`，并记录 active_before / active_after。
- 重复执行同一个 `step_id` 不得重复推进 state。
- active、completed、waiting、choosing、failed 之间不允许同一个节点重复出现，除非节点声明 `reentrant: true`。
- choices 中的 transition id 必须来自当前 node 的候选项。
- state schema 版本必须记录，workflow spec 变更时需要检测兼容性。

## Report Schema

workflow report 不只回答 checks 是否通过，还回答状态是否推进。

示例：

```json
{
  "report_id": "latest",
  "workflow_id": "research_workflow",
  "workflow_version": 1,
  "step_id": "20260518-002",
  "created_at": "2026-05-18T10:30:00Z",
  "status_before": "running",
  "status_after": "running",
  "active_before": ["literature_collection"],
  "active_after": ["information_completeness_review"],
  "nodes": [
    {
      "node_id": "literature_collection",
      "node_title": "相关研究信息收集",
      "outcome": "passed",
      "summary": {
        "total_checks": 2,
        "failed_checks": 0,
        "blocking_failures": 0,
        "warning_failures": 0
      },
      "checks": []
    }
  ],
  "transition": {
    "selected": "ready_for_completeness_review",
    "action": "advance",
    "state_changed": true,
    "prompt": "相关研究信息收集已通过。请进入信息完整性判断阶段。"
  },
  "options": [],
  "hook": {
    "decision": "block",
    "reason_kind": "stage_advanced",
    "reason": "literature_collection 已通过。请进入 information_completeness_review。"
  },
  "next_actions": [
    "Write coverage_report.md and evaluate whether the collected sources cover the research questions."
  ]
}
```

report 必须回答：

```text
这次 step 验收了哪些节点？
每个节点为什么通过、失败或等待？
是否执行了 transition？
是否存在需要模型选择的 options？
state 如何变化？
下一次 Codex 应该做什么？
hook 为什么 block 或允许结束？
```

## CLI 命令

CLI 命名应区分两类能力：

```text
run-checks
  现有单次 artifact 验收命令，保持向后兼容。

workflow commands
  面向完整 workflow controller 的状态推进、查看、人工确认和维护命令。
```

命名原则：

- 动词优先，命令名直接表达动作。
- workflow 主循环使用 `step`，因为它表示“执行一次状态机步进”，不暗示一定成功推进。
- 人工决策使用 `approve` / `reject`，避免把人工确认混入 `step`。
- 危险或改变历史的维护命令使用明确对象名，例如 `reset-node`。
- 校验类命令使用 `validate-*`，不读取或修改运行态 state。
- 所有 workflow 命令都显式接收 `--state`，避免隐式改错任务状态。

保留当前单次验收命令：

```bash
agent-harness run-checks --task task.json
agent-harness view latest
```

完整 workflow 命令命名和职责：

`agent-harness step --task workflow.json --state .agent-harness/state.json`

执行一次 workflow controller 步进。它会读取 workflow spec 和 state，验收当前 active 节点，评估 transition，原子更新 state，写入 report，并输出人类 summary 或 hook JSON。这是 Stop hook 调用的主命令。

`agent-harness status --task workflow.json --state .agent-harness/state.json`

查看当前 workflow 运行态。输出当前 status、active nodes、waiting nodes、choosing nodes、最近一次 report、下一步建议和是否允许 Codex 结束。该命令只读，不运行 checks，不修改 state。

`agent-harness history --state .agent-harness/state.json`

查看 workflow 历史。按 step 展示 node outcome、transition、report id、active_before、active_after 和时间。用于审计状态推进过程和定位某次错误转移。

`agent-harness options --state .agent-harness/state.json`

查看当前 choosing 节点的可选 transition。输出每个选项的 transition id、label、prompt、目标 node、建议命令和是否需要 reason。该命令只读，不修改 state。

`agent-harness choose <transition-id> --state .agent-harness/state.json --reason "..."`

记录模型或用户对下一步的选择。该命令校验当前 state 确实处于 choosing，校验 transition 属于候选项，并把选择写入 state。它不直接绕过 checks，也不直接改 active；下一次 `step` 会应用该选择。

`agent-harness view latest --report-dir reports`

查看 report。继续支持现有 report，同时扩展 workflow report 的节点过滤、失败过滤、分页和 JSON 输出。它不读取或修改 state，只负责把 report 转成 Agent 和人类可读的上下文。

`agent-harness reset-node <node-id> --state .agent-harness/state.json`

维护命令。将指定 node 从 completed、failed 或 waiting 状态移回 active。用于人工修复错误状态。该命令必须写入 history，记录 reset 原因和操作者输入。

`agent-harness approve <node-id> --state .agent-harness/state.json`

批准 `human_approval` 节点。写入用户批准结果，使下一次 `step` 可以匹配 `user.approved` transition。该命令只记录决策，不直接绕过 transition。

`agent-harness reject <node-id> --state .agent-harness/state.json --reason "..."`

拒绝 `human_approval` 节点。写入用户拒绝原因，使下一次 `step` 可以匹配 `user.rejected` transition，并把原因带入 report 和 hook prompt。

`agent-harness cancel --state .agent-harness/state.json`

取消当前 workflow。将 state 标记为 `cancelled`，停止后续自动推进。该命令用于用户明确终止任务，不用于普通失败处理。

`agent-harness validate-workflow --task workflow.json`

静态校验 workflow spec。检查 schema、node id、transition target、join wait_for、artifact 引用、失败转移权限和基本 graph 可达性。该命令不读取或修改 state。

通用选项命名：

```text
--task
  workflow spec 或现有 task.json 路径。

--state
  workflow state 文件路径。

--report-id
  本次写入的 report id。Stop hook 可固定为 latest，history 仍应记录不可变 step report。

--report-dir
  report 存储目录。

--node
  限定只处理或查看某个 node。

--reason
  在 `choose`、`reject`、`reset-node` 等命令中记录选择或维护原因。

--hook-json
  输出 Codex Stop hook 可直接消费的 JSON。

--json
  输出普通机器可读 JSON，不包含 Codex hook decision 语义。
```

`step` 执行顺序：

```text
load workflow spec
validate workflow graph
load or initialize state
acquire state lock
resolve active nodes
run checks for ready active nodes
evaluate transitions
enter choosing or apply recorded choice when needed
apply state patches atomically
write workflow report
emit hook JSON or human summary
release state lock
```

hook JSON 模式：

```bash
agent-harness step \
  --task workflow.json \
  --state .agent-harness/state.json \
  --report-id latest \
  --hook-json
```

失败时输出：

```json
{
  "decision": "block",
  "reason": "当前节点 literature_collection 验收未通过。请查看 reports/latest.json，修复失败项后再次停止。"
}
```

通过但未完成时输出：

```json
{
  "decision": "block",
  "reason": "literature_collection 已通过。请进入 information_completeness_review，并产出 coverage_report.md。"
}
```

等待人工输入时输出：

```json
{
  "decision": "block",
  "reason": "workflow 正在等待人工确认 topic_scope_approval。请让用户确认研究范围后运行 agent-harness approve topic_scope_approval。"
}
```

需要模型选择下一步时输出：

```json
{
  "decision": "block",
  "reason": "information_completeness_review 已通过，但存在多个后续选项。请运行 agent-harness options 查看选项，然后用 agent-harness choose <transition-id> --reason \"...\" 记录选择。"
}
```

完成时输出：

```json
{
  "systemMessage": "Agent harness workflow completed."
}
```

## Stop Hook 行为

Stop hook 应保持很薄：

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"

if [[ "${AGENT_HARNESS_HOOK_ACTIVE:-}" == "1" ]]; then
  exit 0
fi
export AGENT_HARNESS_HOOK_ACTIVE=1

agent-harness step \
  --task workflow.json \
  --state .agent-harness/state.json \
  --report-id latest \
  --hook-json
```

hook 不应承担业务路由、报告解析或状态写入。复杂逻辑放在 CLI 中，workspace 只配置 workflow 和 checks。

## 并行和 Join

workflow 支持多个 active 节点。`fan_out` 会同时激活多个目标节点：

```json
{
  "id": "analysis_ready",
  "when": "passed",
  "action": "fan_out",
  "to": ["figure_drawing", "conclusion_synthesis"],
  "prompt": "数据分析已通过。请推进图表绘制和结论整理。"
}
```

join 节点等待 upstream 全部完成：

```json
{
  "id": "writing_ready_join",
  "type": "join",
  "title": "论文撰写准备汇合",
  "wait_for": ["figure_drawing", "conclusion_synthesis"],
  "transitions": [
    {
      "id": "ready_for_paper_writing",
      "when": "passed",
      "action": "advance",
      "to": "paper_writing",
      "prompt": "图表和结论均已完成。请进入论文撰写阶段。"
    }
  ]
}
```

并行语义：

- `fan_out` 只表示多个节点同时 active，不表示 CLI 会并发运行任务。
- 主会话按 hook 提示逐项完成 active 节点。
- `step` 可以验收所有 ready active nodes，也可以通过 `--node` 限定一个节点。
- 一个 active 节点失败不应阻止其他已 ready 节点被验收，除非 workflow 声明 `fail_fast: true`。
- join 节点只有在所有 `wait_for` 节点进入 completed 后才可通过。
- report 应记录每个 active 节点的独立 outcome。

## 人工确认

human approval 是 workflow 的正式节点类型，不应藏在自然语言 prompt 中。

示例：

```json
{
  "id": "topic_scope_approval",
  "type": "human_approval",
  "title": "研究范围人工确认",
  "question": "请确认 research_goal.md 中的研究范围是否可以进入资料收集。",
  "transitions": [
    {
      "id": "approved",
      "when": "user.approved",
      "action": "advance",
      "to": "literature_collection",
      "prompt": "研究范围已确认。请进入资料收集。"
    },
    {
      "id": "needs_revision",
      "when": "user.rejected",
      "action": "advance",
      "to": "goal_analysis",
      "prompt": "用户要求调整研究范围。请修改 research_goal.md。"
    }
  ]
}
```

CLI 提供：

```bash
agent-harness approve topic_scope_approval --state .agent-harness/state.json
agent-harness reject topic_scope_approval --state .agent-harness/state.json --reason "范围过宽"
```

## 锁和幂等

Stop hook 可能重复触发。完整设计要求：

- state 文件写入前获取 lock。
- lock 包含 owner、created_at、expires_at。
- stale lock 可以被显式清理。
- 每次 `step` 使用唯一 `step_id`。
- report id 可以固定为 `latest`，但 history 必须记录不可变 step report id。
- transition application 必须检查 active_before，避免基于过期 state 写入。

## 研究任务完整示例

下面以“围绕某个主题完成研究并产出论文草稿”为例。该例子是设计示意，不要求放入核心项目树。

任务目标：

```text
围绕“在效率时代保持深度思考”完成一项小型研究，形成可追溯资料、分析、图表和论文草稿。
```

workflow 节点：

```text
goal_analysis
  研究目标分析，产出 research_goal.md。

topic_scope_approval
  人工确认研究范围。

literature_collection
  相关研究信息收集，产出 sources.json 和 literature_notes.md。

information_completeness_review
  信息完整性判断，产出 coverage_report.md。

data_processing
  数据处理，产出 processed_data.csv 和 data_processing_notes.md。

data_analysis
  数据分析，产出 analysis_notes.md。

figure_drawing
  图表绘制，产出 figures/ 和 figure_index.md。

conclusion_synthesis
  结论整理，产出 conclusions.md。

writing_ready_join
  等待图表和结论都完成。

paper_writing
  论文撰写，产出 paper.md。

self_review
  自评审，产出 review_report.md。

revision
  修改，产出 paper_revised.md 和 revision_log.md。

completed
  终态。
```

关键路径：

```text
goal_analysis
  -> topic_scope_approval
  -> literature_collection
  -> information_completeness_review
  -> model chooses one of: literature_collection, data_processing, topic_scope_approval
  -> data_analysis
  -> [figure_drawing, conclusion_synthesis]
  -> writing_ready_join
  -> paper_writing
  -> self_review
  -> completed
```

可能回路：

```text
topic_scope_approval rejected -> goal_analysis
information_completeness_review failed -> literature_collection
self_review failed -> revision -> self_review
```

### Workflow 示例

```json
{
  "id": "deep_thinking_research",
  "description": "Research deep thinking in the age of efficiency and produce a paper draft.",
  "version": 1,
  "initial": ["goal_analysis"],
  "defaults": {
    "report_dir": "reports",
    "state_path": ".agent-harness/state.json",
    "stop_hook_blocks_until_completed": true
  },
  "nodes": [
    {
      "id": "goal_analysis",
      "type": "stage",
      "title": "研究目标分析",
      "artifacts": [
        {
          "name": "research_goal",
          "path": "research_goal.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "goal_has_scope",
          "command": ["{python}", "checks/check_goal_scope.py"],
          "severity": "error",
          "config": {
            "artifact": "research_goal"
          }
        }
      ],
      "transitions": [
        {
          "id": "scope_ready_for_user",
          "when": "passed",
          "action": "advance",
          "to": "topic_scope_approval",
          "prompt": "研究目标分析已通过。请请求用户确认 research_goal.md 中的研究范围。"
        }
      ]
    },
    {
      "id": "topic_scope_approval",
      "type": "human_approval",
      "title": "研究范围人工确认",
      "question": "请确认 research_goal.md 中的研究范围是否可以进入资料收集。",
      "transitions": [
        {
          "id": "scope_approved",
          "when": "user.approved",
          "action": "advance",
          "to": "literature_collection",
          "prompt": "研究范围已确认。请进入相关研究信息收集阶段。"
        },
        {
          "id": "scope_rejected",
          "when": "user.rejected",
          "action": "advance",
          "to": "goal_analysis",
          "prompt": "用户要求调整研究范围。请修改 research_goal.md。"
        }
      ]
    },
    {
      "id": "literature_collection",
      "type": "stage",
      "title": "相关研究信息收集",
      "artifacts": [
        {
          "name": "sources",
          "path": "sources.json",
          "type": "json",
          "required": true
        },
        {
          "name": "literature_notes",
          "path": "literature_notes.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "source_count",
          "command": ["{python}", "checks/check_source_count.py"],
          "severity": "error",
          "config": {
            "artifact": "sources",
            "min_sources": 12
          }
        },
        {
          "name": "source_diversity",
          "command": ["{python}", "checks/check_source_diversity.py"],
          "severity": "error",
          "config": {
            "min_categories": 3
          }
        }
      ],
      "transitions": [
        {
          "id": "need_more_sources",
          "when": "passed && checks.source_diversity.score < 1.0",
          "action": "repeat",
          "prompt": "来源数量足够，但类型不够多。请补充不同类型来源。"
        },
        {
          "id": "ready_for_completeness_review",
          "when": "passed",
          "action": "advance",
          "to": "information_completeness_review",
          "prompt": "相关研究信息收集已通过。请进入信息完整性判断阶段。"
        }
      ]
    },
    {
      "id": "information_completeness_review",
      "type": "gate",
      "title": "信息完整性判断",
      "allow_failure_transition": true,
      "decision_policy": {
        "mode": "model_choice",
        "require_reason": true
      },
      "artifacts": [
        {
          "name": "coverage_report",
          "path": "coverage_report.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "coverage_matrix_complete",
          "command": ["{python}", "checks/check_coverage_matrix.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "collect_more_information",
          "when": "failed",
          "allow_failure_transition": true,
          "action": "advance",
          "to": "literature_collection",
          "label": "回到资料收集",
          "prompt": "信息完整性不足。请回到相关研究信息收集阶段补齐缺口。"
        },
        {
          "id": "collect_more_sources",
          "when": "passed",
          "action": "advance",
          "to": "literature_collection",
          "label": "补充更多来源",
          "prompt": "如果当前资料覆盖仍偏窄，请回到资料收集阶段。",
          "commands": [
            "agent-harness choose collect_more_sources --state .agent-harness/state.json --reason \"资料覆盖仍偏窄\""
          ]
        },
        {
          "id": "start_data_processing",
          "when": "passed",
          "action": "advance",
          "to": "data_processing",
          "label": "进入数据处理",
          "prompt": "如果资料已足够，请进入数据处理阶段。",
          "commands": [
            "agent-harness choose start_data_processing --state .agent-harness/state.json --reason \"资料覆盖已足够进入处理\""
          ]
        },
        {
          "id": "ask_user_scope",
          "when": "passed",
          "action": "wait_for_user",
          "label": "请求用户确认范围",
          "prompt": "如果研究边界仍不清晰，请请求用户确认是否调整范围。",
          "commands": [
            "agent-harness choose ask_user_scope --state .agent-harness/state.json --reason \"研究范围需要用户确认\""
          ]
        }
      ]
    },
    {
      "id": "data_processing",
      "type": "stage",
      "title": "数据处理",
      "artifacts": [
        {
          "name": "processed_data",
          "path": "processed_data.csv",
          "type": "csv",
          "required": true
        },
        {
          "name": "data_processing_notes",
          "path": "data_processing_notes.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "processed_data_valid",
          "command": ["{python}", "checks/check_processed_data.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "ready_for_analysis",
          "when": "passed",
          "action": "advance",
          "to": "data_analysis",
          "prompt": "数据处理已通过。请进入数据分析阶段。"
        }
      ]
    },
    {
      "id": "data_analysis",
      "type": "stage",
      "title": "数据分析",
      "artifacts": [
        {
          "name": "analysis_notes",
          "path": "analysis_notes.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "analysis_answers_questions",
          "command": ["{python}", "checks/check_analysis_questions.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "analysis_ready",
          "when": "passed",
          "action": "fan_out",
          "to": ["figure_drawing", "conclusion_synthesis"],
          "prompt": "数据分析已通过。请推进图表绘制和结论整理。"
        }
      ]
    },
    {
      "id": "figure_drawing",
      "type": "stage",
      "title": "图表绘制",
      "artifacts": [
        {
          "name": "figures",
          "path": "figures",
          "type": "directory",
          "required": true
        },
        {
          "name": "figure_index",
          "path": "figure_index.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "figures_referenced",
          "command": ["{python}", "checks/check_figures.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "figures_done",
          "when": "passed",
          "action": "advance",
          "to": "writing_ready_join",
          "prompt": "图表绘制已完成。"
        }
      ]
    },
    {
      "id": "conclusion_synthesis",
      "type": "stage",
      "title": "结论整理",
      "artifacts": [
        {
          "name": "conclusions",
          "path": "conclusions.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "conclusions_supported",
          "command": ["{python}", "checks/check_conclusions.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "conclusions_done",
          "when": "passed",
          "action": "advance",
          "to": "writing_ready_join",
          "prompt": "结论整理已完成。"
        }
      ]
    },
    {
      "id": "writing_ready_join",
      "type": "join",
      "title": "论文撰写准备汇合",
      "wait_for": ["figure_drawing", "conclusion_synthesis"],
      "transitions": [
        {
          "id": "ready_for_paper_writing",
          "when": "passed",
          "action": "advance",
          "to": "paper_writing",
          "prompt": "图表和结论均已完成。请进入论文撰写阶段。"
        }
      ]
    },
    {
      "id": "paper_writing",
      "type": "stage",
      "title": "论文撰写",
      "artifacts": [
        {
          "name": "paper",
          "path": "paper.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "paper_structure",
          "command": ["{python}", "checks/check_paper_structure.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "ready_for_self_review",
          "when": "passed",
          "action": "advance",
          "to": "self_review",
          "prompt": "论文初稿已通过结构检查。请进入自评审阶段。"
        }
      ]
    },
    {
      "id": "self_review",
      "type": "stage",
      "title": "自评审",
      "allow_failure_transition": true,
      "artifacts": [
        {
          "name": "review_report",
          "path": "review_report.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "paper_quality_review",
          "command": ["{python}", "checks/check_paper_quality.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "needs_revision",
          "when": "failed",
          "allow_failure_transition": true,
          "action": "advance",
          "to": "revision",
          "prompt": "自评审结论要求修改。请进入 revision 阶段，产出 paper_revised.md 和 revision_log.md。"
        },
        {
          "id": "research_complete",
          "when": "passed",
          "action": "complete",
          "prompt": "研究 workflow 已完成。"
        }
      ]
    },
    {
      "id": "revision",
      "type": "stage",
      "title": "修改",
      "artifacts": [
        {
          "name": "paper_revised",
          "path": "paper_revised.md",
          "type": "markdown",
          "required": true
        },
        {
          "name": "revision_log",
          "path": "revision_log.md",
          "type": "markdown",
          "required": true
        }
      ],
      "checks": [
        {
          "name": "revision_addresses_review",
          "command": ["{python}", "checks/check_revision_log.py"],
          "severity": "error"
        }
      ],
      "transitions": [
        {
          "id": "review_revision",
          "when": "passed",
          "action": "advance",
          "to": "self_review",
          "prompt": "修改记录已通过。请重新进入自评审阶段。"
        }
      ]
    }
  ]
}
```

### Stop Hook 循环示例

当前 active 是 `literature_collection`。

第一次 Stop：

```text
harness 运行 source_count 和 source_diversity。
如果 checks 失败，active 仍为 literature_collection，并把失败 report 反馈给 Codex。
如果 checks 通过，执行 ready_for_completeness_review transition。
state.active 更新为 information_completeness_review。
hook 返回 decision=block，提示进入信息完整性判断。
```

信息完整性判断 Stop：

```text
harness 运行 coverage_matrix_complete。
如果 failed transition 被允许且检查失败，harness 可路由回 literature_collection。
如果检查通过，information_completeness_review 的 decision_policy=model_choice 生效。
harness 不直接进入 data_processing，而是进入 choosing 状态。
hook 返回 decision=block，列出 collect_more_sources、start_data_processing、ask_user_scope 等选项和 choose 命令。
主会话运行 agent-harness choose start_data_processing --reason "资料覆盖已足够进入处理"。
下一次 step 校验选择后，state.active 更新为 data_processing。
```

并行节点 Stop：

```text
data_analysis 通过后 fan_out 到 figure_drawing 和 conclusion_synthesis。
主会话逐项完成这两个 active 节点。
writing_ready_join 只有在两个节点都 completed 后才通过。
```

最后一次 Stop：

```text
self_review 通过。
harness 执行 complete transition。
state.status = completed。
hook 不再 block，Codex 可以结束任务。
```

## 完整实施计划

实现工作按能力域组织，每个工作包都以完整设计为目标，并保持现有 `run-checks` 向后兼容。

Schema 和验证：

- 定义 workflow schema、state schema、workflow report schema。
- 校验 node id 唯一性、transition 目标存在、join wait_for 存在、artifact path 合法性。
- 校验失败转移必须显式允许。
- 校验 decision_policy 和 model_choice 候选 transition。

State 和锁：

- 实现 state 初始化、读取、原子写入、锁、history 和 schema version。
- 实现 active/completed/waiting/choosing/failed/choices 的一致性检查。
- 实现重复 step 保护。

Step 运行逻辑：

- 实现 active node resolution。
- 实现 check input 扩展，加入 workflow/state/node/artifacts。
- 实现多 active 节点验收。
- 实现 transition evaluation、model choice、recorded choice application 和 state patch application。
- 实现 hook JSON 输出。

Graph 语义：

- 实现 stage、gate、router、parallel、join、subworkflow、human_approval、terminal。
- 实现 repeat、advance、branch、fan_out、join、wait_for_user、complete、fail、handoff。
- 实现条件表达式读取 checks、metadata、state、artifact 和 user decision。

CLI 和报告：

- 实现 `step`、`status`、`history`、`options`、`choose`、`reset-node`、`approve`、`reject`、`cancel`、`validate-workflow`。
- 扩展 `view` 支持 workflow report、node filter、failed-only、history view。
- 让 hook 只调用 `step --hook-json`，不在 shell 中解析报告。

测试：

- 使用 `unittest` 覆盖 state 初始化、原子写入、失败不推进、显式失败路由、fan_out、join、human approval、model choice、重复 step、report 输出和 hook JSON。
- 每个 check 命令仍必须输出结构化 check-result JSON。
- 示例 workflow 放在独立 example 仓库或 docs，不放入核心项目树作为运行样例，除非明确需要。

## 设计原则

- 任何实质节点都必须绑定可观察 artifact，避免 workflow 退化为提示词列表。
- 阶段通过只能由 checks 决定，不能由 Agent 自述决定。
- transition policy 不应藏在 check 脚本里。
- workflow 完成前 Stop hook 持续 block，但 block reason 必须区分修复、推进、等待、选择和人工确认。
- report 必须足够清楚，让 Agent 可以继续工作，也让人类可以追溯状态推进。
- 默认保持 dependency-free；只有 schema 校验或表达式解析的收益明显超过复杂度时才引入依赖。
