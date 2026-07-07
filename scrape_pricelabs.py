"""Extreu preus i estades mínimes de tots els allotjaments de PriceLabs.

Pipeline HTTP pur (sense navegador):
  1. Login al formulari Rails de pricelabs.co/signin
  2. GET /multicalendar?startDate=...&endDate=...
  3. Parseig del payload RSC de Next.js incrustat a l'HTML

Ús:
  python3 scrape_pricelabs.py [start YYYY-MM-DD] [end YYYY-MM-DD]

Sortides:
  pricelabs_data.json  — estructura completa per allotjament
  pricelabs_data.csv   — pla: listing, data, preu, min_stay, ...
"""
import csv
import json
import sys
from datetime import date, timedelta

from scrapling.fetchers import FetcherSession

from extract import extract
from login import load_credentials, login


def fetch_calendar(start: str, end: str):
    email, password = load_credentials()
    with FetcherSession() as s:
        resp = login(s, email, password)
        if "signin" in resp.url:
            sys.exit("Login fallit — revisa ~/.pricelabs.env")
        url = f"https://app.pricelabs.co/multicalendar?startDate={start}&endDate={end}"
        r = s.get(url, stealthy_headers=True)
        if r.status != 200:
            sys.exit(f"GET multicalendar ha retornat {r.status}")
        data = extract(r.html_content)
        if not data:
            sys.exit("0 allotjaments extrets — l'estructura de la pàgina pot haver canviat.")
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
        print(f"  {str(l['name'])[:45]:47} {len(c):4} dies")
    print("\nDesat: pricelabs_data.json + pricelabs_data.csv")
