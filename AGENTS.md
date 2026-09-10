# Repository Agent Instructions

## Durable memory

These rules apply to every repository task, whether or not a task-specific prompt repeats them.

1. Before substantive work, open `memory/ledger.yaml`. It is the canonical memory entry point and dynamic manifest. Follow its protocol and read only the registered memory files relevant to the task, target paths, notation, prior issues, and failed approaches.
2. Treat memory as fallible prior context. Recheck claims that affect decisions against current task inputs, repository contents, and verification results; current verified evidence wins. Correct stale or superseded memory explicitly.
3. After substantive verification and before the final report, update memory only for durable, novel information. If neither new durable information nor useful database maintenance exists, leave memory unchanged and report an explicit no-op.
4. Agents own the organization of `memory/`. During every memory write, assess whether the current files remain efficient for agent retrieval. Create, split, merge, rename, consolidate, or retire managed memory files when that improves retrieval or maintenance. Do not impose a permanent taxonomy, wait for a monolith to become unusable, or create files per run, conversation, or observation.
5. Keep `memory/ledger.yaml` compact and authoritative as the registry and protocol. Register every managed memory file and record structural changes in its topology history. Before writing, reread the ledger and every affected memory file; merge concurrent updates and stop rather than clobbering changes that cannot be reconciled confidently.
6. Preserve distinct insights and correction history through all reorganizations. Store concise conclusions, evidence pointers, decisions, corrections, and reusable failure lessons—not transcripts, raw logs, secrets, personal data, or private chain-of-thought.
7. In the final response, name the memory files or topics updated/reorganized, or state `unchanged (no durable new information or maintenance needed)`.

If the canonical ledger or a required registered memory file is missing, unreadable, malformed, or cannot be updated when required, do not claim full success; report the exact problem.
