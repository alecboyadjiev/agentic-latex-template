# Repository Agent Instructions

## Durable memory

These rules apply to every repository task, whether or not a task-specific prompt repeats them.

1. Before substantive work, open `agent-data/memory/ledger.yaml`. It is the canonical memory entry point and dynamic manifest. Follow its protocol and read only the registered memory files relevant to the task, target paths, notation, prior issues, and failed approaches.
2. Treat memory as fallible prior context. Recheck claims that affect decisions against current task inputs, repository contents, and verification results; current verified evidence wins. Correct stale or superseded memory explicitly.
3. After substantive verification and before the final report, update memory only for durable, novel information. If neither new durable information nor useful database maintenance exists, leave memory unchanged and report an explicit no-op.
4. Agents own the organization of `agent-data/memory/`. During every memory write, assess whether the current files remain efficient for agent retrieval. Create, split, merge, rename, consolidate, or retire managed memory files when that improves retrieval or maintenance. Do not impose a permanent taxonomy, wait for a monolith to become unusable, or create files per run, conversation, or observation.
5. Keep `agent-data/memory/ledger.yaml` compact and authoritative as the registry and protocol. Register every managed memory file and record structural changes in its topology history. Before writing, reread the ledger and every affected memory file; merge concurrent updates and stop rather than clobbering changes that cannot be reconciled confidently.
6. Preserve distinct insights and correction history through all reorganizations. Store concise conclusions, evidence pointers, decisions, corrections, and reusable failure lessons—not transcripts, raw logs, secrets, personal data, or private chain-of-thought.
7. In the final response, name the memory files or topics updated/reorganized, or state `unchanged (no durable new information or maintenance needed)`.

If the canonical ledger or a required registered memory file is missing, unreadable, malformed, or cannot be updated when required, do not claim full success; report the exact problem.

## PDF source access

Never open, render, OCR, extract, or otherwise inspect a PDF directly when using it as source material. Use `.pdf-cache/manifest.json` to locate the corresponding cached `document.txt` and metadata under `.pdf-cache/sha256/`. Treat a missing, stale, or failed cache entry as a source-access blocker; read-only command agents must report it instead of invoking PDF tools or rebuilding the cache.

Direct PDF tool calls are permitted only to verify a PDF that the current agent itself created or updated during the current task. This exception is for checking the agent's own output, not for reading references, prior artifacts, or other source PDFs.

## Command agents

Every command agent may write task-supporting data beneath `agent-data/` and is read-only elsewhere unless the handler grants a manuscript-writing agent its one explicitly resolved paper directory. `agent-data/` does not expand an agent's final-output contract. Preserve unrelated data, put transient files under `agent-data/scratch/`, and use `agent-data/memory/` only through the durable-memory ledger protocol.

Green and yellow command agents still return reports, README content, or JSON definitions to the handler; they must not write those handler-owned destinations themselves. Manuscript-writing red agents may edit only the resolved paper directory in addition to `agent-data/`; their final response and build verification replace ordinary artifact and memory-write reporting.
