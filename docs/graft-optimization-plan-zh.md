# GRAFT vNext：研究、Codex 接入与 WSDM 2027 执行计划

状态：**v2 设计冻结候选；尚未实现，尚未证明有效**
日期：2026-08-13
基线 commit：`5c972ed`
依据：冻结 DOCX、`docs/method-original-frozen.md`、现有代码、全部已记录实验、Codex 官方
生命周期与集成文档，以及截至本日期可获得的 Agent/Coding-Agent 研究。

本文件是仓库中唯一的当前优化计划。它不修改 `GRAFT Original` 的身份，也不把任何尚未实现或仅
post-hoc 观察到的机制写成已成立贡献。

## 1. 决策摘要

### 1.1 当前结论

GRAFT 与 Codex 的产品架构是可行的，但当前实现和论文效果主张都还没有通过 Gate：

- `Stop` 可以在 Codex 准备结束一个 turn 时触发外部检查，并把证据作为 continuation prompt 送回
  同一 turn/thread；这适合作为产品挂载点；
- Codex 一个 turn 内本来就会搜索、编辑、运行测试、自我修复，甚至调用子 Agent。GRAFT 不能把
  “再调用一个 Codex reviewer”当成天然新增证据；
- 当前 value-aware 实现在完整构图之后才比较 No-Op。最新精确运行在执行 verifier 前已花费
  `168.108s`，超过 `120s` task-epoch wall budget；
- 对同一个冻结图，即使移除资源约束，所有 verifier 的净值仍为负，选择器形式化选择 No-Op。
  所以问题不是预算偶然漏掉了一个好 verifier，而是先付出昂贵构图成本、再发现不值得验证；
- 当前本机同时存在旧全局 runtime 与新版 plugin/runtime。多个 Codex Hook 会并发运行，而现有
  first-writer-wins 去重没有 runtime authority，真实实验具有混版本竞态；
- 现有结果不能支持 GRAFT 优于 Native Codex、反馈提高 resolve rate，或高阶图产生正向质量收益。

### 1.2 vNext 的核心变化

```text
GRAFT Original
完整 Behavior–Failure–Verifier–Blind-spot 图
→ 最大化风险加权检测覆盖
→ 执行 verifier

value-aware v1（当前实现）
completion gate
→ 完整构图
→ 与 No-Op 比较净值
→ 执行或停止

GRAFT vNext（本计划）
唯一 runtime + checkpoint claim + 资源预留
→ 一次轻量 LLM TaskSketch
→ 估计“继续购买计算”的 Value of Computation
→ 只渐进展开可能改变决策的图分支
→ 只为候选集合懒构造高阶共同失效
→ 执行有正保守净值且有修复预算的 verifier
→ 可复现证据返回同一 Codex task epoch
→ promotion/revalidation
```

因此，新版不是放弃原始图，也不是改成硬编码检查器。变化只是把研究对象从“构完图后选哪个
verifier”扩展为“是否构图、构多少图、何时执行、何时停止购买证据”。

### 1.3 当前 Go/No-Go

| 主张或能力 | 状态 |
|---|---|
| Codex 外部 Verification Governor 的机制可行 | GO |
| 当前插件可安全默认开启 | NO-GO：先解决 runtime authority |
| 当前 value-aware v1 有正净效用 | NO-GO |
| GRAFT 提升 Codex 最终质量 | NO-GO |
| 高阶图优于 pairwise | 未验证 |
| vNext 作为候选研究方向 | GO，但必须先做离线/影子校准 |
| WSDM 2027 正向 full method claim | 条件 GO；8 月 16 日硬 Gate |
| WSDM Findings/measurement 路线 | 条件 GO |

## 2. 不允许漂移的方法契约

以下内容来自冻结原稿，在所有版本中保持不变：

1. GRAFT 位于自由 Agent Loop 外部，不规划 Agent 如何写代码或如何修复；
2. LLM 从 raw multi-turn requirements、task-start authority、候选 diff 和 producer evidence 动态生成
   开放词汇的 Behavior、Failure Mode、语义歧义和 verifier objective；
3. verifier 来自通用能力模板，可包括 Agentic Reviewer、Test Agent、LLM Judge、repository-owned
   tests、runtime probe 和静态工具；
4. 每份证据记录 model、provider、prompt、session、context、modality、test author 与 oracle lineage；
5. 多个 verifier 可能共享高阶盲点，不能把不同 thread 或不同名字当成独立证据；
6. 只有可执行、可复现、source-bound 且由原始要求或未修改 authority 支持的 failure evidence 才能
   阻塞停止；
7. 反馈回到同一个 producer thread/task epoch，由 Codex 自主决定修复策略；
8. 生产路径禁止按语言、框架、文件名、benchmark task、隐藏测试或已知答案硬编码任务语义。

