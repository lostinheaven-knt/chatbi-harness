# Skill 路径配置说明

## 当前 agent 假设的 skill 路径

| Skill 名 | agent 期望的路径 |
|---|---|
| grill-with-docs | `~/.agents/skills/grill-with-docs/SKILL.md` |
| superpower:writing-plans | `~/.codex/superpowers/skills/writing-plans/SKILL.md` |
| superpower:executing-plans | `~/.codex/superpowers/skills/executing-plans/SKILL.md` |
| tdd | `~/.agents/skills/tdd/SKILL.md` |
| diagnose | `~/.agents/skills/diagnose/SKILL.md` |

## 验证步骤

部署完 agent 配置后，在 Claude Code 里跑：

```bash
ls -la ~/.claude/skills/
find ~/.claude/skills -name "SKILL.md"
ls -la ~/.agent/skills/
find ~/.agent/skills -name "SKILL.md"
ls -la ~/.codex/skills/
find ~/.codex/skills -name "SKILL.md"
```

## 路径不对怎么办

`superpower` 系列 skill ：

**结构 C（嵌套目录，agent 文件里假设的就是这种）**
```
~/.codex/superpowers/skills
├── writing-plans/SKILL.md
├── executing-plans/SKILL.md
```

## 防御性兜底

我已经在两个 agent 里加了这句：

> 如果 skill 路径不存在（找不到文件），先用 Glob 搜 `~/.agent/skills/**/SKILL.md` 确认实际路径，然后告诉 orchestrator 路径不对，**不要绕过 skill 自由发挥**。

所以即使路径写错，agent 也不会闷头跳过 skill 自己干，而是会停下来报错。但建议你**第一次跑前先验证路径**，避免每个 agent 都搜一遍浪费 token。

## 为什么不用 skill 的自动匹配？

Claude Code 的 skill 系统**默认不会把 skill 描述注入到 subagent 的系统提示**（参考 GitHub issue #32910）。所以 subagent 不会"自动想起来该用 grill-with-docs"——你必须在它的 prompt 里显式告诉它。

我用的是"显式 Read 路径"方案，比"暗示性地写 skill 名"更可靠，因为：
- 路径错了会立刻报错，不会静默退化
- 不依赖 LLM 的模糊匹配，避免"我以为它会调"实际没调
