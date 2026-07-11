# LOG.md — ProofGate

## 2026-07-11 — Fix crítico: Haiku nunca se ejecutaba (binario fuera del PATH del hook)

### Diagnóstico (causa raíz)

`_run_haiku` invocaba `subprocess.run(["claude", ...])`. `subprocess` con lista
de argumentos NO usa shell y busca el binario únicamente en el `PATH` del
proceso actual. En una terminal interactiva el hook heredaría el PATH completo
del shell y funciona; pero un hook lanzado por Claude Code corre con un PATH
mínimo. En esta máquina `claude` vive en `~/.local/bin/claude` (instalación por
usuario, symlink a `~/.local/share/claude/versions/2.1.207`), que no está en
`/usr/bin:/bin`. Reproducción confirmada:

    env -i HOME="$HOME" PATH="/usr/bin:/bin" python3 -c "...claims._run_haiku(...)"
    -> None   (FileNotFoundError capturado -> fallback a regex)

Consecuencia: en producción real el extractor PRIMARIO (Haiku) nunca corría;
el hook caía SIEMPRE al fallback de regex, que es justo el que no cubre "creé".
El bug #3 parecía arreglado pero seguía roto fuera de la suite.

### Por qué la suite no lo cazó

Los tests de `test_haiku.py` inyectan un `haiku_extractor` fake o testean
`_parse_haiku_json` con strings; **nunca ejercitan la resolución real del
binario ni `subprocess.run`**. El mock entra antes de que se toque el PATH, así
que la ruta que fallaba en producción no estaba cubierta por ningún test. Además
`_run_haiku` devolvía `None` idéntico tanto para "binario no encontrado" como
para "Haiku corrió y no vio nada", haciendo el fallo invisible e indistinguible
desde el log — de ahí lo costoso del diagnóstico.

### Fix

1. `_resolve_claude_bin()`: resuelve la ruta en este orden — (a)
   `PROOFGATE_CLAUDE_BIN` explícito, (b) `shutil.which("claude")` con el PATH
   real, (c) lista de rutas de instalación por usuario conocidas
   (`~/.local/bin/claude`, `~/.claude/local/claude`, `/opt/homebrew/bin`,
   `/usr/local/bin`, `/usr/bin`), comprobando fichero + permiso de ejecución.
2. `_run_haiku` usa esa ruta absoluta en vez de `"claude"`.
3. Si no se encuentra, se registra en el LOG DE SESIÓN normal (vía `log()`,
   no en errors.log) el motivo exacto ("binario no encontrado"), distinguible
   de "Haiku corrió y no detectó nada", "timeout", "salida vacía" y "salida no
   parseable". `extract_claims` acepta un `logger` que `proofgate_stop` conecta
   a `log(session_id, ...)`.
4. Si aun así no hay binario → fallback a regex (comportamiento correcto, sin
   cambios), pero ahora con rastro en el log.

### Resultado suite

`test_haiku: OK` (ahora 11 casos; los 4 nuevos ejercitan resolución real del
binario + subprocess con un `claude` falso, override por env, y el log
explícito del motivo) · `test_claims: OK` · `test_verifiers: OK` ·
`test_blocking: OK`.

### Verificación en vivo (la que faltaba: ejercitar la ruta real del binario)

Con PATH restringido a `/usr/bin:/bin` (sin `~/.local/bin`) pero entorno de auth
intacto — fiel a cómo un hook hereda el entorno — `_resolve_claude_bin()`
devuelve `/Users/andre/.local/bin/claude` y `_run_haiku(...)` sobre "Cree
hello.py ... test_saludo pasa" devolvió `file_created`/`hello.py` y `test_pass`
con `names=["test_saludo"]`. Antes del fix, ese mismo PATH daba
FileNotFoundError → None → regex. Bug resuelto en la ruta real.

Nota: bajo `env -i` (entorno totalmente limpio) el binario ya se resuelve, pero
Haiku responde "Not logged in" porque `env -i` borra el contexto de auth; eso
es artefacto del test, no del entorno real del hook, y de todos modos cae
limpiamente a regex (salida sin `[...]` → no parseable → fallback).