固定 schema、状态机、哈希、sandbox、budget、lineage 字段、通用 verifier capability 和统计方法是
协议，不是任务硬编码。

版本永久区分为：

```text
graft-original             # 冻结原稿 baseline
graft-value-aware-v1       # 当前“完整构图后 No-Op”baseline
graft-progressive-vnext    # 本计划，尚未实现与证明
```

## 3. 对最新 Codex 运行框架的正确建模

### 3.1 Turn、thread/session 与 task epoch 不是同一概念

Codex 的一个 turn 是一次用户输入到 assistant message 的完整 Agent Loop，中间可包含很多次模型推理
与工具调用。一个 thread/session 可以包含多个 turn；assistant message 也可能是提问或阶段性汇报，
不等于整个用户任务已完成。

GRAFT 额外维护 `task_epoch`：

- 用户澄清、补充约束和回答 Codex 问题，默认仍属于同一 epoch；
- GRAFT continuation 必须显式标记为系统产生，不得进入 raw user requirements；
- `ACCEPTED` 或 `UNRESOLVED` 后的新人工请求开启下一 epoch；
- session 总预算不因 epoch 或反复 prompt 重置；
- requirements 保存有序原文，不能用最后一条 prompt 或 Codex 总结替代。

当前 exact prompt hash 可以识别未变化的 continuation，但过于脆弱。vNext 使用：

```text
feedback_id + session_id + epoch_id + checkpoint_key + origin=graft
```

作为 continuation provenance；全文 hash 只保留为完整性校验。

### 3.2 自然触发点

```text
UserPromptSubmit → 更新 task epoch / requirements
PostToolUse      → 记录 producer 已有语义证据与成本
SubagentStop     → 默认只观察，不运行完整 GRAFT
Stop(main)       → eligibility + VOC 决策点
SessionEnd       → 归档和离线校准，不能 continuation
```

`Stop` 是正确的技术边界，但不是充分的语义完成条件。GRAFT 只在主 Agent 形成“准备交付的 changed
checkpoint”时考虑昂贵验证；提问、解释、中间进度、等待审批和外部阻塞均为
`SKIP_NOT_ELIGIBLE`。

### 3.3 为什么 GRAFT 必须相对 Codex 已有证据计算边际价值

强 Coding Agent 已经会：

- 读 repository instructions 和上下文；
- 搜索相关实现与依赖；
- 修改、编译、运行测试和修复失败；
- 在较长 turn 内反复迭代；
- 生成或调用 review/subagent 工作流。

所以 verifier 的价值不是它“能否发现某类问题”，而是它在 producer 已有轨迹证据之后还能带来多少
新信息。same-model self-review、detached Codex review 和 Codex 生成的测试可以同时有用，但它们共享
模型、任务解释、repository context 或 oracle，不能被当成三份独立确认。

### 3.4 触发协议修订（2026-08-13）

Codex 调用 `Stop` Hook 与 GRAFT 购买验证计算是两个不同事件。Codex 当前不为 `Stop` 提供 matcher，
所以每个 turn 都可能启动一个极轻量 Hook 进程；这不代表每个 turn 都应进行 TaskSketch、构图或运行
verifier。冻结的分层资格协议为：

```text
Stop event
→ L0 protocol/dedup gate
→ L1 changed-artifact/checkpoint gate
→ L2 LLM lifecycle + TaskSketch gate
→ L3 VOC/resource gate
→ verifier execution
→ optional continuation and promotion
```

- `hello → 你好`、纯解释和没有 artifact 变化的 turn 必须在 L1 前后以零模型调用退出；
- 中间进度、向用户提问、等待决定或外部阻塞必须由 L2 标为 `SKIP_NOT_ELIGIBLE`；
- 只有 `ChangedArtifact ∧ NewCheckpoint ∧ CandidateComplete` 才具有 verification eligibility；
- `eligible` 仍不等于 `verify`，L3 只有在 promotion pending 或保守 `VOC > 0` 且资源可行时购买证据；
- 不使用语言、框架、任务名或 benchmark 关键词决定 eligibility；生命周期语义由 LLM 动态判断；
- trigger 的 precision/recall、false trigger、漏触发、P95 latency 和成本是独立实验对象，不能由
  selector 结果替代。

在 trigger 与 selector 均未通过 held-out Gate 前，公开插件的内置 fallback 使用 `explicit` 模式；
自动 `completion`/`strict` 仅用于受控实验或 reviewed project override。Hook 配置不得静态显示
“正在验证”，因为运行时资格门尚未作出该决定。

## 4. vNext 算法：顺序信息获取而不是一次性全图选择

### 4.1 信息状态与动作

令：

