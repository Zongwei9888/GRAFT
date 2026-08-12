# Post-hoc diagnostic 03: single-element shell payload representation

After the standalone-reproduction prompt correction, the verifier did execute and report two
minimal reproductions, but encoded each exact shell payload as a single-element `command` array,
for example `["python3 -c '...'"]`. The evidence schema describes this field as an array and permits
a command or argv, while the matcher previously interpreted every array as tokenized argv. It
therefore treated the full payload as an executable filename containing spaces.

The generic matcher now parses a command array as shell text only when it contains exactly one
whitespace-containing element. Normal multi-element argv is unchanged, and compound shell payloads
remain ineligible to prove a separately reported inner command. Regression tests cover all three
cases. This remains a post-hoc engineering diagnostic, not a prospective result.
