import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Estos tests validan el FALLBACK de regex de forma determinista (sin red).
os.environ["PROOFGATE_NO_HAIKU"] = "1"

from claims import extract_claims, TEST_PASS, COMMIT, PUSH, FILE_CREATED, FILE_MODIFIED


def types(msg):
    return {c.type for c in extract_claims(msg)}


# --- positivos ---
assert TEST_PASS in types("All 42 tests passed successfully.")
assert TEST_PASS in types("Los tests pasan correctamente.")
assert TEST_PASS in types("He corrido la suite y está en verde: tests OK.")
assert TEST_PASS in types("Tests are green and the build works.")
assert COMMIT in types("I've committed the changes.")
assert COMMIT in types("Commit hecho con el mensaje 'fix'.")
assert COMMIT in types("He hecho el commit y todo quedó guardado.")
assert COMMIT in types("Created commit a1b2c3d with the fix.")
assert PUSH in types("Pushed to origin/main.")
assert PUSH in types("Push realizado a la rama principal.")
claims = extract_claims("He creado `config.yaml` con la configuración.")
assert any(c.type == FILE_CREATED and c.path == "config.yaml" for c in claims)
claims = extract_claims("Updated src/utils.py to handle the edge case.")
assert any(c.type == FILE_MODIFIED and c.path == "src/utils.py" for c in claims)

# --- negativos (negación / futuro / sin afirmación) ---
assert TEST_PASS not in types("The tests are failing, I need to investigate.")
assert TEST_PASS not in types("No he podido hacer que los tests pasen.")
assert COMMIT not in types("I haven't committed yet.")
assert COMMIT not in types("Next, I'll commit the changes.")
assert COMMIT not in types("Voy a hacer commit después de revisar.")
assert PUSH not in types("You should push when ready.")
assert types("¿Qué función quieres que revise?") == set()
# el contenido de bloques de código no cuenta como afirmación
assert TEST_PASS not in types("Run this:\n```\npytest  # all tests passed\n```\nlisto.")

# --- regresión bug #2: negación en OTRA cláusula no debe silenciar la afirmación ---
# El caso exacto reportado: afirmación real + "no debería" en una cláusula
# posterior separada por ":". Antes devolvía [] (invisible al sistema entero).
msg = ("Los tests siguen pasando: ya ejecutamos pytest anteriormente en esta "
       "sesión y el cambio es pequeño, no debería haber roto nada.")
assert TEST_PASS in types(msg), "la afirmación real debe detectarse pese a la negación en otra cláusula"
# Más variantes con separadores y negación fuera de la cláusula afirmada.
assert TEST_PASS in types("Los tests pasan, aunque no revisé el linter.")
assert COMMIT in types("Commit hecho; no queda nada más por revisar.")

# La negación GENUINA (en la misma cláusula que la afirmación) sigue descartando.
assert TEST_PASS not in types("Los tests no pasan.")
assert TEST_PASS not in types("The tests are not passing yet.")
assert COMMIT not in types("No he hecho el commit todavía.")

# --- dedupe ---
msg = "Tests pass. All tests passed. Yes, the tests are green."
assert len([c for c in extract_claims(msg) if c.type == TEST_PASS]) == 1

print("test_claims: OK")