- `I_t`：raw requirements、checkpoint、producer evidence、partial graph、lineage、历史成本、已有
  feedback 与剩余预算；
- `z`：当前 LLM 动态提出的 failure hypothesis；
- `h`：多个 verifier 可能共同漏检的 blind-spot scenario；
- `q`：一次计算动作，例如展开一个 Behavior、实例化一个 verifier、调查 oracle 或构造 hyperedge；
- `S`：准备执行的 verifier 集合；
- `B_t`：wall time、token、美元、名义 cost 和 continuation round 的资源向量。

对 failure `z`，保留原始高阶检测模型：

```text
D_z(S | I_t)
  = 1 - Σ_h π_zh(I_t) × Π_{f∈S}(1 - p_fz|h,I_t)
```

但集合决策改为最终效用：

```text
Δ(S | I_t)
  = Σ_z P(z | I_t) × L_z × D_z(S | I_t) × R_z(S | I_t)
    - C_execute(S)
    - C_false_feedback(S)
    - C_continuation(S)
    - C_revalidation(S)
    - C_regression(S)
```

其中 `P(z | I_t)` 必须相对 producer 已有证据更新，`R_z` 表示 finding 最终被 Codex 正确且安全修复
的概率。一个能找出问题、却无法被当前环境修复或会诱发 broad repair 的 verifier 可以有负净价值。

对于构图/扩图动作 `q`，定义 Value of Computation：

```text
VOC(q | I_t)
  = E_o[max_a V(I_t ∪ o_q, a)] - max_a V(I_t, a) - C(q)
```

动作 `a` 包括继续扩图、执行 verifier、No-Op、abstain 和返回 feedback。精确 Bellman policy 不现实，
vNext 用分阶段区间近似。

### 4.2 Stage A：ResourcePreflight 与原子 escrow

在任何 LLM gate 前，先以 `(session_id, epoch_id, checkpoint_key)` 原子 claim checkpoint，并为本次
attempt 建立 lease。资源向量为：

```text
B = (wall_s, input_tokens, output_tokens, model_usd, nominal, feedback_rounds)
```

所有未知模型成本使用 timeout/token cap 或历史条件分布的保守上分位；没有可靠上界的动作不可执行，
不能按零处理。

必须保留两类 action-dependent escrow：

- `R_V`：至少一个可能形成合格 evidence 的 verifier 上分位成本；
- `R_P`：若 verifier 可能产生 blocking feedback，继续 Codex、重放原 evidence 和 promotion
  revalidation 的上分位成本。

扩图动作只有满足以下向量约束才可执行：

```text
C_spent + C_upper(q) + R_V + R_P <= B
```

并发 verifier 的 wall-time 预估使用 `max(duration) + overhead`，token、美元和名义 cost 才求和。
当前实现对并发 wall time 求和，需要在 vNext 修正。

### 4.3 Stage B：一次轻量 LLM TaskSketch

不再串行运行 completion LLM、完整 behavior LLM、完整 planner LLM。先用一次受限结构化调用同时输出：

- lifecycle：candidate complete / intermediate / question / explanation / blocked / abstain；
- 少量最高优先级 Behavior 与 Failure hypotheses；
- `unexpanded_risk_mass` 与尚未消歧的 requirement branches；
- producer evidence 已覆盖什么、还缺什么；
- 当前环境可见的 executable oracle/capability；
- repair controllability；
- task embedding 和开放词汇特征，供经验模型校准。

LLM 负责语义表示，但 LLM 自报 confidence 不能直接证明 checkpoint 正确或直接触发 No-Op。

### 4.4 Stage C：Pre-graph VOC Gate

Gate 预测的不是“代码对不对”，而是：

```text
best attainable downstream value
= max over graph/verification policies
  E[final reward delta - total remaining cost]
```

输入仅允许在线可见的 TaskSketch、diff/state、producer evidence、capability、lineage、budget 和历史
stage cost；禁止 task 名、hidden evaluator、`resolved`、hidden tests 或事后 failure label。

对预测区间 `[lower, upper]`：

- `upper <= 0`：`NO_OP_VALUE_DOMINATED`；
- `lower > 0`：进入渐进构图；
- 区间跨 0：若有低成本正 VOC refinement，则继续；否则 `ABSTAIN_UNCERTAIN`；
- TaskSketch 加最小 reserve 都付不起：`RESOURCE_EXHAUSTED`；
- 看见风险但没有任何能形成合格 evidence 的 capability：`ABSTAIN_NO_ORACLE`。

冷启动阶段不允许 semantic gate 自动 No-Op。先 shadow 记录；只有“最低可行总成本已经超过用户定义
的最大可恢复价值”这类确定性 dominance 才能 No-Op。其他未知状态 abstain，或在明确的 research
exploration policy 下运行成本封顶、lineage-diverse 的 seed portfolio。

