# Elite Division — sitio provisional

Sitio web para el torneo de Rocket League **Elite Division** (2 grupos x 4 equipos,
todos contra todos, 3 pts por victoria; top 2 de cada grupo a semis y final).

- UI en español, sin dependencias (Python stdlib + JS vanilla, sin CDN).
- Cualquiera con el link puede ver resultados y tabla de posiciones, y reportar partidos.
- Los resultados reportados quedan **pendientes de aprobación**: recién cuentan para
  la tabla y para playoffs cuando un admin los aprueba (código secreto vía `.env`).
- **Standings y brackets (semis + final) se generan automáticamente** desde los
  resultados aprobados — nunca se guardan en el estado.

## Archivos

| Archivo              | Que es |
|----------------------|--------|
| `app.py`             | Servidor HTTP (stdlib). Endpoints `/`, `/api/state`, `/api/report`, `/api/undo`, `/api/admin/verify`, `/api/admin/approve`, `/api/admin/reject` |
| `static/index.html`  | Frontend de una sola pagina (tabs: Reportar / Grupos / Calendario / Playoffs / Equipos / Admin) |
| `config.json`        | Config: grupos, rosters, puntos por victoria |
| `data.json`          | Espejo local de resultados (se regenera solo) |
| `supabase_setup.sql` | SQL para crear la tabla `elite_state` una sola vez |
| `.env`               | Credenciales de Supabase + `ADMIN_CODE` (NO versionar) |

## Como correr

```bash
python3 app.py            # Puerto por defecto 12000
PORT=8080 python3 app.py  # Puerto custom
```

La app lee `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` y `ADMIN_CODE` de las variables
de entorno o del archivo `.env`. Si Supabase no esta disponible, guarda igual en
`data.json`.

## Aprobación de resultados

- `POST /api/report` con `{match_id, winner, reporter}` guarda el resultado con
  `status: "pending"`. No afecta la tabla hasta que se apruebe.
- `POST /api/admin/verify`  `{admin_code}` → valida el código de administrador.
  El panel Admin exige este paso **antes** de mostrar los botones de aprobar/rechazar.
- `POST /api/admin/approve`  `{match_id, admin_code}` → lo marca `approved` y
  recién ahí suma puntos / avanza en el bracket.
- `POST /api/admin/reject`   `{match_id, admin_code}` → lo desmarca (se puede volver
  a reportar).
- Definí `ADMIN_CODE=...` en `.env` (o env var). Sin él, el panel Admin muestra error.

## API en resumen

| Método | Ruta                 | Body                                  |
|--------|----------------------|---------------------------------------|
| GET    | `/api/state`         | —                                     |
| POST   | `/api/report`        | `{match_id, winner, reporter}`        |
| POST   | `/api/undo`          | `{match_id}`                          |
| POST   | `/api/admin/verify`  | `{admin_code}`                        |
| POST   | `/api/admin/approve` | `{match_id, admin_code}`              |
| POST   | `/api/admin/reject`  | `{match_id, admin_code}`              |

El campo `status` de cada partido puede ser `null`, `"pending"` o `"approved"`.

## Supabase

Los resultados se guardan en la tabla `elite_state` (fila unica `id='state'` con
`data` jsonb = `{matches, playoffs}`). Para crearla, correr `supabase_setup.sql`
en el SQL Editor del proyecto. La app usa la `service_role` key via REST API.

## Dos hosts (puertos 12000 y 12001)

El sitio se sirve por dos URLs públicas, cada una mapeada a un puerto:

- `work-1` → puerto `12000` (default)
- `work-2` → puerto `12001` (levantar con `PORT=12001 python3 app.py`)

Ambos comparten el mismo estado (Supabase / `data.json`).

## No caerse (auto-reinicio)

`watchdog.sh` monitorea ambos puertos cada 10s y reinicia el server que no
responda, aguantando crashes, cuelgues y reinicios del contenedor. Los servers
escriben su PID en `/tmp/elite_12000.pid` / `/tmp/elite_12001.pid` para que el
watchdog sepa cuál proceso corresponde a cada puerto.

- **Ordenar todo de cero** (mata instances viejas y levanta servers + watchdog):

  ```bash
  ./start_all.sh
  ```

- Logs: `server.log` (12000), `server2.log` (12001), `watchdog.log` (acciones del watchdog).

Si una URL da "Bad Gateway", esperá ~15s: el watchdog ya debería haber reiniciado
ese puerto. Si sigue caída, corré `./start_all.sh`.