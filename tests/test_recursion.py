"""Tests de la guarda anti-recursión (ver RESEARCH_RECURSION.md).

La sub-llamada a Haiku (`claude -p`) no debe disparar a su vez el hook de
ProofGate. Defensa en capas: PROOFGATE_INSIDE_HAIKU_CALL=1 en el env del
subprocess (early-exit determinista del hook) + --settings disableAllHooks
(la sub-sesión no ejecuta hooks en absoluto). Sin llamadas de red reales.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.pop("PROOFGATE_NO_HAIKU", None)
os.environ.pop("PROOFGATE_INSIDE_HAIKU_CALL", None)

import claims as C

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOOK = os.path.join(ROOT, "scripts", "proofgate_stop.py")
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LOGS = os.path.expanduser("~/.claude/proofgate/logs")

# ---- 1. Con la guarda activa, el hook sale YA: sin log de sesión, sin estado,
#         sin TRUST_REPORT, sin bloquear — aunque el mensaje sea mentiroso.
sid = f"pg-recur-{uuid.uuid4().hex[:8]}"
cwd = tempfile.mkdtemp()
event = {"session_id": sid, "transcript_path": os.path.join(FIX, "liar.jsonl"),
         "cwd": cwd, "hook_event_name": "Stop",
         "last_assistant_message": "Los tests pasan y he hecho el commit.",
         "stop_hook_active": False}
env = dict(os.environ, PROOFGATE_INSIDE_HAIKU_CALL="1", PROOFGATE_NO_HAIKU="1")
p = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                   capture_output=True, text=True, env=env, timeout=30)
assert p.returncode == 0, (p.returncode, p.stderr)
assert p.stdout.strip() == "", p.stdout          # no bloquea, no emite decision
assert not os.path.exists(os.path.join(LOGS, f"{sid}.log")), "no debe tocar el log de sesión"
assert not os.path.exists(os.path.expanduser(f"~/.claude/proofgate/state/{sid}.json"))
assert not os.path.exists(os.path.join(cwd, "TRUST_REPORT.md"))
print("1. guarda activa -> exit 0 inmediato, sin log/estado/informe/bloqueo ✔")

# ---- 1b. Sale antes incluso de leer stdin: con stdin corrupto también exit 0.
p = subprocess.run([sys.executable, HOOK], input="esto no es json",
                   capture_output=True, text=True, env=env, timeout=30)
assert p.returncode == 0 and p.stdout.strip() == ""
print("1b. guarda activa + stdin corrupto -> exit 0 (ni siquiera lee stdin) ✔")

# ---- 1c. La activación de la guarda queda registrada (observable, no silenciosa).
guard_log = os.path.join(LOGS, "recursion-guard.log")
assert os.path.exists(guard_log), "la guarda debe dejar rastro"
with open(guard_log) as f:
    assert "anti-recursión" in f.read().splitlines()[-1]
print("1c. activación registrada en recursion-guard.log ✔")

# ---- 2. _run_haiku propaga la guarda y disableAllHooks al subprocess real.
capture = os.path.join(tempfile.mkdtemp(), "capture.txt")
d = tempfile.mkdtemp()
fake = os.path.join(d, "claude")
with open(fake, "w") as f:
    # Vuelca env de la guarda + args a un fichero y responde JSON válido.
    f.write("#!/bin/sh\n"
            f"echo \"GUARD=$PROOFGATE_INSIDE_HAIKU_CALL\" > {capture}\n"
            f"echo \"ARGS=$@\" >> {capture}\n"
            "echo '[]'\n")
os.chmod(fake, 0o755)
os.environ["PROOFGATE_CLAUDE_BIN"] = fake
try:
    out = C._run_haiku("mensaje de prueba")
    assert out == "[]", out
finally:
    os.environ.pop("PROOFGATE_CLAUDE_BIN", None)
with open(capture) as f:
    captured = f.read()
assert "GUARD=1" in captured, captured
assert "disableAllHooks" in captured, captured
print("2. _run_haiku exporta PROOFGATE_INSIDE_HAIKU_CALL=1 y pasa disableAllHooks ✔")

# ---- 3. Sin la guarda en el env, el hook funciona normal (no rompimos nada):
#         mismo evento mentiroso -> bloquea.
sid2 = f"pg-recur-{uuid.uuid4().hex[:8]}"
event["session_id"] = sid2
env_normal = dict(os.environ, PROOFGATE_NO_HAIKU="1")
env_normal.pop("PROOFGATE_INSIDE_HAIKU_CALL", None)
p = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                   capture_output=True, text=True, env=env_normal, timeout=30)
assert p.returncode == 0 and json.loads(p.stdout).get("decision") == "block", p.stdout
os.remove(os.path.expanduser(f"~/.claude/proofgate/state/{sid2}.json"))
print("3. sin guarda, el hook sigue bloqueando con normalidad ✔")

# ---- 4. Auditoría: _run_haiku es el único punto que lanza `claude`.
import re
hits = []
scripts_dir = os.path.join(ROOT, "scripts")
for name in os.listdir(scripts_dir):
    if not name.endswith(".py"):
        continue
    with open(os.path.join(scripts_dir, name)) as f:
        src = f.read()
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"subprocess|os\.system|os\.exec|popen", line, re.I):
            hits.append(f"{name}:{i}: {line.strip()}")
launch_points = [h for h in hits if "import" not in h]
assert all("claims.py" in h for h in launch_points), launch_points
print("4. auditoría: claims.py es el único módulo que lanza subprocesos ✔")

print("test_recursion: OK")
