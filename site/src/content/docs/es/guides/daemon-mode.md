---
title: Modo Daemon
description: Ejecuta Hive como un daemon de fondo de único dueño con auto-actualización.
---

Por defecto Hive corre como un **servidor stdio por sesión**: tu cliente MCP
lanza `uvx hive-vault` en cada sesión y lo cierra al terminarla. Es el modo más
simple y no necesita nada de esta página.

El **modo daemon** es una mejora opcional. En lugar de un servidor por sesión,
un único proceso `hive serve` de larga vida es el dueño del vault, y cada sesión
se conecta a través de un shim ligero `hive client` que hace de proxy. Merece la
pena activarlo cuando ejecutas varias sesiones concurrentes en una máquina,
quieres las garantías de único dueño de
[ADR-011](https://github.com/mlorentedev/hive/blob/master/docs/adr/adr-011-phase-c-daemon-model.md),
o quieres que los clientes adopten siempre la última versión publicada de forma
automática.

:::note[Siempre degrada, nunca rompe]
Si el daemon está ausente o no responde, `hive client` recurre a un **servidor
in-process** — el mismo código que ejecuta el modo stdio. Un daemon caído te
cuesta las ventajas de único dueño, no tu sesión.
:::

## Los dos procesos

| Comando | Rol |
|---|---|
| `hive serve` | El daemon. Sirve MCP sobre streamable-HTTP en loopback, protegido con bearer token. Único dueño del git + SQLite del vault por máquina (garantizado por un lock singleton). |
| `hive client` | Un shim stdio ligero que lanza tu cliente MCP. Hace de proxy hacia un `hive serve` en marcha; si no hay ninguno alcanzable, recurre a un servidor in-process. |
| `hive service` | Instala/elimina el supervisor del SO que mantiene `hive serve` en marcha. Ver abajo. |

En modo daemon tu cliente MCP se registra con `hive client` en lugar de
`uvx hive-vault` — el proceso cliente es barato de lanzar y no añade latencia de
carga del vault al inicio de sesión.

## Activarlo

`hive service` integra `hive serve` en el supervisor de tu SO para que arranque
al iniciar sesión y se reinicie ante fallos. Es multiplataforma: **systemd
`--user`** en Linux, **Task Scheduler** en Windows.

```bash
uv tool install --upgrade hive-vault   # requiere hive-vault >= 1.32.0
hive service install                   # renderiza unit/task, habilita, arranca
hive service status                    # vista del supervisor (active/running)
```

- **Linux** escribe `~/.config/systemd/user/hive.service`
  (`Restart=on-failure`, `WantedBy=default.target`), y luego ejecuta
  `systemctl --user daemon-reload && enable --now`.
- **Windows** registra la tarea programada `HiveVaultDaemon`
  (`LogonTrigger` + `RestartOnFailure`).

Usa `hive service install --no-enable` para escribir el unit/task sin
arrancarlo, y `hive service uninstall` para detenerlo y eliminarlo.

El último paso es apuntar tu cliente MCP al daemon — cambia la entrada `hive` de
`uvx hive-vault` a `hive client`. El procedimiento completo por máquina
(verificar que el daemon sirve, la edición quirúrgica de `~/.claude.json`, y el
rollback) está en el [runbook de Activación del Daemon](https://github.com/mlorentedev/hive/blob/master/docs/runbooks/daemon-activation.md).

## Auto-actualización: reinicio-al-actualizar

Una vez supervisado, el daemon se mantiene al día. Sondea su propia versión
instalada y, cuando hay un `hive-vault` más nuevo en disco, **sale con código
`75` (`EX_TEMPFAIL`)** para que la política `Restart=on-failure` del supervisor
lo relance con el nuevo código. Sin reinicio manual, y sin añadir latencia al
arranque del cliente.

```
uv tool upgrade hive-vault        # la nueva versión aterriza en disco
        │
        ▼
hive serve detecta el drift       # dentro de HIVE_UPGRADE_POLL_S
        │
        ▼
exit 75  ──►  el supervisor reinicia  ──►  el daemon corre la nueva versión
```

Así, el *mecanismo* de actualización vive en el paquete; **cada cuánto
actualizas es una política de despliegue** que tú controlas — p. ej. un
`uv tool upgrade hive-vault` periódico (un timer de systemd `--user` o una tarea
programada de Windows). El daemon adopta lo que haya en disco en su siguiente
sondeo.

Una parada por señal (graceful) o una instalación rechazada salen con `0`, que
bajo `Restart=on-failure` **no** dispara un relanzamiento — solo el camino de
drift devuelve el código de reinicio distinto de cero. Las escrituras en vuelo
son seguras ante un reinicio porque son idempotentes at-most-once
([ADR-013](https://github.com/mlorentedev/hive/blob/master/docs/adr/adr-013-write-idempotency-at-most-once.md)).

### Ajustar el intervalo de sondeo

| Variable | Por defecto | Descripción |
|---|---|---|
| `HIVE_UPGRADE_POLL_S` | `30.0` | Segundos entre comprobaciones de drift de versión en `hive serve`. Menor = el daemon adopta una versión nueva antes tras una actualización; mayor = menos búsquedas `importlib.metadata`. Validado `> 0` y `<= 3600`. |

Defínela en el entorno del daemon (en Linux, vía `Environment=` /
`EnvironmentFile=` del unit; en Windows, el entorno del perfil de usuario que
hereda la tarea programada).

## Verificar el daemon

El daemon escribe su puerto y bearer token en el directorio de estado de Hive
(solo-dueño, `600`):

```bash
PORT=$(cat ~/.local/share/hive/daemon.port)
TOKEN=$(cat ~/.local/share/hive/daemon.token)

curl -s "http://127.0.0.1:$PORT/health"                                     # {"status":"ok","ready":true,...}
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/status"    # 401 (token-gated)
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/status"   # 200 + métricas
```

`/status` expone `sessions_started`, `total_calls` y la versión en marcha —
útil para confirmar que un reinicio-al-actualizar adoptó de verdad el nuevo
código.

## Cuándo quedarse en stdio

El modo daemon es opt-in. Quédate en `uvx hive-vault` si ejecutas una sola
sesión a la vez, no quieres procesos de fondo, o estás en una plataforma sin
supervisor soportado — el servidor por sesión es completo y usa el mismo código
de vault y workers. Puedes cambiar más tarde en cualquier momento sin migración
de datos: el daemon y el fallback in-process comparten las mismas rutas de git y
store SQLite del vault.
