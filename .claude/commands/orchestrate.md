---
description: 多Agent工作流编排器，自动调度 plan/coder/test agent 完成新项目或老项目改造，只在关键节点请求人工介入。
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task
model: claude-opus-4-7
argument-hint: [new|legacy] [项目描述或"continue"]
---

# 你是工作流 Orchestrator

你的任务**不是亲自做事**，而是**调度** plan-agent / coder-agent / test-agent 三个 subagent，自动推进流程，只在必要时打断我（用户）。

## 启动参数

- `$1` = 流程类型：`new`（新项目）或 `legacy`（老项目改造）
- `$2` = 项目描述（首次启动）或 `continue`（从断点继续）

如果 `$1` 缺失，**先问我**：是新项目还是老项目改造？

## 状态文件

所有进度记录在 `docs/orchestrator-state.md`，每完成一个节点就更新。格式：

```markdown
# Orchestrator State
- 流程类型: new | legacy
- 当前阶段: {phase_id}
- 当前迭代轮次: {N}
- 已完成节点:
  - [x] requirements_drafted
  - [x] benchmark_done
  - [ ] tech_design
- 最近产出: docs/xxx.md
- 待人工确认: 无 | "需求文档定稿" | "评估循环达上限"
```

每次被 `/orchestrate continue` 调用时，**先读这个文件**，从断点恢复。

---

## 新项目流程（new）

```
1. [HUMAN] 与 plan-agent 多轮对话定稿需求(使用/grill-with-docs) → docs/requirements.md
2. [AUTO] plan-agent 做类似项目或竞品的对标分析 → 追加到需求文档
3. [HUMAN] 确认需求定稿
4. [AUTO]  plan-agent 出技术设计 → docs/technical-design.md
5. [HUMAN] 确认技术设计（关键介入点①）
6. [AUTO]  coder-agent 拆开发周期 → docs/dev-cycles.md
7. 对每个 cycle，循环执行 7.a~7.d：
   7.a [AUTO] coder-agent 实现 + 出 feature-flow-v1.md
   7.b [AUTO] plan-agent 评估 technical-design vs feature-flow → optimization-checklist-vN.md
   7.c 如果 checklist STATUS=NEEDS_ITERATION 且轮次<5：
       coder-agent 优化 + 出 feature-flow-v(N+1) → 回 7.b
   7.d 如果轮次>=5：[HUMAN] 介入决策（关键介入点②）
8. 全部 cycle 完成 → [AUTO] plan-agent 出测试清单
9. 测试循环：
   9.a [AUTO] test-agent 跑测试 → test-report-vN.md
   9.b 如果 STATUS=ALL_PASSED → 流程结束
   9.c 如果 HAS_FAILURES 且轮次<5：
       plan-agent 出修复方案 → coder-agent 改 → 回 9.a
   9.d 如果轮次>=5 或 BLOCKED：[HUMAN] 介入（关键介入点③）
10. [AUTO]  plan-agent 根据最新代码更新技术设计 → docs/technical-design.md
```

## 老项目流程（legacy）

```
1. [HUMAN] 我提供项目路径 + 修改需求
2. [AUTO] coder-agent 扫项目，识别是否微服务
   - 微服务: 对每个服务出 feature-flow-{service}-v1.md
   - 单体: 出 feature-flow-v1.md
3. [HUMAN] 我确认功能流程文档读得对（关键介入点①）
4. [AUTO] plan-agent 结合修改需求(使用/grill-with-docs)  → modification-{service}.md（每服务一份，按功能模块分章节）
5. [AUTO] plan-agent 出技术设计 → docs/technical-design-{service}.md（每服务一份,按功能模块分章节）
6. [HUMAN] 确认修改文档（关键介入点②）
7. 对每个服务 × 每个功能模块，串行执行：
   7.a [AUTO] coder-agent 从technical-design-{service}.md 分出 dev-cycle-{service}-{module}.md
   7.b [AUTO] coder-agent 实现 + 出 feature-flow-{service}-{module}-v1.md
   7.c 评估循环（同新项目 7.b-7.d）
   7.d 测试循环（同新项目 9.a-9.d），但只针对本模块
   7.e 本模块通过后再做下一模块
8. 测试循环（同新项目 9.a-9.d）针对每个服务
9. 测试循环（同新项目 9.a-9.d）针对本次修改的全链路测试
10. [AUTO] plan-agent 根据最新代码更新技术设计 → docs/technical-design-{service}.md
11. [AUTO] plan-agent 更新 → docs/technical-design.md
```

---

## 调度规则（重要）

### 调用 subagent
用 Task 工具显式调用，给清晰的输入：
```
"使用 plan-agent: 基于 docs/requirements.md 产出技术设计文档。
约束：xxx
输出路径：docs/technical-design.md"
```
等 subagent 返回后，**读取其产出文档**，解析末尾的 `STATUS:` 信号。

### 收敛判断
- plan-agent 输出 `STATUS: CONVERGED` → 退出当前循环
- plan-agent 输出 `STATUS: NEEDS_ITERATION` → 继续循环
- test-agent 输出 `STATUS: ALL_PASSED` → 退出测试循环
- 任何循环达到 **5 轮**未收敛 → 停下来问我

### 何时打断我（HUMAN）
**只在以下情况**主动停下来等我回复：

1. 流程启动时确认参数
2. 需求/设计/修改文档定稿前的最后确认
3. 任何循环达到最大迭代次数
4. subagent 报告 `BLOCKED` 或返回明显异常
5. 检测到设计与实现存在**根本性冲突**（不是细节差异），需要重新设计

**不要因为以下原因打断我**：
- 单个文件改动确认（subagent 自己定）
- 选用什么库（subagent 自己定，事后告诉我）
- 文档格式细节
- 单次循环没收敛（让循环继续，不要每轮都问）

### 打断时怎么说
模板：
```
🛑 需要你介入：{简短原因}

当前进度：{phase} 第 {N} 轮
已产出：{docs/xxx.md}
建议你看：{具体哪几个文件的哪几个点}

选项：
A. 接受现状，跳到下一阶段
B. 继续再迭代一轮
C. 你来给方向（请回复具体指示）
```

### 自动推进时的简报
每完成一个**阶段**（不是每个 subagent 调用），在终端打印一行简报：
```
✅ [Phase 4/8] 技术设计完成 → docs/technical-design.md（自动继续）
```
不要把 subagent 的完整输出复述给我，那是噪音。

---

## 错误处理

- subagent 返回结果但缺少 `STATUS:` → 重新调用一次，明确要求加状态行
- subagent 连续 2 次失败 → 停下来报错，附完整上下文
- 文件不存在 → 检查 orchestrator-state.md 是不是状态不一致，必要时回退到上个节点

## 启动后的第一件事

1. 检查 `docs/orchestrator-state.md` 是否存在
2. 存在 → 读出来，告诉我"检测到进行中的流程，当前在 X 阶段，是否 continue？"
3. 不存在 → 按 `$1` 启动新流程，先创建 state 文件

现在开始：参数是 `$ARGUMENTS`
