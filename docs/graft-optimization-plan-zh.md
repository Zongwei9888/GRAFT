# GRAFT 反效果问题整理与优化计划

状态：**优化计划 v1；代码候选已实现，M1–M3 效果 Gate 尚未通过**
日期：2026-08-12  
依据：冻结原稿、当前产品代码、Terminal-Bench 因果 checkpoint replay 与
`experiments/terminal_bench/ADVERSE_EFFECT_AUDIT.md`

本计划不修改 `GRAFT Original` 的身份。现有算法和处理固定为实验 baseline；所有优化进入独立的
`value-aware` 策略，只有通过预注册 Gate 后才能成为默认产品策略或论文主方法。

截至 2026-08-12，Phase 0–3 的产品代码和离线 selection replay 入口已经实现，但这不等于
研究假设成立。当前冻结状态如下：

| 项目 | 实现状态 | 证据状态 |
|---|---|---|
| Original baseline 隔离 | 完成；默认仍是 `graft-original` | 100 tests 与 release check 已通过 |
| 语义 evidence / stage cost | 完成 | 覆盖率 M0 实验统计未完成 |
| LLM completion gate | 完成；只判断生命周期 | M1 标注集尚未建立 |
| Value-aware + No-Op | 完成；显式 opt-in | M2 校准与 baseline replay 尚未通过 |
| task-epoch 三类预算 | 完成；未知费用显式记录 | 预算参数尚未校准 |
| feedback promotion guard | 完成；四态结果 | M3 历史回归 replay 尚未通过 |
| shared-prefix benchmark | 未开始 | 必须等待 M1–M3 |

## 1. 当前结论

当前证据不能支持“GRAFT 优于 Native Codex”。更准确的结论是：

- 历史实验中有一次确认的 feedback-induced semantic harm；
- 两个精确 first-Stop checkpoint replay 实验均得到 `delta_feedback = 0`；
- 后两次没有代码伤害，但分别增加约 6m38s 和 6m47s 的 Stop 成本；
- 高阶共同失效图能够识别 same-Codex lineage 和 oracle 缺失；
- 反效果主要产生在生命周期触发、已有证据建模、决策效用和反馈保护层，而不是因为系统没有 LLM；
- 当前实现应被视为失败的研究原型和可复现实验 baseline，不是已成立的质量提升产品。

## 2. 问题分级

### 2.1 已确认问题

| ID | 问题 | 直接证据 | 后果 |
|---|---|---|---|
| C1 | `completion` 实际只是 changed workspace at Stop | checkpoint policy 丢弃最后回复，只检查 tree hash | 多轮 session 中可能过早触发 |
| C2 | 选择目标是 expected detection，不是净效用 | objective 不减 wall/token/repair-regression cost | 很小的正检测概率也会花预算 |
| C3 | 没有显式 No-Op 竞争项 | 只有低至 0.01 的 gain/cost threshold | 已正确候选仍启动昂贵验证 |
| C4 | Codex 已有证据没有进入选择器 | `PostToolUse` 只保存输入/输出哈希，modeler 不读取 | verifier 重复 Codex 已做的测试和调查 |
| C5 | 名义 cost 没有校准实际成本 | cost 1.25/2.0 对应 verifier 可运行至 240 秒 | 预算数值不代表时间、Token 或钱 |
| C6 | child verifier usage 不完整 | Harbor 只记录 producer turn usage | 无法计算真实 GRAFT 总成本 |
| C7 | 缺少外部 oracle 时仍会购买同源推理 | `shadow-relay` 图识别盲点但仍选择两个 verifier | 高 residual risk 与高成本同时出现 |
| C8 | feedback 后没有候选 promotion/rollback guard | 历史 payments 任务确认 broad repair 回归 | 正确 finding 也可能导致更差修复 |
| C9 | 每轮 Stop 可重新获得预算 | budget 以 round 而非 session/task epoch 管理 | continuation 多轮时成本可倍增 |

### 2.2 有强证据、仍需定量验证的问题

