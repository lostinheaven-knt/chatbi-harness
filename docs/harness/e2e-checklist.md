# 真实 E2E 清单 v2（Cycle 5 Task 06 — 人工门）

> v1 跑完后修了 4 个 gap（见下方"v2 改了什么"）。**必须在独立 E2E 工作区跑**，绝不要把完整 hook 块粘进 dev 的 `chatbi-cc-dev/.claude/settings.json`（热重载会锁死 dev 会话）。CC 2.1.217 ≥ 目标 2.1.216。每个事件记录：确切命令、exit code、净化输出、model id。

## v2 改了什么（已离线验证，533 测试绿）

1. **SessionStart**：`CHATBI_PYTHON` 环境变量必须指向 3.10+ 的 python（harness 用 `@dataclass(slots=True)`，3.9 不行）。Mac 上 `/usr/bin/python3` 是 3.9，不能用；用 homebrew 3.14。
2. **Stop gate**：真实 CC Stop 事件只有 `session_id`、没有 `open_findings`。现在门控：有 `session_id` 且无 state → 默认干净 stop（exit 0），**不再死锁**；有 state 文件则按 state 判定。
3. **PostToolUse / SubagentStop gate**：这俩是硬门控，业务字段（`impact_manifest` / `review`）必须由 flow **写到 `.chatbi/runs/<session_id>/`**，门控从那里读（真实 CC 事件不带这俩字段）。flow 不写 → 门控 exit 2（fail-closed，正确）。
4. **config 自死锁缓解**：`chatbi-harness.json` 有未声明字段（如 `_comment`）时，`pretool_guard` 现在**允许读配置文件本身**（让 agent 诊断 + 告诉你怎么修），写仍阻断（SEC-003）。

---

## 0. 一次性准备

> ⚠️ **必须先 `rm -rf /tmp/chatbi-e2e`**。否则第二次 `cp -R chatbi /tmp/chatbi-e2e` 会把 chatbi **嵌套**进旧的 /tmp/chatbi-e2e（旧 .claude/ 保留 = 旧 hook + 被污染的配置），你测的就是旧代码。第一次跑就踩了这个坑。

```bash
rm -rf /tmp/chatbi-e2e                       # 关键：清掉旧的污染工作区
cp -R <chatbi-product-root> /tmp/chatbi-e2e
cd /tmp/chatbi-e2e
# 关键：设置 3.10+ python（homebrew 3.14）
export CHATBI_PYTHON=/opt/homebrew/bin/python3
claude --version   # 2.1.217
```

## 1. 注册 live hooks → `/tmp/chatbi-e2e/.claude/settings.json`

```json
{
  "hooks": {
    "SessionStart":  [{"matcher": "startup|resume|clear|compact","hooks":[{"type":"command","command":".claude/hooks/session_diagnose"}]}],
    "PreToolUse":    [{"matcher": "Edit|Write|MultiEdit|Bash|Read|Grep|Glob","hooks":[{"type":"command","command":"python3 -B -I .claude/hooks/pretool_guard.py"}]}],
    "PostToolUse":   [{"matcher": "Edit|Write|MultiEdit|Bash","hooks":[{"type":"command","command":"python3 -B -I .claude/hooks/posttool_impact.py"}]}],
    "SubagentStop":  [{"matcher": "*","hooks":[{"type":"command","command":"python3 -B -I .claude/hooks/subagent_review_gate.py"}]}],
    "Stop":          [{"matcher": "*","hooks":[{"type":"command","command":"python3 -B -I .claude/hooks/stop_gate.py"}]}],
    "ConfigChange":  [{"matcher": "*","hooks":[{"type":"command","command":"python3 -B -I .claude/hooks/config_change_gate.py"}]}]
  }
}
```

## 2. 启动 `claude`（确保 `CHATBI_PYTHON` 已 export），触发 6 事件

### ✅ SessionStart
- [ ] `/clear`。预期：`session_diagnose` 跑出 init 报告（不再 "Python binding unavailable"）。

### ✅ PreToolUse
- [ ] **阻断**：让 Claude 往工作区外写 `/tmp/external.txt` → exit **2**（SCOPE-001/002）。
- [ ] **放行**：让 Claude 编辑工作区内文件 → exit **0**。
- [ ] **config 诊断读**：用 shell 手动往 `.claude/chatbi-harness.json` 加个 `_comment` 字段（**别让模型加**——模型该用 `description`），然后让 Claude 读这个文件 → 现在 exit **0**（允许诊断读，stderr 有 "allow-diagnostic-read"）；验证完用 shell 删掉 `_comment`。**注意**：要加注释就用 schema 认可的 `description` 字段（schema 已支持），别用 `_comment`/`_note`（schema 严格、会违规）。

> 下面 3 个门控的业务字段（impact_manifest / review / open_findings）真实 CC 事件不带，门控从 `.chatbi/runs/<session_id>/` 读，找不到再 fallback 到 `.chatbi/runs/current/`。**你不用找 session_id**——用工作区根目录的 `e2e-state.py` 把现成 fixture 写到 `current/` 即可。在**另一个终端**（不是 claude 那个）跑，同一个工作区目录。

