"""Extracción de afirmaciones del mensaje de cierre del agente.

Extractor PRIMARIO: una llamada a Haiku que identifica las afirmaciones en
lenguaje natural (cualquier idioma/tiempo verbal) y las devuelve en JSON.
Generaliza mejor que una lista fija de patrones, que nunca cubre todas las
conjugaciones y sinónimos de ES+EN.

Extractor de FALLBACK: heurísticas por regex. Se usan solo si la llamada a
Haiku falla, da timeout o no hay CLI disponible — la red de seguridad, no el
método principal.

Config:
  PROOFGATE_NO_HAIKU=1        fuerza solo-regex (más rápido, sin red)
  PROOFGATE_HAIKU_TIMEOUT=N   timeout en segundos de la llamada a Haiku (def. 18)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

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
    # Para test_pass: nombres concretos citados en la afirmación (p. ej.
    # "test_multiplica", "multiplica"). Si está vacío, la afirmación es
    # genérica ("los tests pasan") y basta con que hubiera un test exitoso.
    names: list[str] = field(default_factory=list)


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

# --- Nombres concretos citados en una afirmación de tests ---
# Identificadores de test estilo pytest (los que aparecen verbatim en la
# salida de `pytest -v`): test_xxx, xxxTest, TestXxx.
_TEST_NAME = re.compile(r"\b(test_\w+|\w+_test|Test[A-Z]\w+|\w*Test)\b")
# Referencia explícita a un símbolo: "función multiplica", "method foo".
_FUNC_KEYWORD = re.compile(
    r"\b(?:funci[oó]n|function|m[eé]todo|method|clase|class)\s+`?([A-Za-z_]\w+)",
    re.IGNORECASE)
# Llamada a función con paréntesis pegado, p. ej. "multiplica(a, b)".
# El paréntesis debe ir SIN espacio detrás del identificador para no capturar
# prosa como "los tests pasan (12 passed)".
_FUNC_CALL = re.compile(r"\b([A-Za-z_]\w+)\(")
# Palabras que NO son nombres de símbolo aunque casen con los patrones.
_NAME_STOPWORDS = {"test", "tests", "suite", "suites"}

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)

# Separadores de cláusula DENTRO de una frase. Se usan solo para acotar la
# comprobación de negación (no para partir la frase de cara al matching
# positivo), porque los patrones de _PATTERNS pueden abarcar varias cláusulas.
_CLAUSE_SEP = re.compile(r"[:;,]")


def _sentences(message: str) -> list[str]:
    text = _CODE_FENCE.sub(" ", message)
    # Cortar por líneas y por fin de frase; suficiente para heurísticas.
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _negated_near(sentence: str, start: int, end: int) -> bool:
    """¿Hay una negación en la MISMA cláusula que la afirmación detectada?

    Acota la búsqueda de negación a la cláusula que contiene el match: desde el
    separador (: ; ,) anterior al match hasta el posterior. Así una negación en
    otra cláusula ("los tests pasan: ... no debería haber roto nada") no
    descarta una afirmación verdadera, mientras el patrón positivo sigue viendo
    la frase completa. Si el match abarca varias cláusulas, la negación dentro
    de ese tramo sí cuenta.
    """
    left = 0
    for m in _CLAUSE_SEP.finditer(sentence, 0, start):
        left = m.end()
    right_m = _CLAUSE_SEP.search(sentence, end)
    right = right_m.start() if right_m else len(sentence)
    return bool(_NEGATION.search(sentence[left:right]))


def _test_names(sentence: str) -> list[str]:
    """Nombres concretos de test/función citados en una frase de tests."""
    found: list[str] = []
    for regex in (_TEST_NAME, _FUNC_KEYWORD, _FUNC_CALL):
        for m in regex.finditer(sentence):
            name = m.group(1)
            if name.lower() in _NAME_STOPWORDS:
                continue
            if name not in found:
                found.append(name)
    return found


def extract_claims(message: str, *, haiku_extractor=None) -> list[Claim]:
    """Extrae afirmaciones del mensaje de cierre.

    Primario: Haiku (salvo PROOFGATE_NO_HAIKU=1). Fallback: regex, cuando Haiku
    devuelve None (fallo/timeout/sin CLI) o lanza. Si Haiku devuelve una lista
    (incluso vacía) se respeta: significa que corrió y esas son las afirmaciones.

    `haiku_extractor` es un punto de inyección para tests (evita la red real).
    """
    if os.environ.get("PROOFGATE_NO_HAIKU") != "1":
        extractor = haiku_extractor if haiku_extractor is not None else _haiku_claims
        try:
            result = extractor(message)
        except Exception:
            result = None  # fail-open al fallback de regex
        if result is not None:
            return result
    return _regex_claims(message)


def _regex_claims(message: str) -> list[Claim]:
    claims: list[Claim] = []
    by_key: dict[tuple[str, str], Claim] = {}

    def add(ctype: str, sentence: str, path: str = "", names: list[str] | None = None) -> None:
        key = (ctype, path.lower())
        claim = by_key.get(key)
        if claim is None:
            claim = Claim(ctype, sentence, path, list(names or []))
            by_key[key] = claim
            claims.append(claim)
        elif names:
            # Misma afirmación en otra frase: acumula los nombres citados.
            for n in names:
                if n not in claim.names:
                    claim.names.append(n)

    for sentence in _sentences(message):
        # La negación se comprueba por cláusula (ventana alrededor de cada
        # match), no sobre la frase entera: una negación en OTRA cláusula no
        # debe silenciar una afirmación verdadera.
        for ctype, pattern in _PATTERNS:
            for m in pattern.finditer(sentence):
                if not _negated_near(sentence, m.start(), m.end()):
                    names = _test_names(sentence) if ctype == TEST_PASS else None
                    add(ctype, sentence, names=names)
                    break  # una afirmación de este tipo por frase basta

        verb_match = _CREATE_VERBS.search(sentence)
        verb_type = FILE_CREATED
        if not verb_match:
            verb_match = _MODIFY_VERBS.search(sentence)
            verb_type = FILE_MODIFIED
        if verb_match and not _negated_near(sentence, verb_match.start(), verb_match.end()):
            for m in _PATH.finditer(sentence):
                add(verb_type, sentence, m.group(1))

    return claims


# --- Extractor primario: Haiku ---

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_VALID_TYPES = (TEST_PASS, COMMIT, PUSH, FILE_CREATED, FILE_MODIFIED)

_HAIKU_PROMPT = """You extract factual COMPLETION claims from an AI coding assistant's final message to its user. A completion claim is something the assistant states it HAS ALREADY DONE — not something it plans to do, and not something it says it did NOT do.

