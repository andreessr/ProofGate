# RESEARCH.md — Formato del evento Stop y transcripts de Claude Code

Fecha: 2026-07-10. Fuentes: documentación oficial (code.claude.com/docs/en/hooks,
/plugins, /plugin-marketplaces) + inspección directa de transcripts JSONL reales
en `~/.claude/projects/` en esta máquina.

## 1. Input del hook Stop (stdin, JSON)

El hook Stop recibe por stdin un único objeto JSON:

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/Users/andre/.claude/projects/<proyecto-slug>/<session_id>.jsonl",
  "cwd": "/Users/andre/my-project",
  "permission_mode": "default",
  "hook_event_name": "Stop",
  "last_assistant_message": "Texto completo de la respuesta final de Claude en este turno...",
  "stop_hook_active": false
}
```

Puntos críticos (verificados en la doc oficial):

- **`last_assistant_message`**: el texto completo del mensaje de cierre viene
  directamente en el input. Es la fuente correcta para extraer afirmaciones,
  porque **el transcript se escribe de forma asíncrona y puede no incluir aún
  los últimos mensajes del turno** cuando el hook se dispara. El transcript se
  usa para la EVIDENCIA (tool calls/results anteriores), no para el mensaje final.
- **`stop_hook_active`**: `true` cuando Claude ya está continuando como
  resultado de un Stop hook previo. Es la primera línea de defensa anti-bucle.
- **Timeout**: 600 s por defecto para hooks (configurable con `"timeout"` en la
  config del hook). Aun así apuntamos a <5 s en el caso normal.
- Stop **no soporta matchers**; se dispara en cada fin de turno.

## 2. Formato del transcript JSONL (verificado contra sesiones reales)

Cada línea es un JSON independiente. Tipos observados en
`~/.claude/projects/-Users-andre-Desktop-ProofGate/<session>.jsonl`:

| `type` | Contenido relevante |
|---|---|
| `user` | Prompt del usuario **o** resultado de tool (ver abajo) |
| `assistant` | Mensaje del modelo: `message.content[]` con bloques `text`, `thinking`, `tool_use` |
| `system` | Meta (avisos, hooks ejecutados); tiene `subtype`, `level`, `content` |
| `attachment` | Adjuntos/recordatorios del harness |
| `file-history-snapshot`, `mode`, `permission-mode`, `last-prompt` | Metadatos de sesión, sin `message` |

Campos comunes en líneas con mensaje: `uuid`, `parentUuid`, `timestamp` (ISO),
`sessionId`, `cwd`, `gitBranch`, `isSidechain` (true = línea de un subagente,
hay que excluirlas o tratarlas aparte), `version`.

### 2.1 Llamada a tool (línea `assistant`)

```json
{"type":"assistant","message":{"role":"assistant","content":[
  {"type":"tool_use","id":"toolu_01Fzt...","name":"Bash",
   "input":{"command":"ls ...","description":"..."}}
]}}
```

### 2.2 Resultado de tool (línea `user`)

```json
{"type":"user","message":{"role":"user","content":[
  {"type":"tool_result","tool_use_id":"toolu_01Fzt...",
   "content":"<stdout como string, o lista de bloques>","is_error":false}
]},
 "toolUseResult":{"stdout":"...","stderr":"","interrupted":false,"isImage":false}}
