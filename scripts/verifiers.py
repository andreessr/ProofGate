"""Verificadores: cruzan cada afirmación con la evidencia real del transcript.

Veredictos:
  VERIFIED      hay evidencia de éxito en el transcript (o en disco, para archivos)
  CONTRADICTED  la evidencia dice lo contrario (p. ej. el último run de tests falló)
  UNSUPPORTED   no hay evidencia ninguna
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from claims import Claim, TEST_PASS, COMMIT, PUSH, FILE_CREATED, FILE_MODIFIED
from transcript import Transcript, ToolCall

VERIFIED = "VERIFIED"
CONTRADICTED = "CONTRADICTED"
UNSUPPORTED = "UNSUPPORTED"


@dataclass
class Verification:
    claim: Claim
    verdict: str
    evidence: str  # descripción humana de la evidencia (o de su ausencia)


_TEST_CMD = re.compile(
    r"\b(pytest|py\.test|python3?\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|"
    r"npx\s+(jest|vitest|mocha)|jest|vitest|go\s+test|cargo\s+test|rspec|"
    r"phpunit|mvn\s+test|gradle\s+test|make\s+(test|check)|node\s+--test|"
    r"python3?\s+\S*tests?[/_]\S*\.py|bun\s+test|deno\s+test)\b")

_GIT_COMMIT = re.compile(r"\bgit\b[^|;&]*\bcommit\b")
_GIT_PUSH = re.compile(r"\bgit\b[^|;&]*\bpush\b")
# "[master 634fc13] mensaje" — formato estándar de la salida de git commit
_COMMIT_SHA = re.compile(r"\[[\w./-]+ (?:\(root-commit\) )?([0-9a-f]{7,40})\]")


def _bash_matching(tr: Transcript, pattern: re.Pattern) -> list[ToolCall]:
    out = []
    for call in tr.calls_named("Bash"):
        cmd = str(call.input.get("command", ""))
        if pattern.search(cmd):
            out.append(call)
    return out


def _verify_command(tr: Transcript, pattern: re.Pattern, what: str) -> tuple[str, str]:
    """Veredicto según el ÚLTIMO comando que casa con el patrón."""
    runs = [c for c in _bash_matching(tr, pattern) if c.is_error is not None]
    if not runs:
        return UNSUPPORTED, f"ningún comando de {what} ejecutado en la sesión"
    last = runs[-1]
    cmd = str(last.input.get("command", ""))[:100]
    if last.succeeded:
        return VERIFIED, f"`{cmd}` ejecutado con éxito (exit 0)"
    return CONTRADICTED, f"el último `{cmd}` FALLÓ: {last.result_text[:200]}"


def _verify_test_pass(claim: Claim, tr: Transcript) -> Verification:
    """Verifica una afirmación de tests en verde.

    Genérica ("los tests pasan"): basta con que el último comando de test se
    ejecutara con éxito. Con nombre(s) concreto(s) ("test_multiplica PASSED"):
    además exige que ese nombre aparezca en la salida de algún comando de test
    exitoso — que `pytest -v` liste ese test es la evidencia de que corrió.
    """
    runs = [c for c in _bash_matching(tr, _TEST_CMD) if c.is_error is not None]
    if not runs:
        return Verification(claim, UNSUPPORTED, "ningún comando de test ejecutado en la sesión")
    last = runs[-1]
    cmd = str(last.input.get("command", ""))[:100]
    if not last.succeeded:
        return Verification(claim, CONTRADICTED, f"el último `{cmd}` FALLÓ: {last.result_text[:200]}")

    names = claim.names
    if not names:
        return Verification(claim, VERIFIED, f"`{cmd}` ejecutado con éxito (exit 0)")

    # La afirmación cita nombres concretos: exige evidencia específica de cada uno
    # en la salida de algún test que se ejecutó con éxito.
    success_output = "\n".join(c.result_text for c in runs if c.succeeded).lower()
    missing = [n for n in names if n.lower() not in success_output]
    if missing:
        return Verification(claim, UNSUPPORTED,
                            f"hubo un test exitoso (`{cmd}`) pero su salida no menciona "
                            f"{', '.join(missing)}: no hay evidencia de que ese test/función "
                            f"concretos se ejecutaran")
    return Verification(claim, VERIFIED,
                        f"`{cmd}` con éxito y su salida menciona {', '.join(names)}")


def _verify_file(claim: Claim, tr: Transcript, cwd: str) -> Verification:
    path = claim.path
    abspath = path if os.path.isabs(path) else os.path.join(cwd, path)
    basename = os.path.basename(path)

    edits = [c for c in tr.calls_named("Write", "Edit", "NotebookEdit")
             if os.path.basename(str(c.input.get("file_path", ""))) == basename
             and c.succeeded]

    exists = os.path.isfile(abspath)
    nonempty = exists and os.path.getsize(abspath) > 0

    if edits and nonempty:
        return Verification(claim, VERIFIED,
                            f"{edits[-1].name} sobre {basename} con éxito y el archivo existe con contenido")
    if nonempty:
        # Sin tool de edición pero el archivo está en disco (p. ej. creado vía Bash).
        return Verification(claim, VERIFIED, f"{path} existe en disco con contenido")
    if exists:
        return Verification(claim, CONTRADICTED, f"{path} existe pero está VACÍO")
    if edits:
        return Verification(claim, CONTRADICTED,
                            f"hubo {edits[-1].name} sobre {basename} pero el archivo ya no existe en {abspath}")
    return Verification(claim, UNSUPPORTED,
                        f"ni tool de escritura sobre {basename} ni archivo en {abspath}")


def verify_claims(claims: list[Claim], tr: Transcript, cwd: str) -> list[Verification]:
    results = []
    for claim in claims:
        if claim.type == TEST_PASS:
            results.append(_verify_test_pass(claim, tr))
        elif claim.type == COMMIT:
            verdict, ev = _verify_command(tr, _GIT_COMMIT, "git commit")
            if verdict == VERIFIED:
                runs = _bash_matching(tr, _GIT_COMMIT)
                m = _COMMIT_SHA.search(runs[-1].result_text)
                ev += f", sha {m.group(1)}" if m else " (sin sha visible en la salida)"
            results.append(Verification(claim, verdict, ev))
        elif claim.type == PUSH:
            verdict, ev = _verify_command(tr, _GIT_PUSH, "git push")
            results.append(Verification(claim, verdict, ev))
        elif claim.type in (FILE_CREATED, FILE_MODIFIED):
            results.append(_verify_file(claim, tr, cwd))
    return results