### 4.5 Stage D：Progressive Graph Construction

维护 partial graph：

```text
G_t = (B_t, Z_t, F_t, H_t, U_t)
```

其中 `U_t` 是未展开风险/歧义质量。候选动作包括：

1. `expand_behavior(b)`：细化当前 Behavior 的 competing failure hypotheses；
2. `instantiate_verifier(z, capability)`：动态实例化一个通用 verifier capability；
3. `investigate_oracle(z)`：判断仓库或 runtime 是否存在 authority；
4. `expand_hyperedge(S, z)`：只为可能进入候选集合的 verifier 组构造共同失效场景；
5. `stop_modeling_and_execute(S)`；
6. `NO_OP` 或 `ABSTAIN`。

每步选择满足 escrow 的最大正 `LCB(VOC)` 动作。以下任一成立即停止建模：

- 当前 `S` 已有正保守净值，继续扩图的 VOC 非正；
- 所有未展开分支的价值上界非正；
- 达到 graph allocation/deadline；
- 下一步会侵占 verifier/promotion reserve；
- 不确定性太宽且没有便宜 refinement，此时必须 abstain。

### 4.6 Stage E：Lazy higher-order graph

高阶图保留，但不再全量预生成：

1. 确定性 lineage signature 只负责提名可能相关的 verifier 组；
2. 只有该组可能进入最优集合时，LLM 才针对当前动态 failure 构造 task-specific blind spot；
3. `π_zh` 和 conditional detection 用 held-out detection matrix 按 lineage、task embedding、modality 和
   oracle type 校准；
4. 数据不足时使用宽区间/ambiguity set，不能退回“默认独立”；
5. 若 held-out 实验中 full high-order 没有稳定优于 pairwise，就删除标题级高阶创新主张。

### 4.7 Stage F：Evidence、continuation 与 promotion

verifier 默认 read-only；需要写测试的 Agent 只在一次性 workspace copy 中工作。输出必须包含：

```text
checkpoint_key
violated behavior / failure hypothesis
authority/source refs
exact reproduction command or executable observation
expected vs actual
lineage
cost and completeness
```

满足 eligibility 且已预留 `R_P` 才能返回 Stop continuation。Codex 自行选择修复。修复后必须：

- 重跑原 finding；
- 重放仍适用的 pre-feedback evidence；
- 分类为 `FIXED_AND_PRESERVED | NOT_FIXED | REGRESSED | UNRESOLVED`；
- 未通过 promotion 时不能把新 checkpoint 写成成功。

## 5. 产品状态机与唯一 runtime authority

### 5.1 状态必须分开

| 状态 | 含义 | 是否证明正确 |
|---|---|---|
| `SKIP_NOT_ELIGIBLE` | 当前不是候选交付 | 否 |
| `NO_OP_VALUE_DOMINATED` | 已校准后，所有下游净值上界均不为正 | 否 |
| `ABSTAIN_UNCERTAIN` | 信息不足且无经济 refinement | 否 |
| `ABSTAIN_NO_ORACLE` | 有风险但没有合格 evidence capability | 否 |
| `RESOURCE_EXHAUSTED` | 可能有用但预算无法完成 | 否 |
| `EXECUTE` | 有正保守净值集合并已预留资源 | 否 |
| `ALLOW_EVIDENCE` | 执行后无合格 failure evidence | 只表示本轮证据未发现问题 |
| `CONTINUE_EVIDENCE` | 有合格证据，继续同一 Codex epoch | 否 |
| `PROMOTED` | finding 修复且旧行为重验未回归 | 对已验证范围成立 |

No-Op、abstain、resource exhausted 和 evidence-backed allow 不能再合并为 `ALLOW`。

### 5.2 多轮状态转移

```text
ACTIVE / AWAITING_USER
        ↓ main Stop
STOP_CLAIMED → RESERVED
        ├─ SKIP_NOT_ELIGIBLE
        ├─ NO_OP_VALUE_DOMINATED
        ├─ ABSTAIN / RESOURCE_EXHAUSTED
        └─ GRAPHING → VERIFYING
                         ├─ ALLOW_EVIDENCE → ACCEPTED
                         ├─ FEEDBACK_PENDING
                         └─ UNRESOLVED

FEEDBACK_PENDING + authenticated GRAFT continuation
    → ACTIVE，同一 epoch

ACCEPTED 或 UNRESOLVED + 新人工 prompt
    → 新 epoch，但 session 总预算不重置

ACTIVE + 人工澄清
    → 同一 epoch
```

### 5.3 P0 runtime authority（2026-08-13 已实现）

