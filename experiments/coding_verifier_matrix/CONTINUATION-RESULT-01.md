# Same-thread continuation result 01

Date: 2026-08-13

Status: **mechanism partially validated; formal promotion unresolved**

This is a post-hoc mechanism test on the frozen Metaflow FeatureBench smoke task. It
must not be interpreted as an estimate of GRAFT's population-level value. The native
candidate already received official reward `1.0`, so the official reward has no room
to measure an improvement on this task.

## Frozen inputs

- Task: `netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1`
- Source matrix SHA-256:
  `4881015a048e4f36061f55447b1bd91e109315c5f91497f78a926ae3dee3a509`
- Candidate archive SHA-256:
  `c132a465c8b5898e146bbca6979f93239ff043b386a90b4a6919064ab8b86eda`
- Candidate tree:
  `b6dddee3e6906c475ace17c7018ed007c058d59dd0ca67605ffe24eec44c5e5f`
- Feedback checkpoint:
  `eac964b555bbe7cdc9da66348348bcde4475838ddee4725cd0140a6c9f4c2207`
- Original Codex thread:
  `019ff8d2-366e-72a2-81f4-2a7c3ecb50ed`
- Original session SHA-256:
  `4fb55edbc0f193a5395697486b8861f50e849207da6067e5dc0dc203dd7d8baa`
- Frozen GRAFT source commit:
  `2fce6cc29a0eb65ca349910614709374b790d6f4`
- Feedback SHA-256:
  `6447151126d4eb001b72ae439ddb0602d6ff407ab9903b5f6e64f3be2cda82cf`

The frozen Original selector chose `adversarial-stub-test` and
`runtime-annotation-function` under budget 4. Only the former had eligible,
reproducible blocking evidence, so only that evidence was returned to Codex. The
feedback contained three observed failures: loss of the positional-only `/` marker,
incorrect serialization of a singleton tuple default, and invalid generated source
when a docstring contains triple quotes. No hidden evaluator output was visible to
GRAFT, Codex, or the promotion verifier.

## Observed execution

The continuation emitted the same thread id as the frozen producer thread. It changed
only the candidate implementation and reported:

- all three frozen reproductions passed;
- 31 restored focused regression tests passed;
- 162 generated `.pyi` files parsed successfully;
- module compilation and `git diff --check` passed.

The repaired state was frozen as:

- repaired tree:
  `642a7849410aa608cb5851d539089d71d931200655787119963601dda5c624eb`
- repaired checkpoint:
  `35a90cd85aaffe7780a32ad50602bd20da6d1f7a19e6ab327b3d687b9c45bbda`
- repaired archive SHA-256:
  `2c6be9704dc4b48d58e1b7f811d9a64dfc97f48d32920b99e3e0015b8c22130e`
- continued session SHA-256:
  `35771956810633ad353c92b438b6b957983545bf01e4ed9d0505f79376d03c96`

The independent promotion reviewer returned `PASS` and
`fixed_and_preserved`. It reported requirement-derived runtime checks covering the
three target failure modes, overload and alias preservation, reset behavior, and
compilation of all 162 generated stub files. The producer worktree remained stable.

The official evaluator then returned reward `1.0` with zero trial exceptions. The
pre-feedback candidate also had reward `1.0`; therefore official reward delta is
exactly zero.

## Why formal promotion is unresolved

The promotion reviewer ran its checks through shell heredocs, but represented the
commands in its structured verdict as rewritten argv arrays. Those arrays did not
exactly match a Codex command event. GRAFT's evidence guard therefore set
`executed_evidence=false` and the overall promotion status remained `unresolved`.

This rejection is correct. Weakening exact command-event matching after seeing a
desired PASS would turn model testimony into an oracle. The general protocol was
instead amended to:

1. require temporary checks to be created with a file-edit tool;
2. require each check to be run as one standalone command, without heredocs,
   pipelines, redirects, or chained shell programs; and
3. mechanically downgrade a claimed `fixed_and_preserved` outcome to `unresolved`
   whenever no executed evidence matches.

The amendment contains no task- or repository-specific verification logic.

## Cost and scope

- Harbor wall time: 9 minutes 50 seconds.
- Continuation usage: 1,805,536 input, 1,669,888 cached input, and 15,086 output
  tokens.
- Promotion reviewer usage: 189,968 input, 158,208 cached input, and 3,248 output
  tokens.
- A complete dollar cost is unavailable from this harness and must not be inferred
  from the producer-only Harbor accounting.

This run establishes that selected executable feedback can be delivered to the same
Codex thread and can induce a source-stable repair without reducing the official
score. It does **not** establish that the graph caused the repair, that selection
beats a checklist or run-all policy, or that GRAFT improves benchmark reward. The
next valid experiment is a prospectively frozen, unseen task using the amended
evidence protocol, followed by multi-task paired evaluation with a benchmark that
has non-saturated outcomes.