## 2026-07-11 — Cambio de diseño: Haiku como extractor primario, regex de fallback

### Diagnóstico de fondo

El síntoma reportado ("Creé hello.py..." → `extract_claims()` devuelve `[]`)
es porque `_CREATE_VERBS` cubre `creado/creada/he creado` pero no el pretérito
`creé`. Pero el problema no es esa conjugación concreta: es que mantener listas
de regex exhaustivas para ES+EN, todas las conjugaciones y sinónimos, es una
guerra perdida — cada prueba nueva encuentra otra forma que se cuela.
"¿Qué afirma este mensaje en lenguaje natural?" es justo la tarea donde un
modelo generaliza mejor que una lista fija.

### Plan de cambio (invertir la prioridad)

1. **Haiku pasa a ser el extractor PRINCIPAL, ON por defecto.** El prompt
   (`_HAIKU_PROMPT`) se amplía para (a) detectar cualquier idioma/tiempo verbal
   (incluye "Creé"/"He creado"/"I created" como file_created) y (b) devolver
   también `names` para afirmaciones test_pass, que antes solo sacaba el regex.
2. **Regex pasa a ser FALLBACK.** Se usa solo si Haiku falla/timeout/no hay CLI.
   Para distinguir "Haiku corrió y no vio nada" (`[]`, se respeta) de "Haiku
   falló" (`None`, se cae a regex), `_haiku_claims` devuelve `None` en fallo.
3. **Verificadores sin cambios**: siguen deterministas (sha, exit codes,
   contenido de archivo). Esto es solo sobre CÓMO se extrae, no cómo se verifica.
4. **Timeout explícito** de Haiku: 18 s (`PROOFGATE_HAIKU_TIMEOUT`), dentro del
   presupuesto de 60 s del hook, dejando margen para el fallback + verificación.
5. **`PROOFGATE_NO_HAIKU=1`** fuerza solo-regex (más rápido, sin red).

### Cómo lo hago testeable sin red real

- `extract_claims(message, *, haiku_extractor=None)`: punto de inyección. Los
  tests pasan un `haiku_extractor` fake que simula la respuesta de Haiku (lista
  = éxito, `None` = fallo → fallback, `raise` → fallback). Cero llamadas de red.
- La lógica de subproceso (`_run_haiku`) se separa del parseo
  (`_parse_haiku_json`), que se testea directamente con un string JSON de
  ejemplo (incluye el caso "Creé hello.py" y `names`).
- Los tests de regresión YA EXISTENTES (nombre no vinculado, negación
  multi-cláusula) fijan `PROOFGATE_NO_HAIKU=1` para validar de forma
  determinista que el FALLBACK de regex sigue cubriéndolos. La cobertura de
  esos casos por Haiku se garantiza a nivel de prompt (instrucciones
  explícitas) y se puede comprobar en vivo manualmente, pero la suite no
  depende de ello para no volverse lenta/frágil.
- **Decisión**: NO se extiende el regex para cubrir "creé". El objetivo del
  cambio es dejar de perseguir conjugaciones; Haiko lo cubre por prompt. El
  modo solo-regex (NO_HAIKU) mantiene el hueco a sabiendas, es la elección
  del usuario que prioriza velocidad/offline.

### Resultado suite

`test_haiku: OK` (7 casos: parseo con names, regresión "Creé hello.py" vía
Haiku, fallback por None/excepción/JSON no parseable, respeto de `[]`,
NO_HAIKU fuerza regex, y el fallback conserva los casos que el regex ya
cubría) · `test_claims: OK` · `test_verifiers: OK` · `test_blocking: OK`.
Los tres tests preexistentes fijan `PROOFGATE_NO_HAIKU=1` para validar el
fallback de forma determinista y sin red.

### Verificación en vivo (fuera de la suite)

