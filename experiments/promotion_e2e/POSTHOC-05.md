# Post-hoc diagnostic 05: resume at the session-bound workspace

Codex resume retains the producer thread's original writable sandbox root. Restoring the archived
checkpoint under a different temporary directory let the continued agent inspect the candidate but
made its patch fail as read-only. A faithful lifecycle diagnostic must restore the exact absolute
workspace path captured in the original GRAFT snapshot.

`resume_at_original_workspace.py` therefore:

1. refuses to run if the original path already exists;
2. restores the archived checkpoint at that exact path and verifies its checkpoint hash;
3. reruns current GRAFT normally and continues only on `continue_with_evidence`;
4. resumes the original thread, checks the returned ID and a changed source checkpoint;
5. evaluates the original 23 cases plus the two dynamically discovered counterexamples;
6. dynamically builds a promotion graph and executes the mandatory revalidation verifier.

The restored workspace is kept until the diagnostic artifacts are written. This is a post-hoc
integration diagnostic, not a prospective effectiveness result.

