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
import urllib.error
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


def hostly_listings(supabase_url: str, service_key: str) -> dict:
    """{listing_id: nom} de tots els anuncis coneguts per Hostly.

    És la llista canònica de què s'ha de capturar: el multicalendari de
    PriceLabs en serveix un subconjunt variable i no es pot fer dependre'n.
    """
    q = (f"{supabase_url}/rest/v1/accommodation_channel_listings"
         f"?external_rental_id=not.is.null&select=external_rental_id,accommodations(name)")
    req = urllib.request.Request(q, headers={"apikey": service_key,
                                             "Authorization": f"Bearer {service_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.load(resp)
    return {r["external_rental_id"]: (r.get("accommodations") or {}).get("name") or ""
            for r in rows if r.get("external_rental_id")}


SEVERITATS = ("warning", "error", "critical")


def avisa(text: str, severity: str = "error", detail: str | None = None):
    """Deixa l'avís a `system_issues` (RPC log_system_issue). La comprovació diària
    de la BD (`pricing_freshness_check`, 05:45 UTC) és qui notifica el gestor.
    Fins al 22-09-2026 s'enviava per WhatsApp (Evolution 694232714): aquella
    instància està tancada i els avisos es perdien en silenci."""
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    print(f"AVÍS [{severity}] {text}" + (f" — {detail}" if detail else ""))
    if not supabase_url or not service_key:
        return
    if severity not in SEVERITATS:  # system_issues té un CHECK sobre severity
        severity = "warning"
    try:
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/rpc/log_system_issue",
            data=json.dumps({"p_source": "preus", "p_title": text[:200],
                             "p_detail": detail, "p_severity": severity}).encode(),
            method="POST",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"avís registrat a system_issues (HTTP {resp.status})")
    except urllib.error.HTTPError as e:  # l'avís mai ha de fer caure l'scrape
        print(f"WARN: system_issues ha rebutjat l'avís: HTTP {e.code} {e.read()[:300]!r}", file=sys.stderr)
    except Exception as e:
        print(f"WARN: no s'ha pogut registrar l'avís: {e}", file=sys.stderr)


def send_whatsapp(text: str):
    """Compatibilitat: ara és avisa()."""
    avisa(text)


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
        (ok if has_price else empty).append(l)  # calendari buit = buit, no ok
    return ok, empty


def rows_from(data, snapshot_date: str):
    from datetime import datetime, timezone
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows = []
    skipped = 0
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
            # MAI escriure un dia SENSE preu: l'upsert va per (listing_id,
            # stay_date), així que pujar-lo sobreescriuria amb NULL el preu bo
            # d'ahir. Un dia sense preu simplement no es toca i conserva
            # l'últim conegut.
            #
            # Això no és teòric: passa quan PriceLabs serveix el listing a
            # mitges (cas Canal, 28-jul-2026: 363 de 364 dies a null). El guard
            # de split_empty_listings només atrapa el cas TOTAL, no el parcial.
            # I un dia perdut es llegeix com "aquest dia no té preu", que és el
            # senyal que fa servir l'horitzó per tancar-lo al canal.
            if price is None:
                skipped += 1
                continue
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
                # si PriceLabs marca el listing en error, deixa el motiu a la BD;
                # si el recàlcul (/api/process) ha fallat, també
                "sync_status": (l.get("sync_status") or l.get("error_message")
                                or (("process: " + l["process_error"]) if l.get("process_error") else None)),
                # data de càlcul real de PriceLabs (resposta de /api/process)
                "last_refreshed_at": l.get("last_refreshed_at"),
                # l'upsert va per (listing_id, stay_date): sense això el DEFAULT now()
                # només s'aplicava a l'INSERT i la columna deia agost amb files d'avui
                "scraped_at": scraped_at,
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
    if skipped:
        print(f"  {skipped} dies sense preu: NO es pugen (es conserva l'últim conegut)")
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
    dry = os.environ.get("DRY_RUN") == "1"

    coneguts = hostly_listings(supabase_url, service_key)
    print(f"Scraping PriceLabs {start} → {end} ({len(coneguts)} anuncis a Hostly) ...")
    data = fetch_calendar(start, end, with_reasons=True, extra=coneguts)

    # Guard anti-buit: un anunci sense cap preu no es puja (es conserva el
    # snapshot anterior). No s'escriu mai un dia sense preu.
    data, empty = split_empty_listings(data)
    rows = rows_from(data, snapshot_date=start)
    print(f"{len(data)} allotjaments ({len(empty)} buits saltats), {len(rows)} files.")
    if dry:
        print("DRY_RUN: no es puja res.")
    else:
        upsert(rows, supabase_url, service_key)
        print("OK — snapshot desat.")

    # Resum: què ha quedat de cada anunci. Un anunci compta com a bé només si
    # s'ha pogut recalcular avui, té preus i PriceLabs no hi posa cap avís.
    def nom(l):
        return coneguts.get(l.get("id")) or l.get("name") or l.get("id")
    be, malament = [], []
    for l in data + empty:
        ref = (l.get("last_refreshed_at") or "")[:10]
        motiu = None
        if l in empty:
            motiu = "sense preus"
        elif l.get("process_error"):
            motiu = l["process_error"][:60]
        elif l.get("error_message"):
            motiu = l["error_message"][:60]
        elif ref != start:
            motiu = f"càlcul del {ref or '?'}"
        (malament if motiu else be).append((nom(l), motiu))
    total = len(coneguts) or len(data) + len(empty)
    dia = date.today().strftime("%d/%m")
    if not malament and len(be) >= total:
        print(f"✅ Preus {dia} · {len(be)}/{total} recalculats i capturats")
        return 0
    detall = " · ".join(f"{n}: {m}" for n, m in malament) or f"només {len(be)} de {total}"
    avisa(f"⚠️ Preus {dia} · {len(be)}/{total} ok · {len(malament) or total - len(be)} amb problema",
          "warning", detall[:1000])
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as e:
        if str(e) not in ("", "0", "1", "None"):
            avisa(f"❌ Preus {date.today().strftime('%d/%m')}: la passada ha fallat", "error", str(e)[:500])
        raise
    except Exception as e:
        avisa(f"❌ Preus {date.today().strftime('%d/%m')}: la passada ha petat", "error", f"{type(e).__name__}: {e}"[:500])
        raise
