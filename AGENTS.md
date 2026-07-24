# 项目工作流约定

本项目使用多 Agent 编排开发流程。**优先使用 `/orchestrate` 命令**，不要绕过流程直接写代码或改代码。

## 启动命令

| 场景 | 命令 |
|---|---|
| 新项目（从需求开始） | `/orchestrate new "需求一句话描述"` |
| 老项目改造 | `/orchestrate legacy "项目路径 + 改动需求"` |
| 从断点继续 | `/orchestrate continue` |
| 查看 agent 是否加载 | `/agents` |

## Agent 分工（不要混用）

- **plan-agent**: 需求、技术设计、评估、测试清单。**只写文档，不写代码。**
- **coder-agent**: 代码实现、读代码产出功能流程文档。**功能流程文档必须反映代码实际行为，禁止复述设计文档。**
- **test-agent**: 执行测试、产出结构化测试报告。**只报告 bug，不修 bug。**

## 文档约定

所有产出物放 `docs/` 下，命名规则严格遵守：

```
docs/
├── orchestrator-state.md           # 当前流程进度，每个节点完成后更新
├── requirements.md                 # 需求文档（plan-agent 产出）
├── technical-design.md             # 技术设计（plan-agent 产出）
├── dev-cycles.md                   # 开发周期划分（coder-agent 产出）
├── dev-cycle-{N}.md                # 第 N 个周期的开发文档
├── feature-flow-v{N}.md            # 功能流程文档（coder-agent 产出，反映代码现状）
├── optimization-checklist-v{N}.md  # 评估优化清单（plan-agent 产出）
├── test-checklist-v{N}.md          # 测试清单
├── test-report-v{N}.md             # 测试报告
└── （老项目场景下文件名带 -{service}-{module} 后缀）
```

**版本号 v{N}**：每次迭代 +1，不覆盖旧版本，方便回溯。

## 关键工作流原则

### 文档对账机制
- plan-agent 的设计/修改文档 = 期望
- coder-agent 的功能流程文档 = 事实
- 两者 diff 出优化清单 → 循环到收敛

**coder-agent 绝不能把设计文档复述一遍当功能流程文档**，否则永远收敛不了。

### STATUS 信号
每个 subagent 响应末尾必须有状态行，orchestrator 靠它判断下一步：
- `STATUS: CONVERGED` / `NEEDS_ITERATION`（plan-agent）
- `STATUS: READY_FOR_REVIEW`（coder-agent）
- `STATUS: ALL_PASSED` / `HAS_FAILURES` / `BLOCKED`（test-agent）

如果某次响应漏了 STATUS，直接要求补上，不要自行推断。

### 循环上限
所有自动循环硬上限 **5 轮**。到上限不再自动迭代，必须由人决策。

### 完成检查清单（强制）

**宣布"项目完成"或"COMPLETE"之前，orchestrator 必须逐项自检：**

| # | 检查项 | 验证方式 |
|---|--------|---------|
| 1 | 所有 cycle CONVERGED | optimization-checklist 无 CRITICAL/HIGH |
| 2 | 所有测试通过 | `pytest tests/ -q` 全部 pass |
| 3 | **`docs/technical-design.md` 已更新为 AS_BUILT** | 读取文件确认 STATUS: AS_BUILT，且内容反映最新代码/文件结构 |
| 4 | 文件清单完整 | 对所有产出物做 `ls` 或 `find` 验证 |

> **第 3 项是最常漏的**。所有 cycle 结束后，必须显式 dispatch plan-agent 执行"根据最新代码更新 tech-design → AS_BUILT"这一步，不得跳过。即使"只改了文档没动代码"，也须确认 tech-design 版本号、文件清单、deviation records 与实际一致。

检查清单全部 [x] 后才能更新 `orchestrator-state.md` 为 COMPLETE。

## 人工介入时机

orchestrator 默认只在以下 3 种情况打断我：
1. 需求/设计/修改文档定稿前的最后确认
2. 评估或测试循环达到 5 轮上限
3. subagent 报告 BLOCKED 或异常

其他场景全自动推进。**不要因为单个文件改动、库选型、文档格式细节来问我。**

## 不在工作流里的请求

如果我直接说"帮我改一下 src/xxx.py"这种**绕过工作流**的请求：
- 小修小补（typo、注释、显式我说的单行修改）→ 直接做
- 涉及功能逻辑变更 → 提醒我："这个改动建议走 `/orchestrate legacy`，要不要我启动？"
- 紧急 hotfix → 直接做，但改完提醒我同步更新 feature-flow 文档

## 项目特定信息

<!-- 下面这块按你项目实际情况填，删掉不适用的 -->

- 主要语言/框架: <!-- 例如 Python 3.11 + FastAPI -->
- 测试框架: <!-- 例如 pytest -->
- 代码风格: <!-- 例如 black + ruff -->
- 是否微服务: <!-- 是/否，是的话列出服务名 -->
- 部署环境: <!-- 例如 Docker + k8s -->
