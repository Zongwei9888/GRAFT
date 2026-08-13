# Coding verifier matrix protocol amendment 01

Status: **frozen before any producer, graph builder, model verifier, or official evaluator result**

Frozen date: 2026-08-13

## Reason

The first hash-ordered `lv1` task, `huggingface__transformers.e2e8dbed.
test_processing_wav2vec2.4f660c78.lv1`, declares an NVIDIA GPU reservation in its public Compose
environment. The local Docker runtime has no NVIDIA device provider. Its Oracle trial was cancelled
during environment preparation and produced no model trajectory, verifier output, reward, or
official evaluator result.

This row is retained as `infrastructure_cancelled_gpu_unavailable`; it is not a task failure and
will not enter accuracy, cost, or selection estimates. The frozen Wav2Vec2 job configuration remains
unchanged for auditability.

## Prospective resource rule

For this local one-task smoke, a task is eligible only when all hardware reservations declared by
its public environment can be satisfied by the runtime. Hardware eligibility is checked before
reading solution or evaluator contents and before running any producer or model verifier.

The next task under the already-frozen SHA-256 ordering that is both `lv1` and locally satisfiable is:

- task: `netflix__metaflow.b390a8d4.test_stub_generator.7bf08c98.lv1`;
- public instruction SHA-256:
  `dd90ed450be01f947d1dd44ad362794903f97c822f359fdf0142dd17bc12e638`;
- task metadata SHA-256:
  `ef631c9e1bfb04044903fef3ab357eda8767242e0e5de7199d4d22f7feecdb89`;
- declared resources: 2 CPUs, 8 GiB memory, 15 GiB storage, no GPU reservation.

No task solution, evaluator test, producer output, graph, or verifier result was inspected when this
amendment and fallback were selected.
