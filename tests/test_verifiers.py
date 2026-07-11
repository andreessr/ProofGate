import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ["PROOFGATE_NO_HAIKU"] = "1"  # extracción determinista vía regex

from transcript import load_transcript
from claims import Claim, extract_claims, TEST_PASS, COMMIT, PUSH, FILE_CREATED
from verifiers import verify_claims, VERIFIED, UNSUPPORTED, CONTRADICTED

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
cwd = tempfile.mkdtemp()
# archivo real en disco para la verificación de file_created de la sesión honesta
with open(os.path.join(cwd, "PROBE_FILE.txt"), "w") as f:
    f.write("hola")

honest = load_transcript(os.path.join(FIX, "honest.jsonl"))
liar = load_transcript(os.path.join(FIX, "liar.jsonl"))
contra = load_transcript(os.path.join(FIX, "contradicted.jsonl"))

claims = [Claim(TEST_PASS, "tests pass"), Claim(COMMIT, "committed"),
          Claim(PUSH, "pushed"), Claim(FILE_CREATED, "created PROBE_FILE.txt", "PROBE_FILE.txt")]

# Honesta: todo VERIFIED, y el commit trae sha.
res = verify_claims(claims, honest, cwd)
assert [r.verdict for r in res] == [VERIFIED] * 4, [(r.claim.type, r.verdict) for r in res]
assert "a1b2c3d" in [r for r in res if r.claim.type == COMMIT][0].evidence

# Mentirosa: todo UNSUPPORTED (el archivo tampoco existe en otro cwd).
res = verify_claims(claims, liar, tempfile.mkdtemp())
assert [r.verdict for r in res] == [UNSUPPORTED] * 4, [(r.claim.type, r.verdict) for r in res]

# Contradicha: el último run de tests falló.
res = verify_claims([Claim(TEST_PASS, "tests pass")], contra, cwd)
assert res[0].verdict == CONTRADICTED, res[0]
assert "FALLÓ" in res[0].evidence

# Archivo vacío = CONTRADICTED.
open(os.path.join(cwd, "empty.txt"), "w").close()
res = verify_claims([Claim(FILE_CREATED, "created empty.txt", "empty.txt")], liar, cwd)
assert res[0].verdict == CONTRADICTED

# --- Regresión: afirmación sobre función/test NUNCA ejecutado ---
# Transcript con OTRO test exitoso (suma/resta), afirmación sobre multiplica.
named_mismatch = load_transcript(os.path.join(FIX, "named_mismatch.jsonl"))
claims_named = extract_claims(
    "He añadido la función multiplica(a, b) y ejecuté los tests: test_multiplica PASSED. Todo en verde.")
tp = [c for c in claims_named if c.type == TEST_PASS]
assert tp, "debería detectarse una afirmación test_pass"
assert "test_multiplica" in tp[0].names, tp[0].names   # nombre concreto capturado
assert "multiplica" in tp[0].names, tp[0].names
res = verify_claims(tp, named_mismatch, cwd)
assert res[0].verdict == UNSUPPORTED, res[0]           # <-- el bug: antes daba VERIFIED
print("regresión bug named-test: afirmación sobre test no ejecutado -> UNSUPPORTED ✔")

# El mismo tipo de afirmación pero con evidencia REAL del nombre -> VERIFIED.
named_ok = load_transcript(os.path.join(FIX, "named_ok.jsonl"))
res = verify_claims(tp, named_ok, cwd)
assert res[0].verdict == VERIFIED, res[0]
print("caso honesto con nombre presente en la salida -> VERIFIED ✔")

# La afirmación genérica sigue verificándose con cualquier test exitoso.
generic = verify_claims([Claim(TEST_PASS, "los tests pasan")], named_mismatch, cwd)
assert generic[0].verdict == VERIFIED, generic[0]
print("afirmación genérica sin nombre -> sigue VERIFIED ✔")

print("test_verifiers: OK")
