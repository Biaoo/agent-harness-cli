# Agent Harness CLI

[English](README.md) | [简体中文](README.zh-CN.md)

一个用于围绕 AI Agent 产出构建验收循环的实用参考框架。

`agent-harness-cli` 帮你把“Agent 看起来完成了”变成可执行、可检查的反馈。CLI 运行项目自有的 check 脚本，写入机器可读报告，给 Codex 一个可分页查看的报告界面，也可以通过显式 state 控制长流程任务。

## 什么是 Agent Harness Engineering

Agent Harness Engineering 是围绕 AI Agent 构建工程化外部系统的实践，使 Agent 的工作过程可约束、可观测、可验证，并能通过可执行反馈循环持续改进。

在更宽泛的用法里，这个外部系统可以包括任务说明、工具、运行时上下文、记忆、安全控制、执行环境、评估器、报告和人工交接。本项目是其中一个具体面向的聚焦型参考框架：Codex workspace 中的 artifact 验收循环。

它不依赖 Agent 在最终回复里的自我判断，而是定义：

- Agent 必须产出的 artifact
- 判断 artifact 是否合格的 checks
- Agent 可以检查的 report 格式
- 把失败结果转成下一轮提示的 hook 行为

`agent-harness-cli` 提供这个循环中最小可复用的骨架。领域规则仍由你的 workspace 负责：checks、rubric、fixtures，以及可选的 LLM judge 调用。

## 为什么需要它

Agentic 工作经常卡在最后一公里：artifact 已经生成，但验收标准没有被转化成 Agent 可以使用的反馈循环。

`agent-harness-cli` 提供一个项目本地验收循环的参考实现：

- 用确定性检查处理客观要求
- 用 checklist 或 LLM 辅助检查处理语义要求
- 用 warning check 提供指导，用 error check 阻断交付
- 用 JSON report 支持复现和审查
- 用分页查看支持大型报告
- 用 Codex Stop hook 把失败检查转成 continuation prompt

核心思想很简单：**失败的检查应该成为下一轮 Agent 修改的有效上下文。**

## 适合什么场景

当 Agent 生成的 artifact 可以被检查，并且你希望有一套可以复制和改造的模式时，就适合使用：

- 项目文档、提案、规格说明、研究笔记
- 需要仓库级验收规则的代码修改
- 生成的数据报告或分析结果
- 有严格长度、结构或质量要求的写作任务
- 需要在失败后自动继续迭代的长流程 Agentic 任务

核心边界是：领域逻辑放在 workspace 自己的 check 脚本中，CLI 提供统一的运行和报告查看方式。

## 它不是什么

`agent-harness-cli` 不试图成为完整的 Agent 平台。它不是：

- eval 平台
- agent runtime
- 通用 workflow engine
- 内置大量规则的检查库
- 项目测试、lint、review 或领域判断的替代品

它的价值在于提供一套参考模式：定义 artifact，运行项目自有 checks，写入报告，并把失败结果反馈给下一轮 Agent 修改。

## 相关项目

