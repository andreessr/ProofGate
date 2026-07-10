---
description: Explain ProofGate status, show the latest TRUST_REPORT, or check why a stop was blocked. Use when the user asks about ProofGate, trust reports, blocked stops, or unverified claims.
---

# ProofGate

ProofGate is a Stop hook that prevents this session from closing with
unverified claims. When the agent's closing message asserts something
("tests pass", "committed", "created file X"), ProofGate cross-checks it
against real evidence in the session transcript (actual tool results: test
exit codes, git commit shas, files on disk) and blocks the stop if the
evidence is missing or contradictory, up to 2 retries per session.

When invoked:

1. If the user asks for status or the report, read `TRUST_REPORT.md` in the
   project root (if present) and summarize verified vs. unverified claims.
2. For diagnostics, check the session log under `~/.claude/proofgate/logs/`
   (one file per session id) and the retry state under
   `~/.claude/proofgate/state/`.
3. Explain configuration when asked:
   - `PROOFGATE_DISABLED=1` — disable entirely
   - `PROOFGATE_MAX_BLOCKS=N` — max blocks per session (default 2)
   - `PROOFGATE_REPORT=0` — skip TRUST_REPORT.md generation
   - `PROOFGATE_USE_HAIKU=1` — also use Haiku (via `claude -p`) to extract
     claims that the regex heuristics miss

If a stop was just blocked by ProofGate: do not argue with the hook. Either
actually run the commands that produce the evidence (run the test suite, do
the real commit) or rewrite the closing message to honestly state what was
not done.
