# ChatBI Harness 开发工作区

本 repo 用 `/orchestrate` 多 Agent 工作流开发 ChatBI Harness。

## 仓库布局

- `harness/` - **ChatBI Harness 源料**。开发时不生效：Claude Code 只读 repo 根
  `.claude/`，不会自动加载 `harness/.claude/`。所以 harness 的 hooks 不会 fire、
  commands 不会出现在 `/` 列表、rules 不会进上下文。harness 代码就是"源料"，
  只有测试和 build 时用。包含：harness 代码（`.claude/{lib,hooks,commands,skills,
  schemas,fixtures,rules}`）+ 契约（`CLAUDE.md`/`CONTEXT.md`）+ 文档（`docs/`）+
  `e2e-state.py`。
- `.claude/` - **orchestrate 工作流**（`agents/{plan,coder,test}-agent.md` +
  `commands/orchestrate.md`）。不带 harness。
- `tests/` - 测试 `harness/` 里的代码（`PYTHONPATH` 指向 `harness/.claude/lib`）。
- `docs/` - 开发文档（`dev-cycle-*`、`feature-flow-*`、`orchestrator-state`、
  `test-*`、`optimization-*`、`requirements`、`technical-design`）。
- `build-product.sh` - 从 `harness/` 子集构建 `../chatbi`（干净安装产物）。
- `AGENTS.md` - orchestrate 工作流详细约定。

## 开发流程

优先用 `/orchestrate`（详见 `AGENTS.md` + `.claude/commands/orchestrate.md`）。
不要绕过流程直接写代码。

## 构建 + 测试

```sh
python3 -B -m unittest discover -s tests/harness   # 测试 harness/ 代码
./build-product.sh                                   # 构建产品 ../chatbi
```

## harness 治理契约

harness 的治理契约在 `harness/CLAUDE.md`（产品安装时用），**不在 repo 根**。
开发 harness 时按需读它；repo 根跑 `/orchestrate` 不受 harness 治理约束。
