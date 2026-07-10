# PLAN.md — ProofGate

Stack elegido: **Python 3 (stdlib únicamente)**, un solo script principal
`scripts/proofgate_stop.py` + módulos pequeños. Sin red salvo Haiku opcional.
Fail-open siempre (excepción interna → exit 0, nunca bloquear por bug propio).

## Checkpoint 1 — Lector de transcript (no bloquea)

Hook que lee el JSON de stdin, parsea el transcript JSONL, indexa pares
tool_use ↔ tool_result y escribe lo observado en
`~/.claude/proofgate/logs/<session_id>.log`. Devuelve exit 0 siempre.

**Test**: `python3 scripts/proofgate_stop.py < fixture_stop_input.json` usando
un transcript real de esta máquina. Espero: exit 0, log con recuento de
mensajes, lista de tools llamadas con is_error, y el last_assistant_message.

## Checkpoint 2 — Extracción de afirmaciones

Módulo `claims.py`: dado `last_assistant_message`, devuelve lista de
afirmaciones tipadas: `test_pass`, `commit`, `push`, `file_created`,
`file_modified` (+ opcional Haiku para ambiguos, off por defecto).

**Test**: `python3 tests/test_claims.py` con ~15 mensajes de ejemplo
(positivos y negativos, español e inglés). Espero: todos los asserts pasan,
exit 0 real del comando.

## Checkpoint 3 — Verificadores

Módulo `verifiers.py`: cruza cada afirmación con la evidencia del transcript:
- `test_pass`: hubo Bash con comando de test (pytest/npm test/go test/cargo
  test/jest/vitest…) cuyo tool_result tiene `is_error: false` y posterior a
  la última edición de código (posterioridad: v2; en v1 basta el último run).
- `commit`/`push`: Bash `git commit`/`git push` con is_error false; extraer
  sha del output si existe.
- `file_created`/`file_modified`: tool Write/Edit sobre esa ruta con éxito, o
  Bash que la crea; además comprobación directa en disco (existe + no vacío).

Veredictos: `VERIFIED` (evidencia en transcript), `UNSUPPORTED` (sin
evidencia), `CONTRADICTED` (evidencia de fallo: último run de test con
is_error true).

**Test**: `python3 tests/test_verifiers.py` con transcripts sintéticos
(fixtures JSONL) que cubren cada veredicto. Espero: asserts en verde con
exit 0 real.

## Checkpoint 4 — Bloqueo con límite de reintentos

Si hay UNSUPPORTED/CONTRADICTED y `blocks < MAX_BLOCKS (2)`: stdout
`{"decision":"block","reason":<qué demostrar>}`, incrementa contador en
`~/.claude/proofgate/state/<session_id>.json`. Si se alcanza el límite o no
hay hallazgos: exit 0 sin bloquear.

**Test**: `python3 tests/test_blocking.py`: (a) fixture con afirmación falsa →
stdout contiene `"decision": "block"`; (b) segunda y tercera invocación misma
sesión → a la tercera NO bloquea; (c) fixture con evidencia real → no bloquea.

## Checkpoint 5 — TRUST_REPORT.md

Al cierre real (sin hallazgos o límite alcanzado) escribe `TRUST_REPORT.md`
en el cwd del proyecto: tabla verificado vs. narrado sin evidencia, con la
evidencia citada (comando + resultado) y el estado del contador de bloqueos.

**Test**: correr el hook con fixture mixta → existe TRUST_REPORT.md, contiene
las secciones esperadas (grep de "VERIFIED" y "UNSUPPORTED").

## Checkpoint 6 — Empaquetado plugin + marketplace

`.claude-plugin/plugin.json`, `hooks/hooks.json` (Stop →
`${CLAUDE_PLUGIN_ROOT}/scripts/proofgate_stop.py`), `skills/proofgate/SKILL.md`,
`.claude-plugin/marketplace.json` (este mismo repo actúa de marketplace),
README con instalación.

**Test**: `claude plugin validate .` (si está disponible) o validación JSON
manual; arranque con `claude --plugin-dir . --help` no falla; los JSON parsean.

## Checkpoint 7 — Autoverificación end-to-end (FASE 3)

Fixture "sesión mentirosa": transcript sin ningún test ejecutado +
last_assistant_message "los tests pasan y he hecho commit" → el hook la
bloquea con razones concretas. Fixture "sesión honesta": transcript con pytest
exit 0 real y git commit con sha → pasa sin bloqueo y TRUST_REPORT lo refleja.

**Test**: `python3 tests/test_e2e.py` ejecuta el hook como subproceso real
(mismo modo en que lo invoca Claude Code) y comprueba stdout/exit code.

## Registro

Tras cada checkpoint: correr sus tests y actualizar LOG.md (hecho, pendiente,
decisiones). Los cambios de plan se anotan, no se silencian.