- 可运行示例：[Biaoo/agent-harness-cli-example](https://github.com/Biaoo/agent-harness-cli-example)
- 本仓库包含 CLI 和两个 Codex skill：
  `harness-check-designer` 用于设计 harness，
  `harness-workflow-runner` 用于运行已有 workflow。
  这两个 skill 都是领域无关的；具体项目负责提供自己的 workflow 说明、
  checks，以及可选的项目级 skill。
- 完整设计：[Workflow Controller 完整设计](docs/workflow-controller-design.zh-CN.md)

## 安装

使用 `uv` 安装 CLI：

```bash
uv tool install agent-harness-cli
```

也可以不做全局安装，直接运行 PyPI 上的包：

```bash
uvx --from agent-harness-cli agent-harness --help
```

## 安装 Codex Skills

设计 checks、workflow spec、hook 或项目 `AGENTS.md` 时，安装 designer skill：

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-check-designer -a codex
```

运行已有 workflow 项目时，安装 runner skill：

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-workflow-runner -a codex
```

如果要安装到 Codex 全局 skill 目录，添加 `-g`：

```bash
npx skills add Biaoo/agent-harness-cli --skill harness-check-designer -a codex -g
npx skills add Biaoo/agent-harness-cli --skill harness-workflow-runner -a codex -g
```

设计 harness 时使用 `$harness-check-designer`。运行已有 workflow 时使用
`$harness-workflow-runner`。

## 构建一个验收循环

常规使用时，应在项目里配置好检查任务和 Codex Stop hook，让 Codex 在准备停止时自动运行 harness。手动 CLI 命令主要用于开发或调试 check。

1. **写清 artifact 契约。** 定义 Codex 应该产出什么，以及保存在哪里。
2. **编码验收检查。** 在项目中编写聚焦的 check 脚本，例如 `checks/check_required_sections.py`。
3. **描述任务。** 在 `task.json` 中写入 artifact 和 checks。
4. **接入 Stop hook。** 在 Codex 准备结束时运行 `agent-harness run-checks`。
5. **失败时继续。** blocking check 失败时，返回 Codex continuation decision，让 Agent 继续工作。
6. **查看报告。** 调试时或 Agent 需要分页上下文时，使用 `agent-harness view`。

最小 `task.json`：

```json
{
  "id": "proposal_review",
  "artifacts": [
    {
      "name": "proposal",
      "path": "proposal.md",
      "type": "markdown",
      "required": true
    }
  ],
  "checks": [
    {
      "name": "required_sections",
      "command": ["{python}", "checks/check_required_sections.py"],
      "severity": "error",
      "config": {
        "artifact": "proposal"
      }
    }
  ]
}
```

最小 `.codex/hooks.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$(git rev-parse --show-toplevel)/.codex/hooks/run-agent-harness-check.sh\"",
            "async": false,
            "timeout": 360,
            "statusMessage": "Running agent harness"
          }
        ]
      }
    ]
  }
}
```

完整 Stop hook 脚本可以参考可运行示例：
[Biaoo/agent-harness-cli-example](https://github.com/Biaoo/agent-harness-cli-example)。

## Codex Stop Hooks

当 blocking check 失败时，`agent-harness run-checks` 会以 `1` 退出。这是正确的 CLI 行为。在 Codex Stop hook 中，应把这个结果转换成 Codex continuation decision：

```json
{
  "decision": "block",
  "reason": "Agent harness found blocking failures. Update the artifact and rerun the harness."
}
```

对于 Stop hook 来说，这不是拒绝本轮结果，而是告诉 Codex 用 `reason` 作为下一轮提示继续执行。应使用生成的报告，给 Codex 一个简短且可操作的 continuation reason。

## 手动调试

开发或调试 check 时，可以直接运行 CLI：

```bash
agent-harness run-checks --task task.json --report-id sample-report
```

命令会写入报告，并输出紧凑 summary：

```text
PASSED 2/2 checks
report_id: sample-report
report_path: reports/sample-report.json

Next:
  agent-harness view sample-report
  agent-harness view sample-report --failed-only
```

分页查看报告：

```bash
agent-harness view sample-report --page 1 --page-size 5
agent-harness view sample-report --failed-only
```

## 长流程 Workflow Controller

除了单次 `run-checks`，CLI 也支持用 workflow graph 管理一项长流程任务的状态。它仍然只运行当前项目声明的 checks，但会把任务拆成多个可验收节点，并在 Codex Stop hook 中持续返回 `decision: "block"`，直到 workflow 完成。

典型命令：

```bash
agent-harness validate-workflow --task workflows/<workflow>.json
agent-harness step --task workflows/<workflow>.json --hook-json
agent-harness status --state .agent-harness/state.json
agent-harness history --state .agent-harness/state.json
agent-harness options --state .agent-harness/state.json
agent-harness choose <transition-id> --state .agent-harness/state.json --reason "why this route is correct"
agent-harness approve <node-id> --state .agent-harness/state.json --reason "user approved"
agent-harness reject <node-id> --state .agent-harness/state.json --reason "user rejected"
agent-harness reset-node <node-id> --state .agent-harness/state.json --reason "need another pass"
agent-harness cancel --state .agent-harness/state.json --reason "task changed"
```

`step` 会验收当前 active 节点，写入 state/report，并根据 transition 更新下一步：

- 节点未通过：保持当前节点 active，hook block，要求修复。
- 节点通过且只有一个可行 transition：自动推进 state，hook block，提示主会话进入下一节点。
- 节点通过且存在多个可选 transition：进入 `choosing`，hook block，提示运行 `agent-harness options` 和 `agent-harness choose`。
- 人工确认节点：进入 `waiting`，等待 `approve` 或 `reject` 后再由下一次 `step` 应用转移。
- workflow 完成：`--hook-json` 输出 `systemMessage`，不再 block。

workflow 节点中的 check 会收到扩展输入：

```json
{
  "root": "project root provided by harness",
  "task_path": "workflows/<workflow>.json",
  "task": {},
  "check": {},
  "workflow": {},
  "state": {},
  "node": {},
  "artifacts": {}
}
```

完整 workflow controller 设计和研究任务示例见：
[Workflow Controller 完整设计](docs/workflow-controller-design.zh-CN.md)。

## 工作方式

核心形态是：

```text
task.json + external check commands + report store + paginated viewer
```

每个 check 都是一个普通命令。harness 会写入一个 input JSON 文件，并在命令没有显式包含 `{input}` 时自动追加 `--input <path>`。它也会把 `{python}` 替换成当前 Python 解释器。

task 中的 check 示例：

```json
{
  "name": "todo_markers",
  "command": ["{python}", "checks/todo_markers.py"],
  "severity": "warning",
  "config": {
    "patterns": ["TODO", "TBD"]
  }
}
```

每个 check 接收到的输入：

```json
{
  "root": "project root provided by harness",
  "task_path": "task.json",
  "task": {},
  "check": {}
}
```

check result 结构：

```json
{
  "check": "required_artifacts",
  "passed": true,
  "score": 1.0,
  "severity": "error",
  "summary": "All required artifacts exist.",
  "reasons": []
}
```

失败的 check 应该给出具体原因、证据和建议修复方式。

## 设计原则

- CLI 保持轻量，workspace 保持控制权。
- 优先使用确定性检查，再考虑 LLM judge。
- LLM judge 的模型调用逻辑应放在用户的 check 脚本或 workspace 中。
- warning failure 用来引导 Agent，但不阻断运行。
- error failure 用来阻断交付。
- 领域逻辑属于用户自己的 check 脚本。
- task 和 report 使用 JSON，避免引入额外 parser 依赖。
