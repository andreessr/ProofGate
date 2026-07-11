"""Tests del extractor primario (Haiku) y del fallback a regex.

Nunca hace llamadas de red reales: inyecta un `haiku_extractor` fake o testea
el parseo (`_parse_haiku_json`) directamente con strings JSON de ejemplo.
"""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Partimos de un entorno limpio (otro test pudo dejar NO_HAIKU puesto).
os.environ.pop("PROOFGATE_NO_HAIKU", None)

import claims as C
from claims import (extract_claims, _parse_haiku_json, Claim,
                    TEST_PASS, COMMIT, FILE_CREATED)

# 1. Parseo de una respuesta realista de Haiku (con prosa alrededor y `names`),
#    incluido el caso reportado "Creé hello.py".
raw = '''Here are the completion claims I found:
[
  {"type":"file_created","text":"Creé hello.py con la función saludo()","path":"hello.py","names":[]},
  {"type":"test_pass","text":"test_saludo PASSED","path":"","names":["test_saludo","saludo"]}
]
That's all.'''
parsed = _parse_haiku_json(raw)
assert parsed is not None, "JSON válido no debería dar None"
byt = {c.type: c for c in parsed}
assert FILE_CREATED in byt and byt[FILE_CREATED].path == "hello.py", byt
assert TEST_PASS in byt and "test_saludo" in byt[TEST_PASS].names, byt
print("1. parseo de JSON de Haiku (con names y prosa alrededor) ✔")

# 2. Regresión del bug: "Creé hello.py" SÍ se detecta con Haiku como primario.
def fake_haiku(_message):
    return [Claim(FILE_CREATED, "Creé hello.py con la función saludo()", "hello.py", [])]

res = extract_claims("Creé hello.py con la función saludo().", haiku_extractor=fake_haiku)
assert any(c.type == FILE_CREATED and c.path == "hello.py" for c in res), res
print("2. 'Creé hello.py' vía Haiku primario -> file_created ✔")

# 3. Fallback: si Haiku devuelve None (fallo), se cae a regex, que sí sabe coger
#    una forma que cubre ("He creado hello.py").
res = extract_claims("He creado hello.py.", haiku_extractor=lambda m: None)
assert any(c.type == FILE_CREATED and c.path == "hello.py" for c in res), res
print("3. fallback a regex cuando Haiku devuelve None ✔")

# 3b. Fallback también si el extractor lanza excepción (p. ej. no hay CLI).
def boom(_m):
    raise RuntimeError("no CLI disponible")

res = extract_claims("He creado hello.py.", haiku_extractor=boom)
assert any(c.type == FILE_CREATED for c in res), res
print("3b. fallback a regex cuando Haiku lanza excepción ✔")

# 4. Los casos que el regex YA sabía manejar siguen cubiertos por el fallback:
#    negación multi-cláusula y nombre concreto de test.
res = extract_claims(
    "Los tests siguen pasando: el cambio es pequeño, no debería haber roto nada.",
    haiku_extractor=lambda m: None)
assert any(c.type == TEST_PASS for c in res), "fallback debe cubrir negación multi-cláusula"
res = extract_claims(
    "He añadido la función multiplica(a, b) y ejecuté los tests: test_multiplica PASSED.",
    haiku_extractor=lambda m: None)
tp = [c for c in res if c.type == TEST_PASS]
assert tp and "test_multiplica" in tp[0].names, "fallback debe seguir sacando names"
print("4. el fallback conserva los casos que el regex ya manejaba ✔")

# 5. PROOFGATE_NO_HAIKU=1 fuerza solo-regex: el extractor Haiku ni se llama.
called = {"n": 0}
def tracker(_m):
    called["n"] += 1
    return []

os.environ["PROOFGATE_NO_HAIKU"] = "1"
res = extract_claims("He creado hello.py.", haiku_extractor=tracker)
assert called["n"] == 0, "con NO_HAIKU=1 no debe invocarse Haiku"
assert any(c.type == FILE_CREATED for c in res), res
os.environ.pop("PROOFGATE_NO_HAIKU", None)
print("5. PROOFGATE_NO_HAIKU=1 fuerza solo-regex ✔")