```

Observaciones clave para los verificadores (de un transcript real de 8.8 MB):

- El emparejamiento tool_use ↔ tool_result es por `tool_use_id`.
- **Bash fallido**: `is_error: true` y el content empieza con `"Exit code N"`
  seguido del output. Ejemplo real:
  `{"type":"tool_result","content":"Exit code 1\nTraceback...","is_error":true}`.
- **Bash exitoso**: `is_error: false` (o ausente); el exit code 0 NO se
  serializa explícitamente — la ausencia de error implica exit 0.
- `toolUseResult` (nivel superior de la línea, no dentro de `message`) trae
  `stdout`/`stderr`/`interrupted` para Bash; para Write/Edit trae otras formas
  (p. ej. `type: "create"`, `filePath`). Es un campo redundante/estructurado
  útil pero no garantizado en todas las versiones → el verificador se apoya
  primero en `message.content[].tool_result` + `is_error`.
- `content` de un tool_result puede ser **string o lista** de bloques
  `{"type":"text","text":...}` → normalizar siempre.

## 3. Patrón de bloqueo

Dos mecanismos equivalentes:

1. **Exit code 2** + mensaje por **stderr** → stderr se le muestra a Claude y
   el turno no termina.
2. **Exit 0 + JSON por stdout**: `{"decision":"block","reason":"..."}` →
   `reason` se le muestra a Claude.

Elegimos la opción 2 (JSON) porque permite además `hookSpecificOutput.additionalContext`
(feedback sin bloquear) y `systemMessage`, y es más explícita. Salidas relevantes:

```json
{"decision":"block","reason":"Demuestra X antes de cerrar"}
{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"..."}}
{"continue":false,"stopReason":"..."}   // parar del todo (precedencia sobre decision)
```

Cualquier otro exit code ≠ 0/2 = error no bloqueante (se ignora el bloqueo).
**Regla de robustez: ante cualquier excepción interna, salir con 0 y no
bloquear** — un bug del hook jamás debe secuestrar la sesión.

## 4. Anti-bucle infinito

Un Stop hook que bloquea provoca que Claude siga trabajando y vuelva a parar →
el hook se dispara otra vez. Defensas en capas:

1. **`stop_hook_active`**: si viene `true`, ya estamos en un ciclo de
   continuación forzada por stop hook. No basta por sí solo (queremos permitir
   1–2 reintentos), pero se registra.
2. **Contador propio de reintentos por sesión**: fichero de estado
   `~/.claude/proofgate/state/<session_id>.json` con `{"blocks": N}`.
   Si `N >= MAX_BLOCKS` (por defecto **2**), NO se bloquea más: se deja cerrar
   y el informe marca las afirmaciones como "no verificadas (límite de
   reintentos alcanzado)".
3. **Bloquear solo con hallazgos nuevos**: se guarda el hash de las
   afirmaciones no respaldadas; si el nuevo bloqueo repetiría exactamente las
   mismas, se cuenta igual contra el límite (evita ping-pong estéril).
4. Fail-open: cualquier error de parsing/IO → exit 0.

## 5. Empaquetado como plugin

Estructura (verificada en doc oficial — `plugin.json` va DENTRO de
`.claude-plugin/`, todo lo demás en la raíz del plugin):

```
proofgate/
├── .claude-plugin/plugin.json    # {"name","description","version","author"}
├── hooks/hooks.json              # {"hooks":{"Stop":[{"hooks":[{"type":"command","command":"..."}]}]}}
├── skills/proofgate/SKILL.md     # frontmatter: description; se invoca /proofgate:proofgate
└── scripts/…                     # el hook en sí; referenciado con ${CLAUDE_PLUGIN_ROOT}
```

- En `hooks.json` el comando usa `${CLAUDE_PLUGIN_ROOT}` para rutas absolutas
  dentro del plugin instalado.
- Test local: `claude --plugin-dir ./proofgate` y `/reload-plugins`.
- Marketplace: repo GitHub con `.claude-plugin/marketplace.json`:

```json
{
  "name": "proofgate-marketplace",
  "owner": {"name": "Andres"},
  "plugins": [
    {"name": "proofgate", "source": "./proofgate", "description": "..."}
  ]
}
```

Instalación por el usuario:
`/plugin marketplace add <user>/<repo>` (o ruta local) →
`/plugin install proofgate@proofgate-marketplace`.

## 6. Llamada opcional a Haiku

CLI `claude` disponible en el sistema → se puede usar
`claude -p --model claude-haiku-4-5-20251001` sin gestionar API keys, o la API
directa si hay `ANTHROPIC_API_KEY`. En v1 será **opcional y off por defecto**
(`PROOFGATE_USE_HAIKU=1` para activar): las heurísticas cubren el MVP y la
llamada añade latencia. Decisión anotada en LOG.md.
