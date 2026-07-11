"""Tests del extractor primario (Haiku) y del fallback a regex.

Nunca hace llamadas de red reales: inyecta un `haiku_extractor` fake o testea
el parseo (`_parse_haiku_json`) directamente con strings JSON de ejemplo.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Partimos de un entorno limpio (otro test pudo dejar NO_HAIKU puesto).
os.environ.pop("PROOFGATE_NO_HAIKU", None)

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

print("test_haiku: OK")