当前机器上旧 global runtime 与 v0.5 plugin/runtime 并存；Codex 会并发执行多个来源的 matching Hook。
现有 `claim_event()` 没有 runtime 版本，谁先写 marker 谁获胜。旧 runtime 可能先 claim、因 config v2
失败而 fail-open，并压掉新版。这会使实验结果依赖进程调度。

实现 vNext 或再跑真实实验前必须；当前已完成其中运行时隔离与诊断部分：

1. 实验环境只保留一个 source-pinned v0.5+ runtime；
2. 新增 `RuntimeIdentity(installation_id, distribution, package_version, protocol_version,
   runtime_digest)`；
3. authority 由配置显式 pin，不以竞速选主；非 authority 只能 shadow，不得 claim、写 state 或启动模型；
4. state envelope 保存 schema version 与 writer runtime digest；旧 runtime 不兼容时只能报告；
5. `graft doctor` 枚举 user/repo/plugin/SDK Hook，发现多 authority 或 schema 不兼容即 unhealthy；
6. SDK experiment、plugin product mode 与 repo development hooks 互斥；
7. Stop、SDK adapter 和 manual CLI 统一调用 `CheckpointService`，同一个
   `(session, epoch, checkpoint)` 最多执行一次昂贵验证，并缓存 decision。

当前实现新增 protocol-v2 event/state 目录、`RuntimeIdentity`、显式
`GRAFT_RUNTIME_AUTHORITY` pin、repo→plugin→global 的确定性产品优先级、state writer envelope，
以及 `graft doctor` 的 repo/plugin/global 来源审计。本机旧 global v0.3 的三条 Hook 已通过安全卸载器
移除并保留备份；项目内由 `graft-repo-v1` 获权，其他目录由 `graft-plugin-v1` 获权。

尚未完成的是第 7 项统一 `CheckpointService` 与跨入口昂贵验证缓存，它属于 P1，而不是再次修改
authority 规则。

`installation_id` 不能简单加入 event hash，否则只会让每个副本各运行一次。

## 6. 实现顺序与验收 Gate

### P0 — 实验完整性：先修 authority，不改算法（已通过代码与本机 smoke）

实现：

- `codex/runtime_authority.py`；
- state schema envelope；
- `graft doctor` Hook-source audit；
- 固定 owner 的 event/checkpoint claim；
- 移除本机旧 global v0.3 或明确禁用，只保留 source-pinned runtime。

验收：

- global/plugin/repo 两两与三者并发时 winner 固定；
- v0.3 不得 claim、写 v2 state 或压制 v0.5；
- 同一事件并发 100 次，昂贵 controller 恰好运行一次；
- 真实 Codex smoke 和 fake runner 都通过。

当前证据：混来源选择不依赖启动顺序，legacy state 与 v2 state 物理隔离，非 authority 在
claim/state/model 之前退出；下一次端到端实验必须记录 `graft doctor` JSON。

### P1 — 统一生命周期与 checkpoint service

实现：

- `CheckpointService.handle_stop()`；
- session/epoch 双 ledger；
- feedback nonce/provenance；
- accepted/unresolved 后人工 prompt 新开 epoch；
- manual verify 不能绕过活动 lease 和 Stop budget。

验收：

- clarification 留在原 epoch；
- GRAFT continuation 不进入 user requirements；
- unchanged repeated Stop 命中缓存；
- changed workspace 产生新 attempt；
- session 总预算单调且不能由新 prompt 刷新。

### P2 — ResourcePreflight、deadline 与 escrow

实现：

- `ResourceVector`、`BudgetLedger`、`BudgetLease`；
- graph stage absolute deadline 和即时 cost callback；
- verifier/promotion reserve；
- 并发 wall time 用 max，token/美元用 sum；
- resource exit 与 semantic decision 分离。

验收：

- graph 永远不能消费 `R_V/R_P`；
- timeout/error/crash 也计费并可恢复 lease；
- `PROCEED` 时至少能执行一个预留 verifier；
- worst-case stage 上限总和超过配置预算时配置直接不合法。

### P3 — TaskSketch + pre-graph shadow mode

实现：

- 把 completion 与轻量 TaskSketch 合为一个结构化调用；
- 记录 opportunity interval、capability、unexpanded risk 与真实 downstream 结果；
- semantic No-Op 只 shadow，不影响在线 Codex；
- promotion pending 永远绕过 semantic No-Op。

验收：

- 至少 50 个自然多轮 checkpoint 的 lifecycle precision、recall、P95 成本；
- 正确与缺陷 checkpoint 均存在；
- shadow false-No-Op、abstain 和校准曲线完整；
- test/OOD split 按 task/repository 分组，不能按 checkpoint 随机泄漏。

### P4 — Progressive graph + lazy high-order

实现：

