"""Desglossament de preu de PriceLabs via /api/fetch_reasons_json.

Aquest endpoint retorna, per cada listing i data, el "per què" del preu:
factors de mercat (temporada, demanda), personalitzacions (last-minute,
ocupació), ocupació de veïnat, ADR de l'any passat, resum en text i si el
dia és event/festiu. Una sola crida POST cobreix tots els listings d'un
rang de dates (es parteix en finestres de 90 dies).
"""
import json
import re
from datetime import date, timedelta

ENDPOINT = "https://app.pricelabs.co/api/fetch_reasons_json"
WINDOW_DAYS = 90


def pms_of(listing_id: str) -> str:
    return "tokeet" if listing_id.endswith("-tk2") else "tokeet"


def pct(v):
    """'+30%' / '-39%' → 30.0 / -39.0 ; None si no aplica."""
    if not v or not isinstance(v, str):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v)
    return float(m.group(0)) if m else None


def num(v):
    if v in (None, "", "-1", -1, "-1%"):
        return None
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        return float(m.group(0)) if m else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _windows(start: str, end: str, days: int = WINDOW_DAYS):
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while s <= e:
        w_end = min(s + timedelta(days=days - 1), e)
        yield s.isoformat(), w_end.isoformat()
        s = w_end + timedelta(days=1)


def _post(session, ids, start, end):
    payload = {
        "listings": [{"listing_id": i, "pms_name": pms_of(i)} for i in ids],
        "start_date": start,
        "end_date": end,
        "page": 1,
    }
    r = session.post(ENDPOINT, json=payload, stealthy_headers=True,
                     headers={"Content-Type": "application/json",
                              "Accept": "application/json"})
    if r.status != 200:
        raise RuntimeError(f"fetch_reasons_json → {r.status}")
    m = re.search(r"\{.*\}", r.html_content, re.S)  # Scrapling embolcalla en <html>
    if not m:
        raise RuntimeError("Resposta de reasons sense JSON")
    return json.loads(m.group(0))


def fetch_reasons(session, ids, start: str, end: str) -> dict:
    """{listing_id: {stay_date: day_reasons}} per tots els listings i dates."""
    out = {}
    for w_start, w_end in _windows(start, end):
        data = _post(session, ids, w_start, w_end)
        for ld in data.get("response", {}).get("data", []):
            lid = ld["listing_id"]
            out.setdefault(lid, {}).update(ld.get("reasons_json_data", {}))
    return out


def flatten(day: dict) -> dict:
    """Camps plans clau + el desglossament sencer, a partir d'un dia de reasons."""
    info = day.get("listing_info", {}) or {}
    mf = day.get("market_factors", {}) or {}
    oc = day.get("other_customizations", {}) or {}

    # market_factors indexats per 'key'
    by_key = {}
    for v in mf.values():
        if isinstance(v, dict) and v.get("key"):
            by_key[v["key"]] = v

    minstay_reason = None
    for v in oc.values():
        if isinstance(v, dict) and v.get("key") == "minstay_reason":
            minstay_reason = v.get("value")

    is_event = bool(minstay_reason and re.search(r"vento|estivo", minstay_reason))

    # min_stay numèric: el valor de minstay_reason comença amb el número ("2 (...)")
    r_min_stay = None
    if minstay_reason:
        m = re.match(r"\s*(\d+)", minstay_reason)
        r_min_stay = int(m.group(1)) if m else None

    return {
        # camps de preu de fallback (quan el multicalendari ve buit per aquest listing)
        "r_price": num(info.get("price")),
        "r_uncustomized_price": num(info.get("uncustomized_price")),
        "r_base_price": num(info.get("base_price")),
        "r_min_price": num(info.get("minimum_price")),
        "r_max_price": num(info.get("maximum_price")),
        "r_min_stay": r_min_stay,
        "seasonality_pct": pct(by_key.get("seasonality", {}).get("value")),
        "demand_factor_pct": pct(by_key.get("demand_factor", {}).get("value")),
        "nhood_occ": num(info.get("nhood_occ")),
        "nhood_demand": num(info.get("nhood_demand")),
        "adr": num(info.get("ADR")),
        "adr_stly": num(info.get("ADR_STLY")),
        "occupancy": num(info.get("occupancy")),
        "minstay_reason": minstay_reason,
        "is_event": is_event,
        "price_summary": info.get("summary"),
        "reasons": day,
    }
