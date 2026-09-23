"""Extreu preus i estades mínimes de tots els allotjaments de PriceLabs.

Pipeline HTTP pur (sense navegador):
  1. Login al formulari Rails de pricelabs.co/signin
  2. GET /multicalendar → un objecte per anunci (avís, parent_key, estat de sync)
  3. POST /api/process per anunci → força el recàlcul i retorna els preus nous
     amb `last_refreshed_at` (la data de càlcul real de PriceLabs)
  4. (opcional) /api/fetch_reasons_json → desglossament del preu

Per què el pas 3: un anunci que ningú obre NO es recalcula sol. Entre el
4 i el 19 d'agost de 2026 PriceLabs va marcar els 18 anuncis com a «no
revisado» i va servir la memòria cau fins que es va esgotar; Hostly va
empènyer un mes de preus vells. Obrir la fitxa (o aquesta crida) és el que
fa que PriceLabs calculi. La sincronització segueix apagada: /api/process
NO empeny res al canal (verificat 22-09-2026: «Última Sincronización» no
es mou).

Ús:
  python3 scrape_pricelabs.py [start YYYY-MM-DD] [end YYYY-MM-DD]

Sortides:
  pricelabs_data.json  — estructura completa per allotjament
  pricelabs_data.csv   — pla: listing, data, preu, min_stay, ...
"""
import csv
import json
import re
import sys
from datetime import date, timedelta

from scrapling.fetchers import FetcherSession

from extract import extract
from login import load_credentials, login
from reasons import fetch_reasons, flatten

PROCESS_URL = "https://app.pricelabs.co/api/process"
REINTENTS_RECALCUL = 1      # un reintent si PriceLabs torna un càlcul que no és d'avui
ESPERA_RECALCUL = 60        # segons abans del reintent


def dia_buit(d: str) -> dict:
    """Dia sense dades del multicalendari; els preus vindran de process/reasons."""
    return {"date": d, "price": "", "min_stay": "", "booked_price": "",
            "user_price": "", "unbookable": None, "num_bookings": None,
            "check_in": "", "check_out": "", "uncustomized_price": "",
            "min_price": None, "max_price": None, "holiday_flag": None}


def dia_de_process(d: dict) -> dict:
    """Dia del pricing_array de /api/process, al mateix format que el multicalendari."""
    return {"date": d.get("date"), "price": d.get("price"), "min_stay": d.get("min_stay"),
            "booked_price": d.get("booked_price"), "user_price": d.get("user_price"),
            "unbookable": d.get("unbookable"), "num_bookings": d.get("num_bookings"),
            "check_in": d.get("check_in"), "check_out": d.get("check_out"),
            "uncustomized_price": d.get("uncustomized_price"),
            "min_price": d.get("min_price"), "max_price": d.get("max_price"),
            "holiday_flag": d.get("holiday_flag")}