### ⚠️ PostToolUse
- [ ] **放行**：终端 B（`/tmp/chatbi-e2e`）跑 `python3 e2e-state.py impact-pass` → 它把合法 manifest 写到 `.chatbi/runs/current/impact_manifest.json`。然后终端 A（claude）打 `跑一下 ls`（一个 benign Bash）。PostToolUse fire → 读 current → exit **0**（claude 会显示成功）。
- [ ] **阻断**：终端 B `python3 e2e-state.py impact-block`（写未同步 manifest）。终端 A 再打 `跑一下 ls` → exit **2**（DOC-004，stderr 有 "unsynced"）。
- [ ] **不写 state**：终端 B `python3 e2e-state.py clear`。终端 A `跑一下 ls` → exit **2**（缺 impact_manifest，fail-closed）。

### ⚠️ SubagentStop
- [ ] **放行**：终端 B `python3 e2e-state.py review-pass`。终端 A 打 `调用 adversarial-reviewer 子 agent 审一下"revenue_example last month"这个候选`（让它真的派生子 agent；子 agent 结束时 SubagentStop fire）。exit **0**（PASS verdict）。
- [ ] **阻断**：终端 B `python3 e2e-state.py review-block`。终端 A 同样派生子 agent → exit **2**（BLOCKED verdict，REV-002/003）。
- [ ] **只读确认**：终端 A 打 `读 .claude/agents/adversarial-reviewer.md，确认 tools 行只读` → 应看到 `tools: Read, Grep, Glob`。
- 注：如果保守的模型不肯派生子 agent（说"做不到"），SubagentStop 这条就 live 测不了——记下来，离线已验证（见 compatibility.md）。

### ✅ Stop
- [ ] **干净**：终端 B `python3 e2e-state.py findings-clean`（或 `clear`）。终端 A 让本轮结束（不打了 / `/exit`）→ exit **0**（无 block finding）。
- [ ] **阻断**：终端 B `python3 e2e-state.py findings-block`。终端 A 结束本轮 → exit **2**（open block finding，REV-003）。
- 注：第一次跑的死锁（连卡 9 次）已修——有 session_id 无 state 时默认干净 stop，不再强制结束。

### ✅ ConfigChange
- [ ] **阻断**：终端 A 打 `编辑 .claude/chatbi-harness.json，把 workspace.allow_candidate_writes 改成 false`（削弱写入边界）→ ConfigChange fire → exit **2**。
- [ ] **放行**：终端 A 打 `编辑 .claude/chatbi-harness.json，把 governance.owners.default_domain_owner 设为 domain_owner_example`（有效、非削弱改动）→ exit **0**。
- 注：权限/沙箱类削弱（删 deny、关 sandbox）模型会自己拒（SEC-001/SEM-003），不必非得让 gate 拦。

## 3. 生产无连接 STOP
- [ ] 产品配置默认 `adapters.semantic=[]`（没配真实 adapter）。终端 A 打 `/chatbi-analyze`，给它一个数据问题（如 "上个月 revenue_example 多少"）。预期：**STOP** fail-closed（SEM-001/PORT-001，"no usable adapter"），不回退 Fixture。
- 注：保守模型可能不肯跑 `/chatbi-analyze`（说入口未就绪）。这条离线已验证（`test_e2e.py test_production_no_connection_stops`）；live 跑通了就记，跑不通就标"离线已验证"。

## 4. 沙箱（环境支持）
- [ ] 开 deny-write/deny-execute → 真实阻断。不支持 → 保留 BLOCKING GAP，不伪造。

## 5. 记录到 `docs/harness/compatibility.md`
每条一行：
```
- <Event>: mode=<e2e-state.py 的参数 或 "live">, exit=<0|2>, model=<id>, note=<净化摘要>
```
明确 6 个 P0 事件是否全部触发。**session_id 已不是问题**（用 `current` fallback，`e2e-state.py` 写到 `.chatbi/runs/current/`）。

## 6. 通过 → HOOK-003/005 = IMPLEMENTED（46/46）→ AS_BUILT → COMPLETE

## 清理
```bash
rm -rf /tmp/chatbi-e2e   # 一次性；dev 原封不动
```

## 已知 gap（live 才能确认；现在已降级）
- ~~**session_id 来源**~~：已解决——门控现在 fallback 读 `.chatbi/runs/current/`，`e2e-state.py` 直接写那儿，不用 session_id。
- **真实 CC 事件字段名**：`ConfigChange` 的 `source`、`SubagentStop` 的 `stop_hook_active` 等若和门控假设不一致，门控容忍未知字段（HOOK-003）不会崩，但记成偏差。
- **reviewer 子 agent 真实运行**：`adversarial-reviewer.md` 工具面只读已确认（读文件）；但"真实派生 + 产出 verdict"要模型肯派生子 agent——保守模型可能拒，拒了就标"离线已验证"。
- **保守模型不肯触发**：GLM-5.2 很守治理边界，可能拒跑 Bash/派生子 agent/`/chatbi-analyze`。拒了就把该条标"live 未触发，离线已验证"，不硬来。