Return ONLY a JSON array. Each element is an object:
{
  "type": one of "test_pass", "commit", "push", "file_created", "file_modified",
  "text": the exact sentence the claim comes from,
  "path": for file_created/file_modified, the file path (e.g. "src/app.py"); "" otherwise,
  "names": for test_pass, the specific test or function names the message says passed (e.g. ["test_multiplica","multiplica"]); [] for a generic "tests pass" claim or any other type
}

Rules:
- Include ONLY things asserted as already completed. Ignore intentions ("I'll commit next", "voy a crear"), and ignore explicit non-completions ("I did not run the tests", "no he hecho commit").
- Detect claims in ANY language (Spanish and English included) and ANY verb tense. For example "Creé hello.py", "He creado hello.py" and "I created hello.py" are all file_created with path "hello.py".
- Return [] if there are no completion claims.

Message:
"""


def _run_haiku(message: str) -> str | None:
    """Ejecuta el CLI `claude` con Haiku. Devuelve stdout o None si falla."""
    timeout = float(os.environ.get("PROOFGATE_HAIKU_TIMEOUT", "18"))
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", _HAIKU_MODEL, _HAIKU_PROMPT + message[:6000]],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    out = (proc.stdout or "").strip()
    return out or None


def _parse_haiku_json(raw: str) -> list[Claim] | None:
    """Parsea la respuesta de Haiku a lista de Claim. None si no es parseable
    (se trata como fallo → fallback). Lista vacía es válido (no hay afirmaciones)."""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        items = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(items, list):
        return None
    claims: list[Claim] = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") not in _VALID_TYPES:
            continue
        raw_names = it.get("names")
        names = [str(n) for n in raw_names if isinstance(n, str) and n] \
            if isinstance(raw_names, list) else []
        claims.append(Claim(it["type"], it.get("text", "") or "",
                            it.get("path", "") or "", names))
    return claims


def _haiku_claims(message: str) -> list[Claim] | None:
    """Extractor primario. None = falló (→ fallback a regex)."""
    raw = _run_haiku(message)
    if raw is None:
        return None
    return _parse_haiku_json(raw)