`_run_haiku("Creé hello.py ... Los tests test_saludo pasan.")` real: 6.6 s de
latencia, devolvió `file_created`/`hello.py` y `test_pass` con
`names=["test_saludo"]`. Confirma prompt + parseo + CLI de punta a punta y que
el bug reportado queda resuelto en uso real.

### Nota de latencia (trade-off de llamar a Haiku en cada Stop)

Medido ~6-7 s por llamada; timeout 18 s; presupuesto del hook 60 s → margen
amplio incluso con fallback. Mitigaciones ya presentes: timeout explícito,
fallback a regex, `PROOFGATE_NO_HAIKU` para modo offline. Posibles mejoras v2
si molesta: (a) no llamar a Haiku si el regex ya encontró afirmaciones y el
mensaje es corto/inequívoco; (b) cachear por hash del mensaje para no repetir
en reintentos de bloqueo del mismo turno; (c) short-circuit si el mensaje no
contiene ninguna palabra "gatillo" (tests/commit/push/creado/…). No
implementadas ahora para no añadir complejidad prematura.

## 2026-07-11 — Fix: negación en otra cláusula silenciaba la afirmación entera

### Diagnóstico (causa raíz)

`_sentences()` solo corta por saltos de línea y por puntuación de fin de frase
(`.!?`), no por `:` ni `,`. El guard de negación estaba en `extract_claims`
como `if _NEGATION.search(sentence): continue`, aplicado sobre esa "frase"
gruesa completa. Resultado: si una frase con varias cláusulas separadas por
`:`/`,` contenía una palabra de negación en CUALQUIER cláusula (aunque fuera
distinta de la que lleva la afirmación real), toda la frase se descartaba. La
afirmación verdadera nunca llegaba a `_PATTERNS`: ni se detectaba, ni se
verificaba, ni salía en el TRUST_REPORT. Silencio total.

