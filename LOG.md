# LOG.md — ProofGate

## 2026-07-10 — Sesión inicial: FASE 0 → FASE 3 completas

### Completado

- **FASE 0**: RESEARCH.md con el formato verificado del evento Stop (docs
  oficiales) + estructura del JSONL inspeccionada sobre transcripts reales de
  esta máquina (incluido uno de 8.8 MB con ejemplos de `is_error: true`).
- **Checkpoint 1** (lector): hook parsea un transcript real de 104 líneas y
  empareja 21 tool calls con sus resultados. Test manual con stdin real → exit 0. ✔
- **Checkpoint 2** (afirmaciones): `claims.py`, heurísticas ES/EN con guardas
  de negación/futuro y exclusión de bloques de código. `tests/test_claims.py`
  (24 asserts) en verde. ✔
- **Checkpoint 3** (verificadores): `verifiers.py` con VERIFIED /
  UNSUPPORTED / CONTRADICTED. `tests/test_verifiers.py` en verde sobre
  fixtures JSONL con el formato real. ✔
- **Checkpoints 4+5+7** (bloqueo, informe, e2e): `tests/test_blocking.py`
  ejecuta el hook como subproceso real: sesión mentirosa bloqueada 2 veces,
  a la 3ª cierra con TRUST_REPORT marcando lo no verificado; sesión honesta
  pasa a la primera con informe en verde; sin afirmaciones no interfiere;
  fail-open ante stdin corrupto y transcript inexistente. 6/6 ✔
- **Checkpoint 6** (empaquetado): plugin.json, hooks.json, SKILL.md,
  marketplace.json. `claude plugin validate .` → "Validation passed". ✔
- **Smoke test real**: `claude --plugin-dir . --model haiku -p "..."` → el
  Stop hook se dispara en una sesión real (log de sesión lo confirma). ✔
- **FASE 3 extra**: hook ejecutado contra el transcript de esa sesión real
  con un mensaje de cierre falso → `decision: "block"` con las dos exigencias
  correctas (test + commit).

### Decisiones de diseño (y por qué)

1. **Python stdlib, un solo hook**: cero dependencias, portable, <1 s de
   ejecución en el caso normal (muy por debajo del timeout).
2. **`last_assistant_message` del input, no del transcript**: la doc avisa de
   que el transcript se escribe asíncrono y puede no incluir el último turno.
   El transcript se usa solo para la evidencia (turnos anteriores).
3. **Bloqueo vía JSON (`decision: "block"`) y no exit 2**: más explícito y
   deja abierta la puerta a `additionalContext` en v2.
4. **Anti-bucle**: contador persistente por sesión en
   `~/.claude/proofgate/state/` con máximo 2 bloqueos (configurable). Al
   agotarse se permite cerrar y el TRUST_REPORT deja constancia. Fail-open
   ante cualquier excepción: un bug del hook no debe secuestrar la sesión.
5. **Haiku opcional y OFF por defecto** (`PROOFGATE_USE_HAIKU=1`): las
   heurísticas cubren el MVP; la llamada añade latencia/coste en cada Stop.
   Implementada vía CLI `claude -p` para no gestionar API keys.
6. **Exit code de Bash**: el transcript no serializa el exit code en éxito;
   se infiere de `is_error` (fallos reales aparecen como "Exit code N" con
   `is_error: true`). Documentado en RESEARCH.md.
7. **Archivos**: el disco es la verdad última — un archivo que existe con
   contenido verifica la afirmación aunque se creara vía Bash; un archivo
   vacío o desaparecido la contradice.
8. **TRUST_REPORT solo si hubo afirmaciones**: Stop se dispara en CADA turno;
   escribir informe en turnos triviales sería ruido.

### Ajustes al plan

- La "posterioridad del test respecto a la última edición" (Checkpoint 3) se
  queda en v2 tal y como preveía el plan; en v1 manda el último run.
- El test en vivo de "agente que miente" no se pudo forzar con un prompt
  (Haiku se negó a afirmar cosas falsas — comportamiento correcto del
  modelo). Se sustituyó por la ejecución del hook contra el transcript real
  de esa sesión con un mensaje de cierre falso, que es exactamente la misma
  ruta de código que ejecuta Claude Code.

### Pendiente / decisiones de producto para el usuario

- **Publicación en GitHub**: el repo está listo (marketplace.json apunta a
  `./`). Falta crear el repo remoto y hacer push — acción externa que dejo
  al usuario (comandos en README/mensaje final).
- Nombre público del marketplace (`proofgate-marketplace`) y handle de GitHub
  en el README (`<tu-usuario>`) por confirmar.
- v2: posterioridad temporal test↔edición, verificación de deploy, Haiku por
  defecto si el coste resulta aceptable.
