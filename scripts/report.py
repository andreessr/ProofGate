"""Generación del TRUST_REPORT.md y del mensaje de bloqueo para el agente."""

from __future__ import annotations

import os
import time

from verifiers import Verification, VERIFIED, CONTRADICTED

_LABEL = {
    "test_pass": "Tests en verde",
    "commit": "Commit realizado",
    "push": "Push realizado",
    "file_created": "Archivo creado",
    "file_modified": "Archivo modificado",
}

_DEMAND = {
    "test_pass": "Run the actual test suite with the Bash tool and show its real exit code.",
    "commit": "Run `git commit` for real (and `git log -1 --oneline` to show the sha).",
    "push": "Run `git push` for real and show its output.",
    "file_created": "Create the file for real (Write tool) or correct your claim.",
    "file_modified": "Apply the edit for real (Edit/Write tool) or correct your claim.",
}


def build_block_reason(failures: list[Verification], blocks: int, max_blocks: int) -> str:
    lines = [
        "ProofGate blocked this stop: your closing message makes claims with no "
        "supporting evidence in the session transcript.",
        "",
    ]
    for i, r in enumerate(failures, 1):
        label = _LABEL.get(r.claim.type, r.claim.type)
        lines.append(f"{i}. You claim \"{r.claim.text.strip()[:140]}\" [{label}] — "
                     f"{'CONTRADICTED' if r.verdict == CONTRADICTED else 'no evidence found'}: {r.evidence}")
        lines.append(f"   → {_DEMAND.get(r.claim.type, 'Provide real evidence.')}")
    lines.append("")
    lines.append("Either produce the evidence by actually running the commands, or "
                 "rewrite your closing message to honestly state what was NOT done. "
                 f"(ProofGate attempt {blocks}/{max_blocks}; after that the session "
                 "closes and unverified claims are flagged in TRUST_REPORT.md.)")
    return "\n".join(lines)


def write_trust_report(results: list[Verification], cwd: str, session_id: str,
                       blocks_used: int, limit_hit: bool) -> str:
    verified = [r for r in results if r.verdict == VERIFIED]
    flagged = [r for r in results if r.verdict != VERIFIED]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# TRUST_REPORT — ProofGate",
        "",
        f"- **Sesión**: `{session_id}`",
        f"- **Fecha**: {stamp}",
        f"- **Afirmaciones verificadas**: {len(verified)}/{len(results)}",
        f"- **Bloqueos usados**: {blocks_used}",
    ]
    if limit_hit:
        lines.append("- ⚠️ **La sesión cerró con afirmaciones sin verificar "
                     "(límite de reintentos alcanzado)**")
    lines += ["", "| Afirmación | Tipo | Veredicto | Evidencia |",
              "|---|---|---|---|"]
    for r in results:
        icon = {"VERIFIED": "✅", "CONTRADICTED": "❌", "UNSUPPORTED": "⚠️"}[r.verdict]
        text = r.claim.text.strip().replace("|", "\\|")[:100]
        ev = r.evidence.replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {text} | {_LABEL.get(r.claim.type, r.claim.type)} | "
                     f"{icon} {r.verdict} | {ev} |")
    lines.append("")
    lines.append("_Generado automáticamente por ProofGate al cierre de la sesión._")

    path = os.path.join(cwd, "TRUST_REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
