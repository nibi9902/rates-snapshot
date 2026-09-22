#!/bin/sh
# Sense RUN_AT: executa un cop i surt (per cron extern).
# Amb RUN_AT=HH:MM (UTC): dorm fins a aquella hora cada dia i executa.
set -e

if [ -z "$RUN_AT" ]; then
  exec python3 push_to_supabase.py
fi

echo "Mode dimoni: execució diària a les $RUN_AT UTC"
while true; do
  now=$(date -u +%s)
  target=$(date -u -d "today $RUN_AT" +%s 2>/dev/null || date -u -j -f "%H:%M" "$RUN_AT" +%s)
  if [ "$target" -le "$now" ]; then
    target=$((target + 86400))
  fi
  sleep $((target - now))
  echo "[$(date -u)] Executant snapshot..."
  # El codi de sortida es registra sempre: 0 = 18/18 bé, 1 = algun anunci amb
  # problema (l'avís ja és a la BD), altres = la passada ha petat.
  set +e
  python3 push_to_supabase.py
  rc=$?
  set -e
  echo "[$(date -u)] Snapshot acabat amb codi $rc"
done