| ID | 假设 | 需要的验证 |
|---|---|---|
| H1 | 便宜 LLM completion gate 能减少普通 session 的误触发 | 多轮对话标注集上的 precision/recall 与延迟 |
| H2 | producer semantic evidence 能显著减少重复 verifier | 有/无 evidence ledger 的选择重叠和成本消融 |
| H3 | actionability/repairability 比 detection probability 更能预测最终收益 | finding→repair→reward 数据上的校准曲线 |
| H4 | 高阶 lineage 在控制净效用后仍优于 pairwise/single reviewer | 同预算、同 false-block 率的 held-out 对照 |
| H5 | 安全 promotion guard 能减少 feedback-induced regression | shared-prefix fork 中的负 `delta_feedback` 比率 |

### 2.3 不能用优化叙事掩盖的限制

- LLM 不能从不存在的 repository oracle 中恢复唯一答案；
- 多个 fresh Codex thread 仍可能共享模型与任务解释盲点；
- hidden benchmark 标签只能用于最终 evaluator，不能进入在线选择；
- completion gate 只能决定“是否适合验证”，不能证明候选正确；
- 硬编码某类任务的检查器可以提高该类 benchmark 分数，但不属于 GRAFT 的泛化方法。

## 3. 冻结约束

后续实现必须遵守：

1. `GRAFT Original` 代码路径、配置和实验结果保留为 baseline，不就地改名；
2. 新策略名为 `value-aware`，通过显式配置启用，初期不是默认策略；
3. 不添加语言、框架、benchmark task、隐藏测试或任务答案的封闭规则；
4. LLM 继续负责 task-specific Behavior、Failure Mode、completion 语义和 verifier 实例化；
5. 确定性代码只负责通用生命周期、状态、成本、证据和安全协议；
6. 每次新 benchmark 实验前固定 commit、任务选择、fallback、指标和分析顺序；
7. 未通过本计划 Gate 的能力不得写成已经有效。

## 4. 目标架构

```text
UserPromptSubmit
    → TaskEpochStore：原始需求与任务边界

PostToolUse
    → ProducerEvidenceLedger：测试、命令、退出状态、修改与成本的有界语义记录

Stop
    → deterministic guards
        ├─ 无 diff / 已验证 / 重复反馈 → No-Op
        └─ 有新候选状态
             ↓
       LLM Completion Gate
        ├─ intermediate / question / explanation / blocked → No-Op
        └─ candidate_complete
             ↓
       LLM task model + verifier planner
             ↓
       Value-Aware Selector（包含 No-Op=0）
        ├─ max LCB(ΔV) ≤ 0 → No-Op
        └─ max LCB(ΔV) > 0 → 执行 verifier
             ↓
       Evidence Gate
        ├─ 不可复现 / 不可行动 / 缺 oracle → unresolved 或 allow
        └─ 可复现且预计可修复 → continuation
             ↓
       Candidate Promotion Guard
        ├─ finding 被修复且旧证据不回归 → 接受新 checkpoint
        └─ 未修复或发生回归 → 保留旧 checkpoint，停止或有限反馈
```

## 5. 分阶段优化计划

### Phase 0 — 测量层先行

目标：在不改变 verifier 选择结果的情况下，补齐真实成本和 producer evidence。

实现：

- 新增有界 `ProducerEvidenceRecord`：tool family、命令/结果预览、退出码、修改路径、持续时间
  与结果状态；不推断语言或测试框架类别；
- 保留原始 payload hash 用于完整性，但不再把 hash 当成语义；
- 对敏感值做截断/脱敏，不解析不稳定的私有 transcript；
- 为 task modeler 和 selector 提供聚合 evidence summary；
- 分别记录 modeler、planner、每个 verifier 和 continuation 的 wall time、usage 与估算费用；
- 将 budget 改为 task-epoch ledger，但 Original baseline 仍按旧规则运行。

涉及模块：

- `src/graft/codex/hooks.py`
- `src/graft/codex/session_state.py`
- `src/graft/schema.py`
- `src/graft/modeling.py`
- `src/graft/codex/cli_runner.py`
- `src/graft/controller.py`

Gate M0：

- 现有 76 tests 与 release check 全部通过；
- 至少 95% 的实验工具事件具有可用的 family/outcome/duration；
- child stage wall time 全覆盖，usage 不可得时必须显式 `unknown`，不能计为零；
- observation-only 对 checkpoint 和 producer 输出零修改。

