#!/usr/bin/env python3
"""ProofGate — Stop hook de Claude Code.

Lee el evento Stop por stdin, cruza las afirmaciones del mensaje de cierre
contra la evidencia real del transcript y bloquea el cierre si hay
afirmaciones sin respaldo (con límite de reintentos anti-bucle).

Regla de oro: fail-open. Cualquier error interno -> exit 0 sin bloquear.
Un bug de ProofGate jamás debe secuestrar la sesión.

Config por variables de entorno:
  PROOFGATE_DISABLED=1      desactiva todo
  PROOFGATE_MAX_BLOCKS=N    reintentos máximos por sesión (defecto 2)
  PROOFGATE_REPORT=0        no escribir TRUST_REPORT.md
  PROOFGATE_USE_HAIKU=1     usar Haiku para extraer afirmaciones ambiguas
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transcript import load_transcript  # noqa: E402
from claims import extract_claims  # noqa: E402
from verifiers import verify_claims, VERIFIED  # noqa: E402
from report import write_trust_report, build_block_reason  # noqa: E402

STATE_DIR = os.path.expanduser("~/.claude/proofgate/state")
LOG_DIR = os.path.expanduser("~/.claude/proofgate/logs")
DEFAULT_MAX_BLOCKS = 2


def log(session_id: str, msg: str) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(LOG_DIR, f"{session_id or 'unknown'}.log"), "a") as f:
            f.write(f"[{stamp}] {msg}\n")
    except OSError:
        pass


def load_state(session_id: str) -> dict:
    try:
        with open(os.path.join(STATE_DIR, f"{session_id}.json")) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"blocks": 0}


def save_state(session_id: str, state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, f"{session_id}.json"), "w") as f:
        json.dump(state, f)


def main() -> int:
    if os.environ.get("PROOFGATE_DISABLED") == "1":
        return 0

    event = json.load(sys.stdin)
    session_id = event.get("session_id", "unknown")
    transcript_path = event.get("transcript_path", "")
    cwd = event.get("cwd") or os.getcwd()
    last_message = event.get("last_assistant_message", "") or ""

    log(session_id, f"Stop recibido. stop_hook_active={event.get('stop_hook_active')} "
                    f"cwd={cwd} transcript={transcript_path}")

    tr = None
    if transcript_path and os.path.exists(transcript_path):
        tr = load_transcript(transcript_path)
        log(session_id, f"Transcript: {tr.n_lines} líneas, {len(tr.tool_calls)} tool calls "
                        f"({tr.parse_errors} errores de parseo)")
        for call in tr.tool_calls:
            detail = call.input.get("command") or call.input.get("file_path") or ""
            log(session_id, f"  tool={call.name} is_error={call.is_error} {str(detail)[:120]}")
    else:
        log(session_id, "Transcript no disponible; sin evidencia que cruzar")

    claims = extract_claims(last_message)
    log(session_id, f"Afirmaciones extraídas: {[(c.type, c.path) for c in claims]}")
    if not claims or tr is None:
        return 0

    results = verify_claims(claims, tr, cwd)
    for r in results:
        log(session_id, f"  {r.claim.type} ({r.claim.path or '-'}): {r.verdict} — {r.evidence[:120]}")

    failures = [r for r in results if r.verdict != VERIFIED]
    state = load_state(session_id)
    max_blocks = int(os.environ.get("PROOFGATE_MAX_BLOCKS", DEFAULT_MAX_BLOCKS))

    if failures and state.get("blocks", 0) < max_blocks:
        state["blocks"] = state.get("blocks", 0) + 1
        save_state(session_id, state)
        reason = build_block_reason(failures, state["blocks"], max_blocks)
        log(session_id, f"BLOQUEADO ({state['blocks']}/{max_blocks})")
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    # Cierre real: o todo verificado, o límite de reintentos alcanzado.
    limit_hit = bool(failures)
    if limit_hit:
        log(session_id, f"Límite de bloqueos alcanzado ({max_blocks}); se permite cerrar")
    if os.environ.get("PROOFGATE_REPORT", "1") != "0":
        path = write_trust_report(results, cwd, session_id, state.get("blocks", 0), limit_hit)
        log(session_id, f"TRUST_REPORT escrito en {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail-open: nunca bloquear por un error propio.
        try:
            log("errors", traceback.format_exc())
        except Exception:
            pass
        sys.exit(0)
