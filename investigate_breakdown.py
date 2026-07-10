"""Investiga d'on surt el desglossament de preu (popup "FACTORES DEL MERCADO").

Obre el multicalendari amb Playwright, clica un badge de preu per obrir el
tooltip de desglossament i registra:
  - QUALSEVOL resposta XHR JSON de pricelabs disparada pel clic (breakdown/*)
  - El text i HTML del tooltip que apareix (per si es calcula client-side)

Sortida: breakdown_probe/ amb els XHR i el DOM del tooltip.
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from login import load_credentials

OUT = Path("breakdown_probe")
OUT.mkdir(exist_ok=True)

INTERESTING = re.compile(r"pricelabs\.co", re.I)
SKIP = re.compile(r"\.(js|css|png|jpg|svg|woff2?|ico)(\?|$)|google|segment|intercom|sentry|datadog|zipy|pendo|salespanel|zoho|novu", re.I)

captured = []
clicked_at = {"t": None}


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
    idx = len(captured)
    captured.append({"idx": idx, "url": url, "method": resp.request.method,
                     "status": resp.status, "size": len(body)})
    try:
        post = resp.request.post_data
    except Exception:
        post = None
    (OUT / f"{idx:03d}.json").write_text(json.dumps(
        {"url": url, "method": resp.request.method, "post_data": post,
         "body_sample": body[:8000]}, indent=2, ensure_ascii=False))


def main():
    email, password = load_credentials()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # reutilitza sessió si existeix
        state = "session_state.json" if Path("session_state.json").exists() else None
        ctx = browser.new_context(locale="es-ES", storage_state=state)
        page = ctx.new_page()

        page.goto("https://app.pricelabs.co/multicalendar", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "signin" in page.url or "sign_in" in page.url:
            print("Sessió caducada, fent login...")
            page.goto("https://pricelabs.co/signin", wait_until="domcontentloaded")
            page.fill("#user_email", email)
            page.fill("#password-field", password)
            page.click("input[type=submit]")
            page.wait_for_url("**/pricing**", timeout=45000)
            page.goto("https://app.pricelabs.co/multicalendar", wait_until="domcontentloaded")

        # espera que el calendari renderitzi
        page.wait_for_selector('[qa-id^="price-tooltip--"]', timeout=45000)
        page.wait_for_timeout(4000)
        print("== multicalendar:", page.url)

        # A partir d'aquí monitoritzem la xarxa: qualsevol XHR ara ve del clic
        page.on("response", on_response)
        n_before = len(captured)

        badge = page.query_selector('[qa-id^="price-tooltip--"]')
        qa = badge.get_attribute("qa-id")
        box = badge.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        def find_tip():
            for sel in ['text=PRECIO BASE', 'text=FACTORES DEL MERCADO',
                        'text=Precio sin personalizar', 'text=Ocupación del Vecindario']:
                el = page.query_selector(sel)
                if el:
                    container = el.evaluate_handle(
                        "e => e.closest('[role=tooltip],[role=dialog],.chakra-popover__content') "
                        "|| e.closest('div[class*=popover]') || e.parentElement.parentElement.parentElement.parentElement")
                    return (container.evaluate("e => e.innerText"),
                            container.evaluate("e => e.outerHTML"))
            return "", ""

        tip_text, tip_html = "", ""
        # 1) HOVER
        print("Provant HOVER sobre:", qa)
        page.mouse.move(cx, cy)
        page.wait_for_timeout(3000)
        tip_text, tip_html = find_tip()

        # 2) LONG-PRESS si el hover no ha obert res
        if not tip_text:
            print("Hover sense resultat, provant LONG-PRESS...")
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.wait_for_timeout(1500)
            tip_text, tip_html = find_tip()
            page.mouse.up()
            if not tip_text:
                page.wait_for_timeout(1000)
                tip_text, tip_html = find_tip()

        (OUT / "tooltip.txt").write_text(tip_text)
        (OUT / "tooltip.html").write_text(tip_html)
        page.screenshot(path=str(OUT / "after_click.png"), full_page=False)
        ctx.storage_state(path="session_state.json")
        browser.close()

    xhr_from_click = captured[n_before:]
    print(f"\n=== {len(xhr_from_click)} XHR JSON disparades pel clic ===")
    for c in xhr_from_click:
        print(f"  [{c['idx']:03d}] {c['method']} {c['status']} {c['size']:>7}B  {c['url'][:130]}")
    print(f"\nTooltip capturat: {len(tip_text)} chars de text")
    if tip_text:
        print("--- primeres línies del tooltip ---")
        print("\n".join(tip_text.splitlines()[:20]))


if __name__ == "__main__":
    main()
