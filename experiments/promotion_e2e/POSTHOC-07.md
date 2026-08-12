# Post-hoc diagnostic 07: writable same-thread repair and promotion

After fixing resume sandbox propagation, this diagnostic reuses the already persisted formal
`continue_with_evidence` decision from `original-path-result.json`. It does not rerun verifier
discovery or manufacture new feedback. It first requires the live original workspace to match the
decision's checkpoint, then resumes the original producer thread with the exact saved reason.

The resulting checkpoint is evaluated on the original 23 cases and the two GRAFT-discovered
counterexamples. A fresh dynamic promotion graph is then required to select and execute the
mandatory revalidation verifier under the corrected selector. The result remains post-hoc because
several integration defects were repaired after observing the original pilot.