- partial graph 与扩展动作接口；
- LCB(VOC) expansion policy；
- lazy lineage nomination 和 task-specific hyperedge；
- online cost quantile 与 ambiguity interval；
- research exploration propensity logging。

验收：

- 在同一冻结 checkpoint pool 上，相比 full graph v1 显著降低 unnecessary graph rate 和 modeling cost；
- defective checkpoint 的 false-No-Op 不超过预注册上限；
- 相同 total cost 与 set-FPR 下比较 independent、pairwise、full high-order、progressive high-order；
- 没有 held-out 高阶增益就不升级为核心主张。

### P5 — promotion 与 shared-prefix 因果实验

```text
同一个 first-Stop checkpoint
├── No feedback
├── strongest-single feedback
├── pairwise-selected feedback
├── GRAFT vNext feedback
└── run-all / oracle upper bound（仅在可行时）
```

验收：

- 每条 arm 使用同一个精确 source-bound prefix；
- 同时包含错误 checkpoint 的修复机会和正确 checkpoint 的退化风险；
- 报告 `delta_feedback`、repair、regression、abstain、timeout、wall/token/$ 与净效用；
- hidden evaluator 始终最后运行，不能进入任何在线选择输入。

## 7. WSDM 2027：论文问题与证据路线

### 7.1 最稳健的研究问题

不要把论文写成“一个 Codex 插件”或“再加一个 reviewer”。应写成：

> 在强 Agent 已拥有自身轨迹证据、多个候选 verifier 具有相关错误且验证计算昂贵时，如何把验证建模
> 为一个带高阶共同失效和资源 escrow 的顺序信息检索问题，只购买可能改变最终停止/修复决策的证据？

这与 WSDM 的检索、排序、foundation-model evaluation、Web agents 和 agentic systems 方向对应。
Codex 是 deployment case；算法与现象必须在可统计的数据上验证。

### 7.2 截止前主数据路线

#### 主线：AgentRewardBench，条件 GO

该数据有 1,302 条 Web Agent trajectory、专家 success/side-effect 等标签和 15 个逐样本 judge 输出，
可以形成真实 verifier × trajectory matrix，测 independent、pairwise 与 lineage-aware high-order
selection。首轮使用 cardinality/token budget；美元只作可用 API judge 子集的补充，因为本地模型成本
为零且没有 wall time。

它只能验证 GRAFT 的 correlated-verifier selection 层，不能证明 Codex repair、source-bound feedback
或完整 coding loop。数据页给出 fair-use Terms of Use 而非标准 SPDX license，且要求衍生物保留
条款；本仓库因此只保存 adapter、ID、revision、hash 与聚合结果，不重新分发原始数据。

固定：

```text
GitHub: f838338886d723d40b586309465a38277803d9e6
HF:     b6d17e646009d6cb63d5dd7be78807b680693f61
```

#### Coding transfer：SWE-Review-Bench，8 月 17 日前不作为主线

该数据提供 1,384 个候选 PR、500 个唯一 issue 和 executable `resolved` label，但没有任何逐 reviewer
output、lineage 或 cost。三个所谓 split 是同一批 issue 的三个 generator quality tier，不是独立
train/dev/test；389 个 issue 同时出现在三个 tier。

因此它目前只能作为未来 coding checkpoint corpus。`resolved`、`test_patch`、`FAIL_TO_PASS` 和
`PASS_TO_PASS` 必须 evaluator-only。若要成为 GRAFT 数据，需按唯一 issue 分组抽取 60–100 个
checkpoint，运行至少 5 个跨 lineage verifier，并记录 per-instance verdict、diagnosis、cost 和一部分
repair delta；这不应阻塞本周主实验。

#### 系统案例：Codex + GRAFT

现有 Terminal-Bench 结果仅作为机制、负结果和 verification-tax case study：

- exact first-Stop replay；
- dynamic graph 与 lineage 确实改变选择；
- 一次真实反馈修复机制；
- feedback-induced harm、零增益和高成本反例；
- 不得据此声称总体提升。

### 7.3 强基线

同 cost、同 set-FPR 下至少比较：

1. No verification / No-Op；
2. random cost-matched；
3. best single（只在 calibration set 选择）；
4. top-k individual recall/ROI；
5. independent Bernoulli coverage；
6. pairwise correlation penalty / mRMR；
7. Greedy Mutual Information；
8. ThriftLLM-style cost-aware selector；
9. GRAFT without lineage；
10. GRAFT pairwise；
11. GRAFT high-order；
12. run-all cost upper bound；
13. exhaustive best feasible subset，仅作不可部署 oracle upper bound。

Coding feedback pilot另比较 no feedback、decision-only、same-model self-review、single-turn diff-only、
diff+retrieved context、strongest single agentic reviewer、GRAFT-selected 和 run-all/oracle upper bound。

### 7.4 指标