# 6. Haiku devuelve [] (corrió y no vio afirmaciones): se respeta, NO se cae a
#    regex. Con Haiku primario, [] es una respuesta válida.
res = extract_claims("He creado hello.py.", haiku_extractor=lambda m: [])
assert res == [], "una lista vacía de Haiku debe respetarse, no caer a regex"
print("6. lista vacía de Haiku se respeta (no fallback) ✔")

# 7. Salida no parseable -> _parse_haiku_json None -> fallback a regex.
assert _parse_haiku_json("no json aqui") is None
assert _parse_haiku_json("") is None
res = extract_claims("He creado hello.py.",
                     haiku_extractor=lambda m: _parse_haiku_json("basura"))
assert any(c.type == FILE_CREATED for c in res), res
print("7. salida no parseable -> None -> fallback ✔")

# --- Resolución del binario `claude` (bug #4: PATH restringido) ---
_orig_fallback = C._CLAUDE_FALLBACK_PATHS
_orig_path = os.environ.get("PATH", "")


def _make_fake_claude(json_out):
    """Crea un ejecutable falso `claude` que emite `json_out` por stdout."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "claude")
    # Usa `echo` (builtin de sh) para no depender del PATH dentro del script,
    # que en estos tests se deja vacío a propósito.
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho '" + json_out + "'\n")
    os.chmod(p, 0o755)
    return p


# 8. Con PATH sin `claude`, se encuentra en una ruta de fallback conocida y se
#    ejecuta de verdad (esto SÍ ejercita _resolve_claude_bin + subprocess).
fake = _make_fake_claude('[{"type":"file_created","text":"Cree hello.py","path":"hello.py","names":[]}]')
os.environ.pop("PROOFGATE_CLAUDE_BIN", None)
os.environ.pop("PROOFGATE_NO_HAIKU", None)
os.environ["PATH"] = "/nonexistent-empty-path"   # shutil.which("claude") -> None
C._CLAUDE_FALLBACK_PATHS = [fake]
try:
    assert C._resolve_claude_bin() == fake, C._resolve_claude_bin()
    out = C._haiku_claims("Cree hello.py")   # resuelve + lanza el fake + parsea
    assert out and out[0].type == FILE_CREATED and out[0].path == "hello.py", out
finally:
    C._CLAUDE_FALLBACK_PATHS = _orig_fallback
    os.environ["PATH"] = _orig_path
print("8. binario en ruta de fallback -> se resuelve y ejecuta con PATH restringido ✔")

# 9. PROOFGATE_CLAUDE_BIN explícito tiene prioridad.
os.environ["PROOFGATE_CLAUDE_BIN"] = fake
try:
    assert C._resolve_claude_bin() == fake
finally:
    os.environ.pop("PROOFGATE_CLAUDE_BIN", None)
print("9. PROOFGATE_CLAUDE_BIN override respetado ✔")

# 10. Binario NO encontrado en ningún sitio -> None + causa registrada en el log
#     (distinguible de 'Haiku corrió y no vio nada').
msgs = []
os.environ["PATH"] = "/nonexistent-empty-path"
os.environ.pop("PROOFGATE_CLAUDE_BIN", None)
C._CLAUDE_FALLBACK_PATHS = []
try:
    assert C._resolve_claude_bin() is None
    res = C._haiku_claims("Cree hello.py", logger=msgs.append)
    assert res is None, res
    assert any("no encontrado" in m.lower() for m in msgs), msgs
finally:
    C._CLAUDE_FALLBACK_PATHS = _orig_fallback
    os.environ["PATH"] = _orig_path
print("10. binario no encontrado -> None y causa explícita en el log ✔")

# 11. El logger se propaga desde extract_claims cuando no hay binario.
msgs = []
os.environ["PATH"] = "/nonexistent-empty-path"
C._CLAUDE_FALLBACK_PATHS = []
try:
    extract_claims("He creado hello.py.", logger=msgs.append)  # cae a regex
    assert any("no encontrado" in m.lower() for m in msgs), msgs
finally:
    C._CLAUDE_FALLBACK_PATHS = _orig_fallback
    os.environ["PATH"] = _orig_path
print("11. extract_claims propaga el motivo al logger ✔")

print("test_haiku: OK")
