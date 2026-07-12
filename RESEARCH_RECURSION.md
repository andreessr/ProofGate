# RESEARCH_RECURSION.md — Recursión del Stop hook vía la sub-llamada a Haiku

Fecha: 2026-07-11. Estado: mecanismo confirmado empíricamente en esta máquina.

## Mecanismo exacto (confirmado)

1. ProofGate está instalado en **scope `user`** (`claude plugin list` →
   `Scope: user`, habilitado en `~/.claude/settings.json`). Eso significa que
   **cualquier** invocación de `claude` de este usuario carga el plugin y sus
   hooks — incluido `claude -p`, e incluido el propio subprocess con el que el
   hook llama a Haiku.
2. Cadena: sesión de usuario termina turno → Stop hook (ProofGate) →
   `_run_haiku()` lanza `claude -p --model haiku …` → esa sub-sesión también
   carga ProofGate → al terminar su único turno dispara SU PROPIO Stop hook →
   que vuelve a lanzar `claude -p …` → etc.
3. Por qué no es un bucle infinito *hoy*: cada nivel N+1 tarda más de los 18 s
   de `PROOFGATE_HAIKU_TIMEOUT` del nivel N (porque a su vez espera a su
   propio hijo), así que cada nivel mata a su hijo por timeout y cae a regex.
   El corte es **accidental** (por agotamiento), no por diseño.

## Medición del daño (baseline, antes del fix)

- `claude -p --model claude-haiku-4-5-20251001 "di solo la palabra hola"`:
  **20,4 s** para devolver una palabra, y generó **6 logs de sesión** de
  ProofGate casi simultáneos (6 session_id distintos), todos con el patrón
  `Haiku no ejecutado: timeout tras 18.0s`. Es decir: varios niveles de
  anidamiento en cascada/paralelo antes de que cada uno se cortara.
- Escenarios donde el corte accidental podría NO funcionar o degradarse más:
  - Si un nivel responde *justo por debajo* del timeout, la cadena puede
    profundizar más niveles (cada uno suma su propia latencia completa).
  - Si el usuario sube `PROOFGATE_HAIKU_TIMEOUT`, la cascada crece linealmente
    con el valor y el número de niveles.
  - Un Stop hijo podría incluso **bloquear** (`decision: "block"`) la
    sub-sesión de Haiku si la salida JSON de Haiku aparenta afirmaciones sin
    evidencia — comportamiento absurdo y difícil de diagnosticar.
  - Coste económico: cada nivel es una sesión real de API facturable.

## Vías de prevención evaluadas

### (a) Flags/config del CLI para no cargar hooks en la sub-llamada

- **`--bare`**: documentado como "skip hooks, LSP, plugin sync…". PERO fuerza
  auth estricta por `ANTHROPIC_API_KEY` (nunca lee OAuth/keychain). En esta
  máquina la auth del CLI es OAuth → `--bare` rompería la llamada a Haiku.
  Descartado como mecanismo único.
- **`--settings '{"disableAllHooks": true}'`**: ajuste documentado
  (`disableAllHooks`) inyectado por CLI; mantiene la auth normal. En la prueba
  A el tiempo bajó de 20,4 s → 3,0 s (los 2 logs que aparecieron eran
  rezagados del baseline por timestamp/cwd). Verificación limpia pendiente de
  límite de sesión del plan; se adopta como capa complementaria.
- **`--setting-sources ""`**: no carga settings de user/project/local, lo que
  debería evitar plugins de usuario por completo. Efecto colateral: también
  ignora el resto de configuración del usuario. Menos quirúrgico que
  `disableAllHooks`; se descarta en favor de éste.

### (b) Guarda por variable de entorno (elegida como defensa principal)

`_run_haiku` exporta `PROOFGATE_INSIDE_HAIKU_CALL=1` en el entorno del
subprocess. Los hooks heredan el entorno del proceso `claude` que los lanza,
así que si un Stop hook se dispara dentro de esa sub-sesión,
`proofgate_stop.py` ve la variable y **sale inmediatamente con exit 0**, antes
de leer stdin, sin transcript, sin extracción, sin Haiku. Corte determinista
en el primer nivel: no depende de timeouts, ni de versión del CLI, ni de que
`disableAllHooks` siga existiendo/funcionando.

### (c) ¿Reutilizar PROOFGATE_DISABLED=1?

Funcionaría mecánicamente (mismo early-exit), pero se descarta: conflaría en
logs y diagnóstico "el usuario desactivó ProofGate a mano" con "esto es una
sub-llamada interna", y un usuario que tuviera `PROOFGATE_DISABLED` en su
entorno por otra razón no distinguiría los casos. Variable nueva y específica
= intención explícita.

## Decisión final: defensa en capas

1. **Capa 1 (determinista, principal)**: `PROOFGATE_INSIDE_HAIKU_CALL=1` en el
   env del subprocess + early-exit en `proofgate_stop.py`. Garantiza el corte
   aunque todo lo demás falle.
2. **Capa 2 (causa raíz, rendimiento)**: `--settings '{"disableAllHooks":
   true}'` en la sub-llamada. Evita cargar/ejecutar CUALQUIER hook en la
   sub-sesión (no solo el nuestro), y reduce la latencia de la llamada a Haiku.
3. El early-exit de la capa 1 deja una línea en
   `~/.claude/proofgate/logs/recursion-guard.log` (best-effort) para que la
   activación de la guarda sea observable, no silenciosa.

## Auditoría de otros caminos de reentrada

- `_run_haiku` es el ÚNICO sitio del código que lanza `claude` como
  subprocess (verificado por grep). No hay otros exec/subprocess a `claude`.
- `PROOFGATE_NO_HAIKU=1` en el hijo no habría bastado: evitaría el nieto,
  pero el hook hijo seguiría corriendo extracción/verificación y podría
  bloquear la sub-sesión.
- El fake de tests (`tests/test_haiku.py`) no invoca `claude` real.
- La skill (`SKILL.md`) no ejecuta `claude` recursivamente.
