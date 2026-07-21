"""Scrape PriceLabs i puja el snapshot diari a Supabase.

Env vars requerides (o ~/.pricelabs.env quan s'executa fora de Docker):
  PRICELABS_EMAIL / PRICELABS_PASSWORD
  SUPABASE_URL          — p.ex. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  — service role key (bypassa RLS)

Només guarda l'últim preu: upsert sobre (listing_id, stay_date), que
sobreescriu la fila existent (snapshot_date passa a ser la data de
l'última captura). No acumula historial.
"""
import json
import os
import sys
import urllib.request
from datetime import date, timedelta

from scrape_pricelabs import fetch_calendar

CHUNK = 200


def num(v):
    """PriceLabs codifica 'sense valor' com -1 o strings; normalitza a float/None."""
    if v in (None, "", "-1", -1):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hostly_name(listing_id: str, supabase_url: str, service_key: str) -> str:
    """Nom de l'apartament a Hostly per a un listing_id (millor que el de PriceLabs)."""
    try:
        q = (f"{supabase_url}/rest/v1/accommodation_channel_listings"
             f"?external_rental_id=eq.{listing_id}&select=accommodations(name)")
        req = urllib.request.Request(q, headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.load(resp)
        if rows and rows[0].get("accommodations"):
            return rows[0]["accommodations"]["name"]
    except Exception:
        pass
    return ""


def send_whatsapp(text: str):
    """Avís d'anomalia per WhatsApp (Evolution API, mateix canal que n8n)."""
    url = os.environ.get(
        "EVOLUTION_URL",
        "https://caserna13-evolution-api.f9pppl.easypanel.host/message/sendText/694232714")
    apikey = os.environ.get("EVOLUTION_APIKEY", "D9AE6D0C900A-43DE-9D27-E66C6E448C38")
    number = os.environ.get("ALERT_NUMBER", "34644969299")
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"number": f"{number}@s.whatsapp.net", "text": text}).encode(),
            method="POST",
            headers={"apikey": apikey, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        print("Avís WhatsApp enviat.")
    except Exception as e:  # l'avís mai ha de fer caure l'scrape
        print(f"WARN: no s'ha pogut enviar l'avís WhatsApp: {e}", file=sys.stderr)


def split_empty_listings(data):
    """Separa els listings que PriceLabs serveix SENSE cap preu diari.

    Quan un listing està en estat d'error a PriceLabs (p.ex. «reconecta tu
    cuenta de Tokeet»), el multicalendari torna el pricing_array amb tots els
    camps buits. Si escrivíssim aquestes files, SOBREESCRIURÍEM dades bones
    del snapshot anterior amb nulls (cas Maçanet, 18-19 jul 2026). Aquests
    listings NO es pugen i es notifica per WhatsApp.
    """
    ok, empty = [], []
    for l in data:
        cal = l.get("calendar") or []
        has_price = any(
            num(d.get("price")) is not None or (d.get("breakdown") or {}).get("r_price") is not None
            for d in cal
        )
        (ok if (has_price or not cal) else empty).append(l)
    return ok, empty


def rows_from(data, snapshot_date: str):
    rows = []
    for l in data:
        for d in l["calendar"]:
            b = d.get("breakdown") or {}
            # El multicalendari deixa el preu buit per alguns listings; en aquest
            # cas fem fallback als valors de reasons (r_*), que són fiables.
            price = num(d["price"])
            if price is None:
                price = b.get("r_price")
            uncustomized = num(d.get("uncustomized_price"))
            if uncustomized is None:
                uncustomized = b.get("r_uncustomized_price")
            min_stay = int(d["min_stay"]) if num(d["min_stay"]) is not None else b.get("r_min_stay")
            base_price = num(l.get("base_price"))
            if base_price is None:
                base_price = b.get("r_base_price")
            min_price = num(l.get("min_price"))
            if min_price is None:
                min_price = b.get("r_min_price")
            max_price = num(l.get("max_price"))
            if max_price is None:
                max_price = b.get("r_max_price")
            rows.append({
                "snapshot_date": snapshot_date,
                "listing_id": l["id"],
                "listing_name": l["name"],
                "stay_date": d["date"],
                "price": price,
                "min_stay": min_stay,
                "booked_price": num(d["booked_price"]),
                "user_price": num(d["user_price"]),
                "uncustomized_price": uncustomized,
                "unbookable": d["unbookable"] == "1",
                "num_bookings": int(d["num_bookings"]) if num(d["num_bookings"]) is not None else None,
                "base_price": base_price,
                "min_price": min_price,
                "max_price": max_price,
                "weekly_discount": num(l.get("weekly_discount")),
                "monthly_discount": num(l.get("monthly_discount")),
                "last_pushed_on": l.get("last_pushed_on"),
                # estat de sincronització de PriceLabs (si és False, el preu és la
                # recomanació que PriceLabs mostra però NO empeny al canal)
                "sync_enabled": bool(l.get("sync_toggle")),
                # si PriceLabs marca el listing en error, deixa el motiu a la BD
                "sync_status": l.get("sync_status") or l.get("error_message") or None,
                "holiday_flag": d.get("holiday_flag") == "1",
                # desglossament del preu (fetch_reasons_json)
                "seasonality_pct": b.get("seasonality_pct"),
                "demand_factor_pct": b.get("demand_factor_pct"),
                "nhood_occ": b.get("nhood_occ"),
                "nhood_demand": b.get("nhood_demand"),
                "adr": b.get("adr"),
                "adr_stly": b.get("adr_stly"),
                "occupancy": b.get("occupancy"),
                "is_event": b.get("is_event"),
                "minstay_reason": b.get("minstay_reason"),
                "price_summary": b.get("price_summary"),
                "reasons": b.get("reasons"),
            })
    return rows


def _post_chunk(endpoint, chunk, service_key):
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(chunk).encode(),
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"{resp.status} {resp.read()[:500]}")