### Phase 1 — 自然 completion gate

目标：只在准备交付的候选 checkpoint 上进入昂贵建模。

实现：

- 保留无 diff、已验证、反馈后未变化等 deterministic guards；
- 新增隔离、低成本的结构化 LLM gate，输出：
  `candidate_complete | intermediate | question | explanation | blocked | abstain`；
- 输入只包含 task epoch、最后回复、diff 摘要和 producer evidence summary；
- 最后一条回复只作为生命周期信号，不作为正确性证据；
- `abstain` 默认 No-Op 并记录，研究配置可单独测试保守策略；
- 显式 `[graft:verify]` 模式继续保留，作为用户主动触发的独立对照。

Gate M1：

- 建立至少 50 个不含 benchmark 答案的多轮 session checkpoint 标注集；
- `candidate_complete` precision ≥ 0.90；
- 相比 changed-Stop baseline，昂贵 GRAFT 调用减少至少 50%；
- 完成交付 checkpoint 的漏触发率和不同任务类型分层结果必须报告，不能只看总体准确率；
- gate 本身的 P95 wall time 和 Token 成本单列。

### Phase 2 — Value-Aware Selector 与显式 No-Op

目标：把“可能发现问题”转换为“值得在现在购买这份证据”。

为每个候选估计：

```text
ΔV(f | S, E_agent)
  = P(eligible finding | x, f, E_agent)
    × P(successful repair | finding, x, f)
    × repair value
    - predicted execution cost
    - predicted feedback-regression cost
```

实现要求：

- `No-Op` 是 utility 0 的正式候选；
- 检测概率必须是相对 `E_agent` 的边际检测，而不是绝对检测；
- 实际成本至少包含 wall time 和模型 Token，名义 cost 只作为冷启动 prior；
- actionability、producer controllability、oracle availability 和 repairability 进入估计；
- 使用不确定性下界 `LCB(ΔV)`，而不是只用 LLM 点估计；
- session/task epoch 总预算不能在每次 Stop 重置；
- 高阶共同失效仍用于联合 finding probability，但不能替代净效用计算。

代码隔离：

- 保留 `OriginalDetectionSelector`；
- 新增 `ValueAwareSelector`；
- 配置写入 `selection_policy: original | value-aware`；
- 两者共享 schema 和执行器，但报告必须记录 policy/version。

Gate M2：

- 先在已有 checkpoint/finding/repair 日志做离线 replay；
- 在相同候选池上，value-aware 的估计净效用优于 Original、best-single、pairwise 与 run-all；
- 至少能对两个最新零收益任务选择 No-Op，且不能通过 task 名称或隐藏结果实现；
- 校准误差、覆盖率、选择频率与成本曲线全部报告；
- M2 未通过前不运行新的昂贵 Terminal-Bench treatment。

### Phase 3 — 安全反馈与候选 promotion

目标：即使 finding 正确，也降低 Codex broad repair 造成回归的风险。

实现：

- continuation 前永久保存 pre-feedback checkpoint 和 evidence set；
- feedback 只包含行为、复现、期望/实际、checkpoint key 和报告路径，不给修复计划；
- 新 checkpoint 必须重新执行原 finding，并重放仍适用的 pre-feedback evidence；
- 将状态分成 `fixed_and_preserved`、`not_fixed`、`regressed`、`unresolved`；
- 产品路径不自动覆盖用户文件；发生回归时保留 archive，并用有限 continuation 请求 Codex 恢复；
- 研究评估对每个 feedback round 计算精确 `delta_feedback`。

Gate M3：

- 所有 feedback 实验都具有可验证 pre/post checkpoint；
- 不再出现无法确定反馈因果方向的 treatment；
- 已知历史 regression replay 能被 promotion guard 拒绝；
- 没有 task-specific rollback 逻辑。

### Phase 4 — 共享前缀因果实验

目标：消除 Native/GRAFT 独立 Codex rollout 造成的主要混杂。

每个任务先产生一个共同 producer checkpoint，然后分叉：

