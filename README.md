# ProofGate 🛡️

**Impide que una sesión de Claude Code se cierre con afirmaciones sin verificar.**

ProofGate es un hook del evento `Stop` que, cada vez que el agente intenta dar
la tarea por terminada:

1. Lee el transcript real de la sesión (el JSONL con lo que de verdad pasó).
2. Extrae las afirmaciones del mensaje de cierre ("tests en verde", "commit
   hecho", "archivo creado", "push realizado").
3. Cruza cada afirmación con la evidencia real: ¿hay un comando de test
   ejecutado con exit code 0, o "tests pass" solo aparece en la narración?
   ¿Hay un `git commit` con sha real en la salida? ¿El archivo existe en disco
   con contenido?
4. Si hay afirmaciones sin respaldo (o contradichas por la evidencia),
   **bloquea el cierre** y le dice al agente exactamente qué tiene que
   demostrar — con un máximo de 2 reintentos para no crear bucles.
5. Al cierre real genera `TRUST_REPORT.md` con lo verificado vs. lo narrado
   sin evidencia.

Todo local: sin red, sin dependencias (Python 3.9+ stdlib). La única llamada
externa es opcional (Haiku, desactivada por defecto).

## Instalación

### Desde GitHub (recomendado)

Dentro de Claude Code:

```
/plugin marketplace add <tu-usuario>/ProofGate
/plugin install proofgate@proofgate-marketplace
```

### Desde una copia local

```
/plugin marketplace add /ruta/a/ProofGate
/plugin install proofgate@proofgate-marketplace
```

### Solo para probar (sin instalar)

```bash
claude --plugin-dir /ruta/a/ProofGate
```

## Probarlo en 2 minutos

1. Abre una sesión con el plugin cargado y pide algo verificable:
   *"crea `hola.txt` con un saludo y dime cuando esté"*.
   El cierre pasará (evidencia real) y verás `TRUST_REPORT.md` en el proyecto.
2. Para ver el bloqueo sin depender del agente, simúlalo:

```bash
echo '{"session_id":"demo","transcript_path":"/tmp/vacio.jsonl","cwd":"'$PWD'",
"hook_event_name":"Stop","stop_hook_active":false,
"last_assistant_message":"Los tests pasan y he hecho el commit."}' \
  | { touch /tmp/vacio.jsonl; python3 scripts/proofgate_stop.py; }
```

Verás `{"decision": "block", "reason": "..."}` con lo que habría que demostrar.

3. La suite completa del proyecto:

```bash
python3 tests/fixtures/make_fixtures.py
python3 tests/test_claims.py
python3 tests/test_verifiers.py
python3 tests/test_blocking.py
```

## Configuración (variables de entorno)

| Variable | Efecto |
|---|---|
| `PROOFGATE_DISABLED=1` | Desactiva ProofGate por completo |
| `PROOFGATE_MAX_BLOCKS=N` | Reintentos máximos por sesión (defecto 2) |
| `PROOFGATE_REPORT=0` | No generar TRUST_REPORT.md |
| `PROOFGATE_NO_HAIKU=1` | Solo-regex: no llamar a Haiku (más rápido, sin red) |
| `PROOFGATE_HAIKU_TIMEOUT=N` | Timeout en segundos de la llamada a Haiku (defecto 18) |

Las afirmaciones se extraen con **Haiku** (vía `claude -p`) por defecto: un
modelo generaliza mejor que una lista fija de regex para "¿qué afirma este
mensaje?" en cualquier idioma o conjugación. Si la llamada falla, da timeout o
no hay CLI, cae automáticamente a heurísticas de regex. `PROOFGATE_NO_HAIKU=1`
fuerza el modo solo-regex. Los **verificadores** son siempre deterministas
(sha, exit codes, contenido de archivo): Haiku solo influye en qué se extrae,
no en cómo se comprueba.

## Qué verifica (v1)

| Afirmación | Evidencia exigida |
|---|---|
| Tests en verde | Último comando de test en el transcript (`pytest`, `npm test`, `go test`, `cargo test`, …) con `is_error: false`. Si el último run falló → **CONTRADICTED** |
| Commit / push | `git commit` / `git push` ejecutado con éxito; se extrae el sha de la salida real |
| Archivo creado/modificado | Tool `Write`/`Edit` con éxito sobre esa ruta y/o el archivo existe en disco con contenido |

Veredictos: `VERIFIED` / `UNSUPPORTED` (sin evidencia) / `CONTRADICTED`
(la evidencia dice lo contrario). Ante cualquier error interno el hook hace
fail-open (exit 0): un bug de ProofGate nunca bloquea tu sesión.

## Estructura

```
.claude-plugin/plugin.json        # manifiesto del plugin
.claude-plugin/marketplace.json   # este repo es también su propio marketplace
hooks/hooks.json                  # registro del Stop hook
scripts/proofgate_stop.py         # entrada del hook (stdin JSON → veredicto)
scripts/transcript.py             # parser del JSONL de sesión
scripts/claims.py                 # extracción de afirmaciones (ES/EN)
scripts/verifiers.py              # cruce afirmación ↔ evidencia
scripts/report.py                 # TRUST_REPORT.md y mensajes de bloqueo
skills/proofgate/SKILL.md         # /proofgate:proofgate — estado y diagnóstico
tests/                            # suite completa (unit + end-to-end)
```

Logs de diagnóstico: `~/.claude/proofgate/logs/<session_id>.log`.
Estado anti-bucle: `~/.claude/proofgate/state/<session_id>.json`.

## Roadmap (v2)

- Verificación de despliegue real (deploy + curl automatizado)
- Comparación visual con IA de la UI resultante
- Trust score histórico por repo/desarrollador e integración con CI (equipo, de pago)

## Licencia

MIT
