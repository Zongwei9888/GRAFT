# GRAFT Original — Frozen Method Contract

Status: **frozen**

Authority: `GRAFT_WSDM_中文稿_Introduction_to_Method_公式与引用修正版.docx`
SHA-256: `2d8c31307f31d335891226288f6d75f107f81026978e8d4dec7ac3ddaa6651a9`

This file is an implementation contract, not a replacement paper. If this summary and the DOCX
conflict, the DOCX is authoritative.

## Non-negotiable method identity

GRAFT is an external feedback controller for a free Agent Loop. At a natural verification
checkpoint it:

1. uses structured model calls over the raw task, observable environment, candidate result and
   state diff to construct task-specific Behaviors and Failure Modes;
2. retrieves or instantiates a heterogeneous verifier pool containing deterministic tools, model
   Judges, Test Agents and Agentic Reviewers;
3. records what each verifier can observe and its full lineage: model, session, prompt, context,
   modality, test author and oracle;
4. represents higher-order shared blind spots explicitly rather than treating different reviewer
   identities as independent evidence;
5. selects a verifier set under a feedback budget to maximize risk-weighted probability that at
   least one verifier detects each relevant failure;
6. binds every result to the requirements and source/environment state that produced it;
7. returns reproducible evidence to the original Agent Loop without prescribing a repair plan;
8. allows stop only when residual risk and marginal feedback value are sufficiently low.

## No task-specific hardcoding

Production GRAFT must not encode a closed list of programming languages, frameworks, repository
layouts, game mechanics, APIs, failure instances, or verifier combinations. A registry contains
general verifier capabilities and execution policies. Structured LLM calls instantiate Behaviors,
Failure Modes and verifier objectives for the current task.

Deterministic tools remain valid evidence anchors when they are discovered by the current Agentic
Verifier, supplied by the repository, or explicitly configured by the user. They are not a
substitute for semantic task modeling.

The following are protocols, not task hardcoding:

- JSON schemas and versioned prompts;
- source-state hashing and evidence records;
- sandbox, trust and timeout policies;
- generic verifier families;
- budget and stop thresholds.

## Frozen separation of concerns

- The producer Agent decides how to solve and repair the task.
- LLM task modelers dynamically identify what must hold and how it may fail.
- LLM/Agentic verifiers dynamically inspect, execute and test the current task.
- Deterministic tools anchor reproducible observations.
- GRAFT models provenance and shared failure, selects evidence sources, and gates stopping.

The hand-authored empirical fixtures under `experiments/terminal_bench/` are retained only as a
historical negative experiment. They are not the GRAFT Original method and must not be loaded by
the default product path.
