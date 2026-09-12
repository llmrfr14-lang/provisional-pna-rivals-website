# Elite Division — sitio provisional

Sitio web para el torneo de Rocket League **Elite Division** (2 grupos x 4 equipos,
todos contra todos, 3 pts por victoria; top 2 de cada grupo a semis y final).

- UI en español, sin dependencias (Python stdlib + JS vanilla, sin CDN).
- Cualquiera con el link puede ver resultados y tabla de posiciones, y reportar partidos.
- **Standings y brackets (semis + final) se generan automáticamente** desde los
  resultados reportados — nunca se guardan, siempre se calculan.

## Archivos

| Archivo              | Que es |
|----------------------|--------|
| `app.py`             | Servidor HTTP (stdlib). Endpoints `/`, `/api/state`, `/api/report`, `/api/undo` |
| `static/index.html`  | Frontend de una sola pagina (tabs: Reportar / Grupos / Calendario / Playoffs / Equipos) |
| `config.json`        | Config: grupos, rosters, puntos por victoria |
| `data.json`          | Espejo local de resultados (se regenera solo) |
| `supabase_setup.sql` | SQL para crear la tabla `elite_state` una sola vez |
| `.env`               | Credenciales de Supabase (NO versionar) |

## Como correr

```bash
python3 app.py            # Puerto por defecto 12000
PORT=8080 python3 app.py  # Puerto custom
```

La app lee `SUPABASE_URL` y `SUPABASE_SERVICE_KEY` de las variables de entorno o
del archivo `.env`. Si Supabase no esta disponible, guarda igual en `data.json`.

## Supabase

Los resultados se guardan en la tabla `elite_state` (fila unica `id='state'` con
`data` jsonb = `{matches, playoffs}`). Para crearla, correr `supabase_setup.sql`
en el SQL Editor del proyecto. La app usa la `service_role` key via REST API.