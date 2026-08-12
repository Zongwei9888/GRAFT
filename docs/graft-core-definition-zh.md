# GRAFT 核心定义、硬编码边界与自然触发策略

状态：**研究定义冻结；value-aware v1 已实现但未通过效果 Gate；vNext 见优化计划**
日期：2026-08-13

本文件保存当前对 GRAFT 的统一解释，防止论文叙事、产品实现和实验修正继续漂移。
原始方法的最高权威仍是
`GRAFT_WSDM_中文稿_Introduction_to_Method_公式与引用修正版.docx`；
`docs/method-original-frozen.md` 是其冻结实现契约。本文件不改写原稿，而是说明原稿中的
“自然验证检查点”在 Codex 产品中的正确含义，并如实区分已经实现与尚未实现的部分。

## 一句话定义

GRAFT 是自由 Coding Agent 外部的反馈选择与停止控制器：它在 Agent 形成准备交付的候选状态时，
用 LLM 动态构造当前任务的行为与失败空间，选择一组不易共同漏检、且相对 Agent 已有证据仍有
正边际净价值的验证器；只有可复现、状态绑定的失败证据才会返回原 Agent 继续修复。

GRAFT 不负责规划如何写代码，也不替代 Codex 的搜索、编辑、测试和修复循环。

## GRAFT 究竟做什么

对一个任务状态 `x`，GRAFT 的完整职责是：

1. 保存当前 task epoch 的原始用户要求，而不是只相信 Agent 的最终总结；
2. 冻结准备交付的源码、环境和需求状态，得到不可混用的 checkpoint；
3. 通过结构化 LLM 调用，为当前任务生成 Behaviors、Failure Modes、可观察量和语义歧义分支；
4. 从通用能力库动态实例化候选验证器，包括 LLM Judge、Agentic Reviewer、Test Agent、
   repository-owned tests、静态工具、差分执行和环境检查；
5. 记录每个验证器的可见信息和 lineage，例如模型、prompt、session、modality、测试作者与 oracle；
6. 用高阶共同失效场景建模多个验证器可能一起漏检的原因；
7. 在预算内检索新增信息最有价值的验证器集合；
8. 在隔离环境执行验证器，将结果绑定到同一个 checkpoint；
9. 只有 authoritative runtime、未修改 baseline oracle，或从原始要求直接导出的可执行
   counterexample，才能形成阻塞反馈；
10. 将可复现失败返回同一个 Codex task epoch，让 Codex 自己决定如何修复；否则允许交付或明确
    标为 unresolved。

## LLM 在哪里参与

GRAFT 不是一套纯硬编码检查器。LLM 至少参与三层：

| 层 | LLM 的职责 | 不允许做的事 |
|---|---|---|
| Task modeler | 从当前任务生成 Behaviors、Failure Modes、歧义假设和可观察量 | 凭空增加用户没有要求的合同 |
| Verifier planner | 将通用验证能力实例化为当前任务的 verifier，并估计检测能力与共同盲点 | 查看隐藏 benchmark 标签后再选择 |
| Model/Agentic verifier | 阅读、执行、生成针对当前状态的探针或测试 | 仅凭主观怀疑产生阻塞反馈 |

确定性测试、编译器、类型检查器和 runtime probe 仍然重要，但它们承担的是证据锚定，而不是用
固定命令表替代任务理解。它们可以来自仓库、用户显式配置，或由当前 Agentic Verifier 根据可见
项目证据动态发现和调用。

## 什么是硬编码，什么不是

### 生产路径不允许硬编码的内容

- 编程语言、框架、目录布局或游戏类型的封闭列表；
- benchmark task 名称、隐藏测试、已知答案或失败实例；
- `Python → pytest`、`Rust → cargo test` 一类固定任务路由作为语义验证主体；
- 为某个任务手写的 Behavior、Failure Mode、verifier 组合或通过条件；
- 看见某个文件名或关键词就断言具体业务合同。

### 必须固定的通用协议

- JSON schema、状态哈希、checkpoint 与 evidence record；
- sandbox、网络、trust、timeout 和 source-binding 规则；
- 通用 verifier family 与能力描述；
- 证据资格规则、预算、停止条件与重复反馈抑制；
- 高阶共同失效目标函数和选择算法。

这些是算法与安全协议，不是任务硬编码。一个系统不可能在完全没有固定协议的情况下运行；关键
边界是固定代码不能预先写入当前任务的语义答案。

0.5 之前出现过语言发现、固定命令 checklist、手写 failure rows 和 exact fixture selector。
它们是历史工程脚手架，已经从默认生产路径移除，只能保留为负面实验材料，不能代表 GRAFT 方法。

## 核心算法

对失败模式 `z`、候选验证器集合 `S` 和共同盲点场景 `h`，冻结版本计算：

```text
P(miss z | S) = Σ_h w_h Π_{f∈S}(1 - p(f detects z | h))
U_detect(S)    = Σ_z risk(z) · (1 - P(miss z | S))
```

