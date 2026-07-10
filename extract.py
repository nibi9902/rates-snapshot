"""Extreu preus i estades mínimes del multicalendari de PriceLabs.

Parseja el payload RSC (self.__next_f.push) incrustat a l'HTML de
/multicalendar i retorna, per cada allotjament: id, nom i array de dates
amb price, min_stay, booked_price, etc.
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


def find_listing_objects(flight: str):
    """Troba cada objecte d'allotjament que conté un pricing_array."""
    decoder = json.JSONDecoder()
    results = []
    for m in re.finditer(r'"pricing_array":\[', flight):
        # retrocedeix fins l'inici de l'objecte listing (brace matching invers)
        start = m.start()
        depth = 0
        i = start
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
            obj, _ = decoder.raw_decode(flight[i:])
            results.append(obj)
        except json.JSONDecodeError as e:
            print(f"WARN: objecte a offset {i} no parsejable: {e}", file=sys.stderr)
    return results


def extract(html: str):
    flight = flight_text(html)
    listings = find_listing_objects(flight)
    out = []
    for l in listings:
        out.append({
            "id": l.get("id") or l.get("listing_id"),
            "name": l.get("name") or l.get("listing_name"),
            "pms": l.get("pms") or l.get("pms_name"),
            "base_price": l.get("base") or l.get("base_price"),
            "min_price": l.get("min") or l.get("min_price"),
            "max_price": l.get("max") or l.get("max_price"),
            "last_pushed_on": l.get("last_pushed_on"),
            "sync_status": l.get("sync_status"),
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
                for d in l.get("pricing_array", [])
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
        print(f"  {str(l['id'])[:24]:26} {str(l['name'])[:42]:44} {len(cal):3} dies  {rng}")
    if data:
        print("\nclaus del primer objecte:", ", ".join(data[0]["_raw_keys"]))
        print("\nmostra primer dia:", json.dumps(data[0]["calendar"][0], indent=2))
    json.dump(data, open("pricelabs_data.json", "w"), indent=1)
    print("\nDesat a pricelabs_data.json")