```text
同一个 first-Stop checkpoint
        ├─ Control：直接运行官方 evaluator
        └─ Treatment：value-aware GRAFT → 必要时 continuation → evaluator
```

对照至少包括：

- checkpoint/Native stop；
- GRAFT Original；
- completion-only；
- value-aware without lineage；
- full value-aware GRAFT；
- run-all，在预算可行时作为上界而非产品策略。

主要指标：

- `delta_feedback` 和负 delta 比率；
- 最终 hidden reward/pass rate；
- verification trigger rate；
- false-block、abstain、timeout 和 actionable repair rate；
- producer、GRAFT 和总 wall/token/currency cost；
- reward per cost 与净效用；
- 按任务、模型 lineage、是否存在 repository oracle 分层。

实验阶梯：

1. 已有轨迹离线 replay；
2. 不含隐藏 benchmark 的多轮 trigger 集；
3. 预注册 10 个未见任务 × 2 个 producer seeds 的小型 Gate；
4. 只有前三步为正，才扩展到至少 30 个任务 × 3 seeds；
5. hidden evaluator 始终最后打开，且只用于最终评分与事后诊断。

Gate E：

- full value-aware 相对共同 checkpoint 的平均净效用为正；
- 负 `delta_feedback` 率不能高于预注册安全界；
- 在等总成本下优于 Original 和至少一个强 baseline；
- lineage 消融产生稳定、可解释损失，否则高阶共同失效不能作为核心贡献；
- 任一关键 Gate 失败，停止“GRAFT 提升 Codex”的主张，只保留 measurement/negative-result 方向。

安全界具体数值必须在打开新任务结果前，根据 Phase 0 成本分布和样本量分析另行预注册；本文件不
用未经校准的任意百分比伪造统计保证。

## 6. 推荐实施顺序

严格顺序如下，不能一边看新 benchmark 结果一边改策略：

1. 冻结当前 commit 作为 Original baseline；
2. 完成 Phase 0，只收集不改变决策的测量数据；
3. 完成 Phase 1，在人工可审计多轮集验证触发策略；
4. 用已有轨迹完成 Phase 2 离线 replay；
5. 完成 Phase 3 的 promotion guard 与回归测试；
6. 冻结 value-aware commit 和实验配置；
7. 预注册 Phase 4 任务、seed、fallback、指标和终止条件；
8. 执行 shared-prefix 实验；
9. 结果通过才扩大实验，否则停止产品默认化和论文正向主张。

## 7. 第一批具体开发任务

| 顺序 | 任务 | 产物 |
|---:|---|---|
| 1 | 定义 semantic telemetry 与 stage-cost schemas | schema、兼容迁移、单测 |
| 2 | 重构 `PostToolUse` 为有界语义 ledger | telemetry 文件与脱敏测试 |
| 3 | 把 evidence summary 注入 task modeler/planner | structured prompt 与 snapshot 测试 |
| 4 | 增加 completion gate 接口和 LLM 实现 | gate report、abstain/No-Op 状态 |
| 5 | 把 Original selector 命名并冻结 | policy version 与回归 fixture |
| 6 | 实现实际成本模型、No-Op 和 `ValueAwareSelector` | `graft replay-selection` |
| 7 | 加入 task-epoch budget 与 candidate promotion | pre/post evidence report |
| 8 | 同步 plugin runtime、文档和安装包 | plugin/source parity tests |
| 9 | 运行 M0–M3，不通过则修测量层而不碰新任务 | Gate 报告 |
| 10 | 冻结并执行 Phase 4 | 因果实验结果 |

## 8. 变更控制

为避免计划再次失控：

- 核心定义只由 `docs/method-original-frozen.md` 和
  `docs/graft-core-definition-zh.md` 管理；
- 本文件只管理优化，不重写原始研究问题；
- 每项实验结论必须标记为 confirmed、supported hypothesis 或 untested；
- 每个策略报告 `method_version`、`selection_policy`、commit 和 config hash；
- 任何新增 task-specific rule 都视为违反计划并阻止 release；
- 固定的 enum、schema、安全边界和未校准超参数属于方法协议，不得冒充任务语义；
- 计划变更必须新增版本和理由，不能覆盖旧结论。