这部分的核心思想不是“多找几个 reviewer”，而是不同 reviewer 即使身份不同，也可能因为共享模型、
prompt、任务解释、测试作者或 oracle 而一起漏检。选择器应优先检索来源和观测方式真正互补的证据。

冻结 Original 实现以 `U_detect` 的边际增益/名义成本做贪心选择，并与最佳单个 verifier 比较。
opt-in 的 `graft-value-aware-v1` 已实现以下净值近似并显式比较 No-Op：

```text
ΔV(f | S, E_agent)
  = P(find actionable failure)
    × P(agent repairs correctly | finding)
    × value(successful repair)
    - wall/time/token cost
    - expected repair-regression cost
```

其中 `E_agent` 是 Codex 在当前 task epoch 已经执行过的语义化测试和调查证据。如果最好的
`ΔV <= 0`，GRAFT 不应启动昂贵 verifier。当前 v1 的根本限制是：它在完整 Behavior/Failure 图与
verifier plan 已经生成之后才比较 No-Op，因此无法收回昂贵的构图沉没成本；其概率与成本也尚未在
held-out 数据上校准。`docs/graft-optimization-plan-zh.md` 冻结了下一步的顺序 VOC 设计。

## 在 Codex 中何时自然运行

Codex 的 `Stop` 是正确的技术挂载点，但不是充分的语义触发条件。官方 Hook 协议中的 `Stop`
表示一个 turn 准备停止；它不是整个多轮用户任务已经完成的保证。`SessionEnd` 又太晚，而且不能
继续 Agent。因此正确设计分为“持续观察”和“条件验证”两层：

```text
UserPromptSubmit → 建立/更新 task epoch 与原始要求
PostToolUse      → 记录 Codex 已有测试、命令、结果和修改
Codex 自由工作
Stop            → 轻量 eligibility + net-value gate
                    ├─ 无代码变化/解释/提问/中间状态 → No-Op
                    ├─ 可交付候选，但新增验证净价值 ≤ 0 → No-Op
                    └─ 可交付候选，且新增验证净价值 > 0 → GRAFT 验证
可复现问题      → continuation 回同一 task epoch
SessionEnd      → 只做归档、成本统计和离线校准
```

因此最准确的表述是：

> GRAFT 自然运行在“Codex 准备交付一个已经形成的候选结果”这一 Stop 边界，而不是每个 Stop，
> 也不是整个 session 结束时。

completion 判断可以由便宜的结构化 LLM gate 完成。它只判断当前回复属于候选交付、中间状态、
提问、解释还是阻塞，不把 Agent 的“我完成了”当成正确性证据。其输入应包括 task epoch、最后回复、
workspace diff 和 GRAFT 自己记录的工具语义 ledger，而不依赖不稳定的私有 transcript 格式。

## 当前实现与核心定义的偏差

当前代码有两条不同路径，不能混写：

- `graft-original` 的 checkpoint policy 仍只判断 baseline、workspace change 与 checkpoint 去重；它
  显式丢弃 `last_assistant_message`，真实语义是 `workspace_changed_at_stop_boundary`，不是真正的
  completion gate；
- opt-in 的 `graft-value-aware-v1` 已在 changed Stop 后运行结构化 LLM completion gate，并已把
  `PostToolUse` 的有界、脱敏语义 evidence summary 注入 modeler/planner；它也实现了 No-Op、
  task-epoch cost ledger 与 promotion 状态。

v1 仍有两项关键偏差：完整构图发生在净值决策之前；No-Op、abstain、resource exhaustion 与
evidence-backed allow 的产品状态还不够正交。混合 global/plugin/repo Hook 的 runtime authority 已在
2026-08-13 通过 protocol-v2 隔离、确定性 authority 与 doctor 审计修复；统一 CheckpointService
仍未实现。
所以 v1 是已实现但未校准、且不应默认启用的研究 baseline，不能写成已经修复了 GRAFT 的反效果。

## 当前实证结论

- 历史任务中存在一次已确认的 feedback-induced semantic harm；
- 两个使用精确 pre-feedback checkpoint replay 的新因果实验中，GRAFT 都没有改变最终得分，
  但分别增加约 6m38s 和 6m47s 的 Stop 成本；
- 高阶共同失效图能识别同一 Codex 来源和缺少外部 oracle，但当前选择目标仍会为很小的正检测概率
  花费预算；
- 因此现有实现是失败的研究原型，尚无证据证明优于 Native Codex；
- checkpoint replay、lineage 和 evidence provenance 是测量基础设施价值，不等于方法效果成立。

## 不变的研究命题

GRAFT 不是要验证所有可能任务，也不是为每个领域预写 oracle。其待验证命题是：

> 对一个从未见过的 Agent checkpoint，LLM 能否基于当前任务动态提出失败空间，并在通用验证能力中
> 检索少量、低共同漏检且相对 Agent 已有证据具有正边际净价值的 verifier，从而以低于 run-all 的
> 成本提高最终任务效用。

如果预注册、多任务、多随机种子实验不能支持这个命题，就应终止或重新定义 GRAFT，而不是继续通过
增加模块改变叙事。