Selection 层：

- failed-trajectory detection recall / joint miss；
- false reject/false feedback 与 set-FPR；
- portfolio precision；
- calibration error；
- cardinality、token、美元预算下的 utility/Pareto frontier；
- independent/pairwise 对 observed co-miss 的误估；
- high-order 相对 pairwise 的 held-out 增益；
- missing/parse failure/abstain sensitivity。

Codex 闭环：

- first-Stop 与 final hidden reward；
- `delta_feedback`、successful repair 和 feedback-induced regression；
- trigger、No-Op、abstain、resource exhaustion 和 promotion rate；
- TaskSketch、graph、verifier、continuation 分阶段 wall/token/$；
- 总净效用与 reward per cost。

所有置信区间按 trajectory/task 配对 bootstrap；coding 数据按 issue、最好按 repository 分组。

## 8. 2026-08-13 至 08-24 的现实日程

### 8 月 13 日

- 冻结 Original、value-aware-v1、全部负/零结果和本计划；
- 处理 P0：实验仅保留一个 source-pinned runtime，完成 `graft doctor` 的最小 authority audit；
- 下载 AgentRewardBench judgments 的固定 revision，不下载不需要的完整截图；
- 构建 verifier × trajectory matrix，完成数量、缺失、parser、token 与 label leakage 审计；
- 冻结 dev/test 和 lineage signature。

### 8 月 14 日

- 在 dev 上实现/运行 best-single、random、independent、pairwise、Greedy MI、run-all 与 exhaustive
  feasible upper bound；
- 测 observed co-miss 与 independence/pairwise calibration gap；
- 只对 lineage 提名的稀疏组估计高阶项，196 个 dev 样本不足以自由估计全部三元组合；
- 冻结 budget、set-FPR 和选择器参数。

### 8 月 15 日

- 一次性打开 frozen test；
- 运行 paired bootstrap、benchmark/producer/model strata、missingness sensitivity；
- 生成第一张真实主图和主表；
- 同时只做 P1/P2 的 correctness tests，不启动新的昂贵 Codex treatment。

### 8 月 16 日：Abstract Gate

**Method GO** 仅当：

- co-miss 显著偏离 independent/pairwise；
- high-order GRAFT 在相同 token/cardinality cost、相同 set-FPR 下优于最强可部署 baseline；
- paired 95% CI 下界大于 0；
- 结果至少在两个预注册 strata 方向一致；
- 无 label leakage，且已有冻结主图、主表和 artifact hash。

**Measurement/Findings GO** 当：

- 共同失效、verification tax 或 pairwise 失准形成稳定大样本结果；
- 但新 selector 没有优于强 baseline。

此时标题和摘要只主张测量/负结果，不写 GRAFT improves Codex。

**No-Go** 当：

- 仍只有插件机制、单任务案例、post-hoc 结果或删失 benchmark；
- verifier matrix/test split/leakage audit 不完整；
- 结果只在调参所见数据成立。

### 8 月 17 日

- 提交只承诺已观察结果的摘要；WSDM 官方 abstract deadline 为 8 月 17 日 AoE；
- coding 闭环只能称 controlled deployment case study，除非此前已经产生独立统计证据。

### 8 月 18–20 日

- 补齐全部强基线、消融、leave-one-benchmark/agent-out 与稳健性；
- 完成 P0–P2 但不让未校准 semantic gate 在线生效；
- 若资源允许，在 SWE-Review-Bench 只做 10 个 smoke，再决定是否扩到 40–60 个 controlled pilot；
- 8 月 20 日冻结所有 test 结果、主参数和表格。

### 8 月 21–24 日

- 完成统计、9 页正文、limitations、ethics 和 GenAI disclosure；
- 将插件/Terminal-Bench 降为系统实现与负结果 case，不用工程完成度替代效果；
- 8 月 22 日做 reviewer-style claim audit；
- 8 月 24 日提交。若核心表不成立，改为 Findings/measurement 叙事或停止 full submission。

## 9. 变更控制

- 冻结原稿和 `docs/method-original-frozen.md` 永远优先；
- 本文件只允许新增带日期、证据和理由的 amendment，不能无记录覆盖结论；
- 每个策略与结果记录 method version、commit、config hash、dataset revision 和 runtime digest；
- confirmed、supported hypothesis、post-hoc、censored 和 untested 必须分开；
- 任何按 task 名、语言、框架、隐藏答案或看过 test label 后添加的规则都会使实验失效；
- 未通过 Gate 的组件不得成为默认产品策略或论文既成贡献；
- 从现在到 abstract Gate，不能产生冻结数据表、置信区间或强基线对照的 UI、App Server、MCP、
  dashboard 和深度插件功能全部推迟。

## 10. 冻结的一句话定义

