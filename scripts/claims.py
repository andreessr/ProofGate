"""Extracción de afirmaciones del mensaje de cierre del agente.

Heurísticas por regex (español + inglés) sobre el last_assistant_message.
Opcionalmente (PROOFGATE_USE_HAIKU=1) se pide a Haiku que extraiga
afirmaciones adicionales; las heurísticas siempre corren primero y la
llamada al modelo es best-effort con timeout corto.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

TEST_PASS = "test_pass"
COMMIT = "commit"
PUSH = "push"
FILE_CREATED = "file_created"
FILE_MODIFIED = "file_modified"


@dataclass
class Claim:
    type: str
    text: str          # frase donde se detectó
    path: str = ""     # solo para file_*


# Si la frase contiene negación o futuro/pendiente, no es una afirmación de hecho.
_NEGATION = re.compile(
    r"\b(no|not|didn'?t|don'?t|haven'?t|hasn'?t|won'?t|couldn'?t|can'?t|cannot|"
    r"never|nunca|sin|falta[n]?|pendiente[s]?|todav[ií]a|a[uú]n no|fail(ed|ing)?|"
    r"fallan?|fallaron|next|luego|after|despu[eé]s|deber[ií]as?|should|would|"
    r"necesito|need to|voy a|i'?ll|let'?s|queda[n]?)\b",
    re.IGNORECASE,
)

_PATTERNS = [
    (TEST_PASS, re.compile(
        r"\btests?\b.{0,60}\b(pass(ed|ing|es)?|green|succeed(ed|s)?|ok\b|"
        r"pasan|pasaron|pasa|en verde|verdes?|exitosos?|correctamente)",
        re.IGNORECASE | re.DOTALL)),
    (TEST_PASS, re.compile(
        r"\b(all|todos?\s+los?)\b.{0,20}\btests?\b|\bsuite\b.{0,30}\b(verde|green|pass)",
        re.IGNORECASE)),
    (COMMIT, re.compile(
        r"\bcommitted\b|"
        r"\bcommit\b.{0,40}\b(hecho|creado|realizado|listo|done|created|made|pushed)\b|"
        r"\b(he hecho|hice|made|created|creado)\b.{0,25}\bcommits?\b|"
        r"\bcommit\b.{0,25}\b[0-9a-f]{7,40}\b",
        re.IGNORECASE)),
    (PUSH, re.compile(
        r"\bpushed\b|\bpush\b.{0,25}\b(hecho|realizado|done|al?\b|to\b)",
        re.IGNORECASE)),
]

_CREATE_VERBS = re.compile(
    r"\b(created|creado|creada|he creado|generated|generado|generada|added|"
    r"añadido|añadida|wrote|escrito|escrita|escrib[ií])\b", re.IGNORECASE)
_MODIFY_VERBS = re.compile(
    r"\b(updated|actualizado|actualizada|edited|editado|editada|modified|"
    r"modificado|modificada|he actualizado|he editado|he modificado)\b",
    re.IGNORECASE)

# Rutas de archivo: token con extensión, con o sin backticks.
_PATH = re.compile(r"[`\"']?([\w~][\w./~-]*\.[A-Za-z0-9]{1,8})[`\"']?")

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)


def _sentences(message: str) -> list[str]:
    text = _CODE_FENCE.sub(" ", message)
    # Cortar por líneas y por fin de frase; suficiente para heurísticas.
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_claims(message: str) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()

    def add(ctype: str, sentence: str, path: str = "") -> None:
        key = (ctype, path.lower())
        if key not in seen:
            seen.add(key)
            claims.append(Claim(ctype, sentence, path))

    for sentence in _sentences(message):
        if _NEGATION.search(sentence):
            continue
        for ctype, pattern in _PATTERNS:
            if pattern.search(sentence):
                add(ctype, sentence)
        verb_type = None
        if _CREATE_VERBS.search(sentence):
            verb_type = FILE_CREATED
        elif _MODIFY_VERBS.search(sentence):
            verb_type = FILE_MODIFIED
        if verb_type:
            for m in _PATH.finditer(sentence):
                add(verb_type, sentence, m.group(1))

    if os.environ.get("PROOFGATE_USE_HAIKU") == "1":
        claims = _merge(claims, _haiku_claims(message))
    return claims


def _merge(base: list[Claim], extra: list[Claim]) -> list[Claim]:
    seen = {(c.type, c.path.lower()) for c in base}
    for c in extra:
        if (c.type, c.path.lower()) not in seen:
            base.append(c)
    return base


_HAIKU_PROMPT = """Extract factual completion claims from this AI assistant's closing message.
Return ONLY a JSON array; each item: {"type": one of "test_pass","commit","push","file_created","file_modified", "text": the sentence, "path": file path or ""}.
Only include things asserted as already done. Return [] if none.

Message:
"""


def _haiku_claims(message: str) -> list[Claim]:
    """Best-effort: usa el CLI `claude` con Haiku. Falla en silencio."""
    try:
        out = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5-20251001",
             _HAIKU_PROMPT + message[:6000]],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        start, end = out.find("["), out.rfind("]")
        if start == -1 or end == -1:
            return []
        items = json.loads(out[start:end + 1])
        return [Claim(i["type"], i.get("text", ""), i.get("path", "") or "")
                for i in items
                if i.get("type") in (TEST_PASS, COMMIT, PUSH, FILE_CREATED, FILE_MODIFIED)]
    except Exception:
        return []
