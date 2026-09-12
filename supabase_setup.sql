-- Ejecutar esto en el SQL Editor de Supabase
-- Crea la tabla donde se guarda el estado completo de la liga (resultados).

create table if not exists elite_state (
  id        text primary key,            -- siempre 'state' (fila unica)
  data      jsonb not null,              -- {matches, playoffs}
  updated_at timestamptz not null default now()
);

-- La app usa la service_role key (autorizacion bearer + apikey), que ignora RLS.
-- Estas politicas son opcionales por si prefieres usar anon key despues.
alter table elite_state enable row level security;

create policy "state es publico de lectura" on elite_state
  for select using (true);

create policy "cualquiera puede escribir" on elite_state
  for insert with check (true);

create policy "cualquiera puede actualizar" on elite_state
  for update using (true);