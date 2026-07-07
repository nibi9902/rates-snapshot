"""Descobreix els endpoints XHR que fa servir el dashboard de PriceLabs.

Fa login amb Playwright, navega al multicalendari i registra totes les
respostes JSON (URL + mostra del cos) a xhr_log/.
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from login import load_credentials

OUT = Path("xhr_log")
OUT.mkdir(exist_ok=True)

INTERESTING = re.compile(r"pricelabs\.co", re.I)
SKIP = re.compile(r"\.(js|css|png|jpg|svg|woff2?|ico)(\?|$)|google|segment|intercom|sentry|datadog", re.I)

captured = []


def on_response(resp):
    url = resp.url
    if not INTERESTING.search(url) or SKIP.search(url):
        return
    ct = resp.headers.get("content-type", "")
    if "json" not in ct:
        return
    try:
        body = resp.text()
    except Exception:
        return
    try:
        post_data = resp.request.post_data
    except Exception:
        post_data = "<binary/comprimit>"
    idx = len(captured)
    captured.append({"idx": idx, "url": url, "method": resp.request.method,
                     "status": resp.status, "size": len(body)})
    (OUT / f"{idx:03d}.json").write_text(json.dumps(
        {"url": url, "method": resp.request.method,
         "post_data": post_data,
         "body_sample": body[:5000]}, indent=2))


def main():
    email, password = load_credentials()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="es-ES")
        page = ctx.new_page()
        page.on("response", on_response)

        page.goto("https://pricelabs.co/signin", wait_until="domcontentloaded")
        page.fill("#user_email", email)
        page.fill("#password-field", password)
        page.click("input[type=submit]")
        page.wait_for_url("**/pricing**", timeout=45000)
        page.wait_for_timeout(8000)

        print("== a /pricing:", page.url)

        # multicalendari: vista amb preus + min stay de tots els allotjaments
        page.goto("https://app.pricelabs.co/multicalendar", wait_until="domcontentloaded")
        page.wait_for_timeout(30000)
        print("== a /multicalendar:", page.url)
        page.screenshot(path="multicalendar.png", full_page=False)

        ctx.storage_state(path="session_state.json")
        browser.close()

    print(f"\n{len(captured)} respostes JSON capturades:")
    for c in captured:
        print(f"  [{c['idx']:03d}] {c['method']} {c['status']} {c['size']:>8}B  {c['url'][:120]}")


if __name__ == "__main__":
    main()
