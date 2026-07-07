"""Login a PriceLabs via HTTP pur (Scrapling FetcherSession) i validació de sessió.

Credencials: llegeix ~/.pricelabs.env amb format:
    PRICELABS_EMAIL=...
    PRICELABS_PASSWORD=...
"""
import os
import sys
from pathlib import Path

from scrapling.fetchers import FetcherSession

ENV_FILE = Path.home() / ".pricelabs.env"


def load_credentials():
    env_email = os.environ.get("PRICELABS_EMAIL")
    env_password = os.environ.get("PRICELABS_PASSWORD")
    if env_email and env_password:
        return env_email, env_password
    if not ENV_FILE.exists():
        sys.exit(f"No existeix {ENV_FILE}. Crea'l amb PRICELABS_EMAIL i PRICELABS_PASSWORD.")
    creds = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds["PRICELABS_EMAIL"], creds["PRICELABS_PASSWORD"]


def login(session: FetcherSession, email: str, password: str):
    signin = session.get("https://pricelabs.co/signin", stealthy_headers=True)
    token = signin.css('input[name="authenticity_token"]::attr(value)').get()
    if not token:
        sys.exit("No s'ha trobat l'authenticity_token — la pàgina de login ha canviat.")

    resp = session.post(
        "https://pricelabs.co/signin",
        data={
            "authenticity_token": token,
            "user[email]": email,
            "user[password]": password,
            "user[remember_me]": "1",
            "commit": "Sign in",
        },
        stealthy_headers=True,
    )
    return resp


if __name__ == "__main__":
    email, password = load_credentials()
    with FetcherSession() as s:
        resp = login(s, email, password)
        print("status:", resp.status)
        print("url final:", resp.url)
        # Si el login funciona, el dashboard ha de respondre autenticat
        dash = s.get("https://app.pricelabs.co/pricing", stealthy_headers=True)
        print("dashboard status:", dash.status)
        print("dashboard url:", dash.url)
        logged_in = "signin" not in dash.url
        print("LOGIN OK" if logged_in else "LOGIN FALLIT (redirigit a signin)")