> GRAFT vNext 是一个位于 Codex 候选交付边界的、带资源 escrow 的顺序证据检索策略：LLM 动态理解
> 未见任务和当前失败空间，系统相对 producer 已有证据估计继续计算的 VOC，只渐进展开可能改变
> 停止/修复决策的图分支，并在真正考虑 verifier 组合时才校准其高阶共同失效；若不能形成正保守净值
> 和可安全修复的可执行证据，它选择 No-Op 或诚实 abstain，而不是为了“多验证”而验证。

## 11. 2026-08-13 AgentRewardBench 首次冻结结果

冻结主实验已经打开一次 test，结果为 **Method NO-GO**，不能改阈值后覆盖：在预注册
`set-FPR <= 0.10` 和 OR 检测规则下，15 个 judge 没有一个在 development 可行，因此 budget 1–4
全部为 `no_feasible_portfolio`，无法进行 GRAFT high-order 与 Greedy MI 的主比较。

事后诊断只用于定位原因：

- independence 对 2/3/4 元组合的 test recall MAE 分别为 0.07875/0.07352/0.06240；pairwise 降至
  0.01262/0.01111/0.01048，说明共同漏检不能按独立处理；
- 当前 high-order 在 3/4 元组合上的 test MAE 为 0.01218/0.01226，反而劣于 pairwise；
- 经验 residual 提名了 87/105 条 pair edge，使 360/455 个 triple 和 1000/1365 个 quadruple 被
  修正；图过密，并在 development 上产生明显的同样本拟合；
- pairwise 相对 independence 的优势只出现在 12 个 benchmark×cardinality strata 中的 6 个，说明
  依赖结构随任务域变化，不能用一个全局图迁移；
- development 只有 59 个成功样本，point-estimate set-FPR 无法可靠迁移到 test。

因此下一版不能继续调整同一测试集上的阈值。新的协议只能把 lineage 当候选依赖的来源提名，在独立
fold 上估计经验相关，并按任务域/难度校准；可行性必须采用不确定性上界。LLM review 仍然是动态
verifier，但其输出是 finding proposal，只有 executable reproduction/authority promotion 才能阻断
Codex。完整结果、artifact hash 与复现命令见
`experiments/agent_reward_bench/RESULTS.md`。

五折 task-group cross-fit 的事后稳健性结果进一步限定结论：pairwise 相对 independence 在 15 个有效
比较中胜 14 次，超过冻结的 12/15 measurement 门，因而“不能假设 verifier 独立”具有稳健证据。
原 cross-fit 协议却误把 cardinality=2 计入 high-order 的 12/15 门；该规模上 high-order 按定义等于
pairwise，所以门槛理论不可达，必须作废而不能据此宣布 high-order 失败。可描述结果是 high-order 在
适用的 10 个比较中胜 7 次，3/4 元平均 MAE 略低，但存在单折明显反转，属于混合证据。另有 4/5 个
training fold 在 10% set-FPR 下无可行 OR portfolio；唯一可行折从 development 0.0986 漂移到 held-out
0.1549。这强化了“不使用 point-estimate FPR 上线”的结论。

## 12. 2026-08-13 首个可执行 Coding Verifier Matrix

FeatureBench Metaflow 的 source-matched smoke 已完成。Native Codex candidate 在官方 evaluator 得分
`1.0`；GRAFT 在完全不反馈、不可修改 producer 的 shadow 模式下动态构造 9 个 Behavior、18 个
Failure Mode、7 个 verifier 与 6 个 blind-spot scenario。三个 verifier 给出了由真实命令事件支持的
blocking reproduction，另有三个 verifier timeout。producer 前后 tree hash 相同。

这个结果只证明“未见任务 → 动态假设 → 动态 verifier → 可执行 evidence matrix”的数据管线成立。
它不证明最终效用：官方 label 与多个 public-requirement reproduction 不一致，需要独立 adjudication；
默认 budget `4.0` 的选择包含一个成功 adversarial verifier 和一个 timeout verifier；移除全部 blind-spot
scenario 后选择完全相同，所以本样本上图没有正向因果作用。相同 candidate 的两次 graph sample 的
Behavior/Failure 数量也不同，暴露 graph stability 问题。

此外，Harbor 的 producer trajectory 统计没有包含 nested shadow Codex 调用，job dollar cost 不能当作
GRAFT 总成本。完整 token lower bound、artifact hash、finding 强弱边界与后续 same-thread promotion
协议见 `experiments/coding_verifier_matrix/SMOKE-02.md`。下一步先做独立 finding adjudication 与严格标注
为 post-hoc 的 shared-prefix repair/promotion 机制实验，再扩展多任务矩阵；不得从这个单样本声称 GRAFT
优于 Native Codex 或高阶图有效。
