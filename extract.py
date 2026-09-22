"""Extreu preus i estades mínimes del multicalendari de PriceLabs.

Parseja el payload RSC (self.__next_f.push) incrustat a l'HTML de
/multicalendar i retorna, per cada allotjament: id, nom, estat (avís),
parent_key i array de dates amb price, min_stay, booked_price, etc.

Els anuncis es troben pel seu `listing_id`, NO per la clau `pricing_array`:
quan un anunci està en error, PriceLabs serveix el seu `pricing_array` com a
referència RSC a l'array buit d'un altre anunci ("$7:1:props:listings:0:…"),
i buscant per `pricing_array` aquells anuncis no apareixien mai (setembre 2026:
16 de 18 invisibles, i cap avís).
"""
import json
import re
import sys


def flight_text(html: str) -> str:
    """Concatena tots els fragments RSC del payload Next.js."""
    parts = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        parts.append(json.loads(m.group(1)))
    return "".join(parts)


def _object_starting_before(flight: str, pos: int):
    """Retrocedeix des de `pos` fins a la clau '{' que obre l'objecte i el decodifica."""
    depth = 0
    i = pos
    while i > 0:
        c = flight[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
        i -= 1
    try:
        obj, _ = json.JSONDecoder().raw_decode(flight[i:])
        return obj
    except json.JSONDecodeError:
        return None


def find_listing_objects(flight: str):
    """Un objecte per anunci: el més complet que contingui `listing_id` i `pricing_array`."""
    per_id = {}
    for m in re.finditer(r'"listing_id":"([^"]+)"', flight):
        lid = m.group(1)
        obj = _object_starting_before(flight, m.start())
        if not isinstance(obj, dict) or obj.get("listing_id") != lid or "pricing_array" not in obj:
            continue
        if lid not in per_id or len(obj) > len(per_id[lid]):
            per_id[lid] = obj
    return list(per_id.values())


def _text(v):
    """PriceLabs alterna {'text': …, 'key': …} i cadena plana per als estats."""
    if isinstance(v, dict):
        return v.get("text") or None
    return v or None


def extract(html: str):
    flight = flight_text(html)
    listings = find_listing_objects(flight)
    out = []
    for l in listings:
        pa = l.get("pricing_array")
        # Referència RSC ("$7:1:props:listings:0:pricing_array") = array buit compartit.
        if not isinstance(pa, list):
            pa = []
        out.append({
            "id": l.get("listing_id") or l.get("id"),
            "name": l.get("listing_name") or l.get("name"),
            "pms": l.get("pms_name") or l.get("pms"),
            "parent_key": l.get("parent_key"),
            "base_price": l.get("base_price") or l.get("base"),
            "min_price": l.get("min_price") or l.get("min"),
            "max_price": l.get("max_price") or l.get("max"),
            "last_pushed_on": l.get("last_pushed_on"),
            "last_booked_date": l.get("last_booked_date"),
            "sync_status": _text(l.get("sync_status")),
            # Present quan PriceLabs té l'anunci en error o sense revisar; en aquest
            # estat el pricing_array ve BUIT (o és una referència a un de buit).
            "error_message": _text(l.get("error_message")),
            "sync_toggle": l.get("sync_toggle"),
            "weekly_discount": l.get("weekly_discount"),
            "monthly_discount": l.get("monthly_discount"),
            "calendar": [
                {
                    "date": d.get("date"),
                    "price": d.get("price"),
                    "min_stay": d.get("min_stay"),
                    "booked_price": d.get("booked_price"),
                    "user_price": d.get("user_price"),
                    "unbookable": d.get("unbookable"),
                    "num_bookings": d.get("num_bookings"),
                    "check_in": d.get("check_in"),
                    "check_out": d.get("check_out"),
                    "uncustomized_price": d.get("uncustomized_price"),
                    "min_price": d.get("min_price"),
                    "max_price": d.get("max_price"),
                    "holiday_flag": d.get("holiday_flag"),
                }
                for d in pa
            ],
            "_raw_keys": sorted(l.keys()),
        })
    return out


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "multicalendar_rendered.html"
    html = open(src).read()
    data = extract(html)
    print(f"{len(data)} allotjaments trobats")
    for l in data:
        cal = l["calendar"]
        rng = f"{cal[0]['date']} → {cal[-1]['date']}" if cal else "(buit)"
        err = f"  ⚠ {l['error_message'][:50]}" if l.get("error_message") else ""
        print(f"  {str(l['id'])[:24]:26} {str(l['name'])[:42]:44} {len(cal):3} dies  {rng}{err}")