Caso reportado: "Los tests siguen pasando: ya ejecutamos pytest anteriormente
en esta sesión y el cambio es pequeño, no debería haber roto nada." → `no
debería` disparaba `_NEGATION` sobre el texto entero → `extract_claims()`
devolvía `[]`.

### Fix aplicado (enfoque elegido y por qué)

Elegido el **enfoque de ventana local** (opción 2), no el de partir por `:`/`,`
(opción 1). Razón del trade-off: los patrones de `_PATTERNS` dependen de
contexto multi-cláusula (`\btests?\b.{0,60}\b(pass...)` con `re.DOTALL`), de
modo que "tests" y "pass" pueden estar separados por una coma dentro de la
misma afirmación; partir la frase por comas rompería esas detecciones
legítimas. La ventana local preserva el matching positivo sobre la frase
completa y solo acota la comprobación de negación.

- Nueva `_negated_near(sentence, start, end)`: busca negación únicamente en la
  cláusula que contiene el match — desde el separador (`: ; ,`) anterior al
  match hasta el posterior. Si el match abarca varias cláusulas, la negación
  dentro de ese tramo sí cuenta.
- El bucle de `extract_claims` ya no hace `continue` por frase. Itera los
  matches de cada patrón (`finditer`) y añade la afirmación salvo que
  `_negated_near` sea cierto. Para archivos, la negación se acota igual
  alrededor del verbo (create/modify), preservando la selección previa.

### Tests de regresión (en `tests/test_claims.py`)

a. El caso exacto reportado + variantes con `,`/`;` y negación fuera de la
   cláusula afirmada → SÍ se detecta la afirmación.
b. Negación genuina en la misma cláusula ("Los tests no pasan.", "The tests
   are not passing yet.", "No he hecho el commit todavía.") → sigue sin
   generar claim. Confirma que no se rompió la detección de negaciones reales.

### Suite completa tras el fix

`test_claims: OK` · `test_verifiers: OK` · `test_blocking: OK` (6/6). Nada roto.

### Confirmación del hueco temporal ya conocido (NO se arregla aquí)

Como pedía el encargo, verifico que una vez arreglada la detección, el hueco
test↔edición (pendiente de v2) sigue presente: con la frase corta y sin
negaciones "Los tests pasan." sobre un transcript donde el `pytest -q` exitoso
ocurrió ANTES de un `Edit` a `core.py`, el verificador genérico devuelve
`VERIFIED` (`pytest -q ejecutado con éxito (exit 0)`) cuando en realidad el
test podría fallar ahora. Es un problema distinto y posterior; queda como está,
anotado abajo como pendiente de v2. Este fix solo garantiza que la afirmación
se DETECTE; su veredicto correcto ante ediciones posteriores es harina de otro
costal.

## 2026-07-10 — Fix: verificador de test_pass no vinculaba nombre concreto

### Diagnóstico (causa raíz)

- `claims.py`: un `Claim` de tipo `test_pass` solo guardaba el tipo genérico.
  El nombre concreto citado en la afirmación (`test_multiplica`, `multiplica`)
  se descartaba en la extracción.
- `verifiers.py`: `verify_claims` para `test_pass` llamaba a
  `_verify_command(_TEST_CMD)`, que devolvía `VERIFIED` en cuanto **el último**
  comando de test del transcript tenía `is_error == False` — sin mirar **qué**
  se testeó ni el contenido del output. Por eso "test_multiplica PASSED" se
  marcaba VERIFIED con solo tener un `pytest` exitoso sobre suma/resta.

### Fix aplicado

1. `claims.py`: `Claim` gana el campo `names: list[str]`. Nueva función
   `_test_names()` que, solo en frases `test_pass`, captura nombres concretos
   con tres patrones: identificadores estilo pytest (`test_\w+`, `\w+_test`,
   `TestX`), símbolo tras palabra clave ("función/function/método/method/
   clase/class NAME") y llamada `NAME(` con paréntesis pegado. `_FUNC_CALL`
   exige el paréntesis SIN espacio detrás del identificador para no capturar
   prosa como "los tests pasan (12 passed)". Stopwords (`test`, `suite`, …)
   excluidas. `add()` ahora acumula nombres si la misma afirmación aparece en
   varias frases.
2. `verifiers.py`: nuevo `_verify_test_pass()`. Sin nombres → comportamiento
   previo (último test exitoso = VERIFIED). Con nombres → además exige que
   CADA nombre aparezca en la salida de algún comando de test exitoso
   (evidencia de que `pytest -v` listó ese test); si falta alguno → UNSUPPORTED.
   El último run fallido sigue dando CONTRADICTED.
3. Caso genérico intacto: sin nombre citado, no se endurece nada.
4. Regresión en `tests/test_verifiers.py` + fixtures `named_mismatch.jsonl`
   (suma/resta OK, afirmación sobre multiplica → UNSUPPORTED) y `named_ok.jsonl`
   (test_multiplica en la salida → VERIFIED).

### Decisiones de producto anotadas

- **Verificamos vía salida del test, no con grep del código fuente.** Exigir
  que exista la función en el código sería otro verificador (más ruidoso y con
  falsa confianza sobre "existe ≠ testeada"). El requisito pide evidencia de
  que ESE test corrió, y la salida de `pytest -v` es justo eso.
- **Coincidencia por subcadena, no por límite de palabra**, para que
  "función multiplica" (afirmación) case con `test_multiplica` (salida). Riesgo
  asumido: un nombre que sea subcadena de otro (`add` en `test_address`) podría
  falso-verificar; aceptable para el MVP.
- **Consecuencia UX**: si el agente cita un test concreto pero corrió `pytest`
  sin `-v` (salida sin nombres), la afirmación queda UNSUPPORTED. Es el
  comportamiento correcto según el requisito; mitigación: correr `pytest -v`.

### Suite completa tras el fix

`test_claims: OK` · `test_verifiers: OK` (incluida la regresión) ·
`test_blocking: OK` (6/6). Nada roto. El caso honesto de `test_blocking`
("los tests pasan (12 passed)") confirma empíricamente que "(12 passed)" no
se captura como nombre espurio.

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
