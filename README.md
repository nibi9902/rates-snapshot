# pricelabs-snapshot

Snapshot diari de preus i estades mínimes de PriceLabs cap a Supabase
(`pricelabs_snapshots` + vista `v_pricelabs_changes`), per verificar que els
preus s'han sincronitzat correctament als apartaments.

## Com funciona

1. Login HTTP al formulari de `pricelabs.co/signin` (sense navegador).
2. `GET app.pricelabs.co/multicalendar?startDate=...&endDate=...` — les dades
   van incrustades a l'HTML com a payload RSC de Next.js.
3. Parseig (`extract.py`) i upsert a Supabase sobre
   `(snapshot_date, listing_id, stay_date)` — re-executar no duplica.

## Variables d'entorn

| Variable | Descripció |
|---|---|
| `PRICELABS_EMAIL` | email del compte PriceLabs |
| `PRICELABS_PASSWORD` | contrasenya |
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | service role key |
| `RUN_AT` | hora diària UTC `HH:MM` (buit = executa un cop i surt) |
| `SCRAPE_DAYS` | dies vista a capturar (defecte 360) |

## Execució local (sense Docker)

Credencials a `~/.pricelabs.env` i:

```bash
set -a; source ~/.pricelabs.env; set +a
python3 push_to_supabase.py
```

## Docker / EasyPanel

App nova des d'aquest repo (Build: Dockerfile). Amb `RUN_AT=05:00` el
contenidor queda en marxa i executa cada dia a les 05:00 UTC (07:00 CEST),
just després del push nocturn de PriceLabs (~03:00 UTC).