def upsert(rows, supabase_url: str, service_key: str):
    endpoint = (f"{supabase_url}/rest/v1/pricelabs_snapshots"
                f"?on_conflict=listing_id,stay_date")
    import time
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        # Reintent amb backoff: el statement_timeout de Postgres pot cancel·lar
        # un bloc si la BD està ocupada (checkpoint, etc.). No és fatal: es reintenta.
        for attempt in range(1, 5):
            try:
                _post_chunk(endpoint, chunk, service_key)
                break
            except Exception as e:
                if attempt == 4:
                    sys.exit(f"Upsert ha fallat definitivament al bloc {i}: {e}")
                wait = 3 * attempt
                print(f"  [reintent {attempt}] bloc {i} ha fallat ({str(e)[:80]}); espero {wait}s")
                time.sleep(wait)
        print(f"  upsert {i + len(chunk)}/{len(rows)}")


def main():
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        sys.exit("Falten SUPABASE_URL o SUPABASE_SERVICE_KEY a l'entorn.")

    days = int(os.environ.get("SCRAPE_DAYS", "360"))
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()

    print(f"Scraping PriceLabs {start} → {end} ...")
    data = fetch_calendar(start, end, with_reasons=True)

    # Guard anti-buit: un listing en error a PriceLabs ve sense preus; no el
    # pugem (conservem el snapshot anterior) i avisem per WhatsApp.
    data, empty = split_empty_listings(data)
    if empty:
        noms = ", ".join(
            hostly_name(l.get("id"), supabase_url, service_key) or l.get("name") or "?"
            for l in empty)
        send_whatsapp(
            f"⚠️ PriceLabs no sincronitza bé: {noms}. "
            "Es mantenen els preus anteriors.")

    rows = rows_from(data, snapshot_date=start)
    print(f"{len(data)} allotjaments ({len(empty)} buits saltats), {len(rows)} files. Pujant a Supabase...")
    upsert(rows, supabase_url, service_key)
    print("OK — snapshot desat.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if str(e) not in ("", "0", "None"):
            send_whatsapp(f"❌ Scrape de PriceLabs ha fallat: {e}")
        raise
    except Exception as e:
        send_whatsapp(f"❌ Scrape de PriceLabs ha petat: {type(e).__name__}: {e}")
        raise
