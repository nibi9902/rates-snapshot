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

CHUNK = 500


def num(v):
    """PriceLabs codifica 'sense valor' com -1 o strings; normalitza a float/None."""
    if v in (None, "", "-1", -1):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def upsert(rows, supabase_url: str, service_key: str):
    endpoint = (f"{supabase_url}/rest/v1/pricelabs_snapshots"
                f"?on_conflict=listing_id,stay_date")
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (200, 201, 204):
                sys.exit(f"Upsert ha fallat: {resp.status} {resp.read()[:500]}")
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
    rows = rows_from(data, snapshot_date=start)
    print(f"{len(data)} allotjaments, {len(rows)} files. Pujant a Supabase...")
    upsert(rows, supabase_url, service_key)
    print("OK — snapshot desat.")


if __name__ == "__main__":
    main()