def process_listing(session, listing_id: str, pms: str, parent_key):
    """Força el recàlcul d'un anunci. Retorna (last_refreshed_at, warning, pricing_array)."""
    payload = {"listingId": listing_id, "pmsName": pms or "tokeet"}
    if parent_key:
        payload["parentKey"] = str(parent_key)
    r = session.post(PROCESS_URL, json=payload, stealthy_headers=True,
                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    if r.status != 200:
        raise RuntimeError(f"/api/process → HTTP {r.status}")
    m = re.search(r"\{.*\}", r.html_content, re.S)  # Scrapling embolcalla en <html>
    if not m:
        raise RuntimeError("/api/process sense JSON")
    j = json.loads(m.group(0))
    if j.get("message") != "SUCCESS":
        raise RuntimeError(f"/api/process: {str(j)[:120]}")
    g = (j.get("response") or {}).get("listingData", {}).get("graph_data") or {}
    return g.get("last_refreshed_at"), bool(g.get("warning")), g.get("pricing_array") or []


def _anunci_sintetic(lid: str, nom: str) -> dict:
    return {"id": lid, "name": nom, "pms": "tokeet", "parent_key": None,
            "base_price": None, "min_price": None, "max_price": None,
            "last_pushed_on": None, "last_booked_date": None, "sync_status": None,
            "error_message": None, "sync_toggle": None,
            "weekly_discount": None, "monthly_discount": None,
            "calendar": [], "_raw_keys": []}


def fetch_calendar(start: str, end: str, with_reasons: bool = False,
                   extra: dict | None = None, recalcula: bool = True):
    """Preus de PriceLabs per al rang demanat, per a tots els anuncis.

    `extra` = {listing_id: nom} d'anuncis que HAN de sortir encara que el
    multicalendari no els serveixi (la llista canònica és la de Hostly).
    `recalcula` = cridar /api/process per anunci (recàlcul + preus frescos).
    """
    email, password = load_credentials()
    with FetcherSession() as s:
        resp = login(s, email, password)
        if "signin" in resp.url:
            masked = f"{email[:3]}…@…{email[-10:]}" if email else "(buit)"
            body = (resp.html_content or "")
            errs = re.findall(r'(Invalid\s+\w+[^<."]{0,60}|contraseña[^<."]{0,60}|incorrect[^<."]{0,60}|blocked[^<."]{0,60}|captcha[^<."]{0,40})', body, re.I)
            print("── DIAGNÒSTIC LOGIN ──", file=sys.stderr)
            print(f"  email: {masked} (len={len(email)})", file=sys.stderr)
            print(f"  status POST: {resp.status}  url final: {resp.url}", file=sys.stderr)
            print(f"  missatges d'error detectats: {errs[:5]}", file=sys.stderr)
            sys.exit("Login fallit — revisa credencials / possible bloqueig d'IP")

        url = f"https://app.pricelabs.co/multicalendar?startDate={start}&endDate={end}"
        r = s.get(url, stealthy_headers=True)
        if r.status != 200:
            sys.exit(f"GET multicalendar ha retornat {r.status}")
        data = extract(r.html_content)

        vistos = {l["id"] for l in data if l["id"]}
        for lid, nom in (extra or {}).items():
            if lid not in vistos:
                data.append(_anunci_sintetic(lid, nom))
        if not data:
            sys.exit("0 allotjaments — ni al multicalendari ni a Hostly.")
        if extra and len(vistos) < len(extra):
            print(f"multicalendari: {len(vistos)} anuncis; {len(extra) - len(vistos)} "
                  f"afegits des de Hostly", file=sys.stderr)

        if recalcula:
            import time
            for l in data:
                try:
                    ref, warn, pa = process_listing(s, l["id"], l.get("pms"), l.get("parent_key"))
                    # A les 05:01 UTC del 23-09-2026 PriceLabs va tornar a tots els anuncis el
                    # càlcul del dia abans (finestra nocturna?); a les 06:10 recalculava a cada
                    # crida. Si el càlcul no és d'avui, s'espera i es reintenta.
                    intents = 0
                    while (ref or "")[:10] < start and intents < REINTENTS_RECALCUL:
                        intents += 1
                        print(f"  {l.get('name')}: càlcul del {(ref or '?')[:10]}, reintent {intents} "
                              f"d'aquí {ESPERA_RECALCUL}s", file=sys.stderr)
                        time.sleep(ESPERA_RECALCUL)
                        ref, warn, pa = process_listing(s, l["id"], l.get("pms"), l.get("parent_key"))
                    l["last_refreshed_at"] = ref
                    l["warning"] = warn
                    # El recàlcul tanca l'avís «no revisado» del multicalendari (verificat
                    # 22-09-2026: Horta II). El que val és l'estat DESPRÉS de processar:
                    # si PriceLabs no hi posa cap warning ara, l'avís vell no s'ha de desar.
                    if not warn:
                        l["error_message"] = None
                    dies = [dia_de_process(d) for d in pa
                            if d.get("date") and start <= d["date"] <= end]
                    if dies:
                        l["calendar"] = dies
                except Exception as e:  # un anunci que no es pot recalcular no atura la resta
                    l["process_error"] = str(e)[:160]
                    print(f"WARN /api/process {l.get('name')}: {e}", file=sys.stderr)

        if with_reasons:
            ids = [l["id"] for l in data if l["id"]]
            reasons = fetch_reasons(s, ids, start, end)
            for l in data:
                per_date = reasons.get(l["id"], {})
                if not l["calendar"]:
                    l["calendar"] = [dia_buit(d) for d in sorted(per_date)]
                for day in l["calendar"]:
                    rd = per_date.get(day["date"])
                    day["breakdown"] = flatten(rd) if rd else None
        return data


def write_csv(data, path="pricelabs_data.csv"):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["listing_id", "listing_name", "date", "price", "min_stay",
                    "booked_price", "user_price", "unbookable", "num_bookings",
                    "check_in", "check_out"])
        for l in data:
            for d in l["calendar"]:
                w.writerow([l["id"], l["name"], d["date"], d["price"],
                            d["min_stay"], d["booked_price"], d["user_price"],
                            d["unbookable"], d["num_bookings"],
                            d["check_in"], d["check_out"]])


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    end = sys.argv[2] if len(sys.argv) > 2 else (date.today() + timedelta(days=180)).isoformat()

    data = fetch_calendar(start, end)
    json.dump(data, open("pricelabs_data.json", "w"), indent=1, ensure_ascii=False)
    write_csv(data)

    total_days = sum(len(l["calendar"]) for l in data)
    print(f"{len(data)} allotjaments, {total_days} dies-allotjament ({start} → {end})")
    for l in data:
        c = l["calendar"]
        estat = l.get("last_refreshed_at") or l.get("process_error") or "?"
        print(f"  {str(l['name'])[:45]:47} {len(c):4} dies  {estat}")
    print("\nDesat: pricelabs_data.json + pricelabs_data.csv")
