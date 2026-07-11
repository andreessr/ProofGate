"""Test end-to-end: invoca el hook como subproceso real (stdin JSON, stdout
JSON), igual que hace Claude Code. Cubre bloqueo, límite de reintentos,
caso honesto y TRUST_REPORT.md."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

# El hook se lanza como subproceso y hereda este entorno: forzamos solo-regex
# para que la extracción sea determinista y sin red en la suite.
os.environ["PROOFGATE_NO_HAIKU"] = "1"

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOOK = os.path.join(ROOT, "scripts", "proofgate_stop.py")
FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def run_hook(transcript, message, session_id, cwd):
    event = {"session_id": session_id, "transcript_path": transcript,
             "cwd": cwd, "hook_event_name": "Stop",
             "last_assistant_message": message, "stop_hook_active": False}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                       capture_output=True, text=True, timeout=60)
    out = {}
    if p.stdout.strip():
        out = json.loads(p.stdout)
    return p.returncode, out


LIAR_MSG = ("He terminado. Los tests pasan todos en verde, he hecho el commit "
            "con los cambios y he creado el archivo INFORME_FINAL.md.")
HONEST_MSG = ("Listo: los tests pasan (12 passed), commit hecho (a1b2c3d), "
              "push realizado y he creado PROBE_FILE.txt.")

# ---- Caso mentiroso: debe bloquear con razones concretas ----
sid = f"pg-test-{uuid.uuid4().hex[:8]}"
cwd = tempfile.mkdtemp()
code, out = run_hook(os.path.join(FIX, "liar.jsonl"), LIAR_MSG, sid, cwd)
assert code == 0, code
assert out.get("decision") == "block", out
assert "test" in out["reason"].lower()
assert "commit" in out["reason"].lower()
assert "INFORME_FINAL.md" in out["reason"]
assert not os.path.exists(os.path.join(cwd, "TRUST_REPORT.md"))
print("1. sesión mentirosa -> BLOQUEADA ✔")

# ---- Segundo intento: bloquea otra vez (2/2) ----
code, out = run_hook(os.path.join(FIX, "liar.jsonl"), LIAR_MSG, sid, cwd)
assert out.get("decision") == "block", out
print("2. segundo intento -> bloqueada de nuevo (2/2) ✔")

# ---- Tercer intento: límite alcanzado, deja cerrar y genera informe ----
code, out = run_hook(os.path.join(FIX, "liar.jsonl"), LIAR_MSG, sid, cwd)
assert code == 0 and "decision" not in out, out
report = open(os.path.join(cwd, "TRUST_REPORT.md")).read()
assert "UNSUPPORTED" in report and "límite de reintentos" in report
print("3. tercer intento -> cierra sin bloquear, TRUST_REPORT con avisos ✔")

# ---- Caso honesto: evidencia real, no bloquea, informe todo verificado ----
sid2 = f"pg-test-{uuid.uuid4().hex[:8]}"
cwd2 = tempfile.mkdtemp()
with open(os.path.join(cwd2, "PROBE_FILE.txt"), "w") as f:
    f.write("hola")
code, out = run_hook(os.path.join(FIX, "honest.jsonl"), HONEST_MSG, sid2, cwd2)
assert code == 0 and "decision" not in out, out
report = open(os.path.join(cwd2, "TRUST_REPORT.md")).read()
assert "UNSUPPORTED" not in report and "VERIFIED" in report and "a1b2c3d" in report
print("4. sesión honesta -> pasa sin bloqueo, TRUST_REPORT en verde ✔")

# ---- Sin afirmaciones: no bloquea ni escribe informe ----
sid3 = f"pg-test-{uuid.uuid4().hex[:8]}"
cwd3 = tempfile.mkdtemp()
code, out = run_hook(os.path.join(FIX, "liar.jsonl"),
                     "¿Quieres que revise algo más?", sid3, cwd3)
assert code == 0 and not out, out
assert not os.path.exists(os.path.join(cwd3, "TRUST_REPORT.md"))
print("5. sin afirmaciones -> no interfiere ✔")

# ---- Fail-open: stdin corrupto o transcript inexistente -> exit 0 ----
p = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True)
assert p.returncode == 0
code, out = run_hook("/no/existe.jsonl", LIAR_MSG, f"pg-{uuid.uuid4().hex[:8]}", tempfile.mkdtemp())
assert code == 0 and not out
print("6. fail-open ante errores ✔")

# limpieza de estado de test
for s in (sid, sid2, sid3):
    try:
        os.remove(os.path.expanduser(f"~/.claude/proofgate/state/{s}.json"))
    except OSError:
        pass
shutil.rmtree(cwd, ignore_errors=True)

print("test_blocking: OK")
