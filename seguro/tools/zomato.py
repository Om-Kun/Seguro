"""
Zomato ordering in Seguro's automation browser profile (~/SeguroProjects/brave-profile).

Flow: find restaurant -> add items -> verify the cart against menu prices (code) ->
STOP unless the user approves this exact cart -> checkout -> the USER pays (Seguro never selects
or enters payment details) -> Seguro waits for Zomato's order confirmation and reports it.

One-time login (you sign in yourself, phone OTP):  python seguro/tools/zomato.py --login
"""
import os
import re
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.tools.registry import registry
from seguro.tools import webpage

BASE = "https://www.zomato.com"
OUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "SeguroDemo", "zomato")
PRICE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")


def _rupees(text: str):
    m = PRICE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _open_restaurant(p, restaurant: str, city: str) -> str:
    """Returns the restaurant's /order URL, found via the city listing or Zomato's search box on live web."""
    want = restaurant.lower()
    p.goto(f"{BASE}/{city.lower()}/order-food-online", wait_until="domcontentloaded", timeout=45000)
    p.wait_for_timeout(2500)

    def pick(links):
        for a in links:
            try:
                txt, href = (a.inner_text() or "").lower(), a.get_attribute("href") or ""
            except Exception:
                continue
            if want in txt and "/order" in href:
                return href if href.startswith("http") else BASE + href
        return None

    url = pick(p.locator("a[href*='/order']").all())
    if not url:
        box = p.get_by_placeholder(re.compile("Search for restaurant", re.I)).first
        if box.count():
            box.click()
            box.fill(restaurant)
            p.wait_for_timeout(2500)
            # Check dropdown suggestion
            item = p.get_by_text(re.compile(re.escape(restaurant), re.I)).first
            if item.count():
                item.click()
                p.wait_for_timeout(4000)
                if "/order" in p.url:
                    return p.url
                if f"/{city.lower()}/" in p.url:
                    url = p.url.split("?")[0].rstrip("/")
                    url = url if url.endswith("/order") else url + "/order"
                    p.goto(url, wait_until="domcontentloaded", timeout=45000)
                    p.wait_for_timeout(3500)
                    return p.url
        url = pick(p.locator("a[href]").all())
        if not url:  # suggestion rows link to the restaurant page; its order tab is /order
            for a in p.locator("a[href]").all():
                href = a.get_attribute("href") or ""
                if want in (a.inner_text() or "").lower() and f"/{city.lower()}/" in href:
                    url = (href if href.startswith("http") else BASE + href).split("?")[0].rstrip("/")
                    url = url if url.endswith("/order") else url + "/order"
                    break
    if not url:
        raise LookupError(f"Restaurant '{restaurant}' not found on Zomato in {city}.")
    p.goto(url, wait_until="domcontentloaded", timeout=45000)
    p.wait_for_timeout(3500)
    return p.url


def _item_card(p, name: str):
    """Innermost menu card containing the item heading and its price."""
    heading = p.locator("h4", has_text=re.compile(rf"^\s*{re.escape(name)}\s*$", re.I)).first
    if heading.count() == 0:
        heading = p.locator("h4", has_text=re.compile(re.escape(name), re.I)).first
    if heading.count() == 0:
        raise LookupError(f"Item '{name}' is not on this restaurant's menu.")
    heading.scroll_into_view_if_needed()
    # Innermost block holding the heading plus its ADD button or quantity stepper (probed DOM:
    # h4 -> div(name, price) -> div(name, price, ADD)).
    card = heading.locator("xpath=ancestor::div[.//*[normalize-space(text())='ADD'] or .//*[local-name()='svg']][1]")
    return heading.inner_text().strip(), card


# Quantity stepper on a menu card is: <svg minus> <span>N</span> <svg plus> (icons have no text).
QTY_SPAN = "xpath=.//span[string-length(normalize-space())>0 and translate(normalize-space(),'0123456789','')='']"
# Number spans that sit inside a menu card stepper (the card also holds an h4 item name).
STEPPER_QTY = ("xpath=//span[string-length(normalize-space())>0 and translate(normalize-space(),'0123456789','')=''"
               " and ancestor::div[.//h4][1]//h4]")
ALL_MINUS = STEPPER_QTY[6:]  # used with the preceding:: axis below


def _repeat_prompt(p):
    """Customizable items ask whether to repeat the last customization when the quantity goes up."""
    btn = p.locator("button:visible, div[role='button']:visible").filter(has_text=re.compile(r"^\s*Repeat", re.I))
    if btn.count() > 0:
        btn.first.click()
        p.wait_for_timeout(800)


def _clear_cart(p, max_clicks: int = 60) -> int:
    """The browser profile remembers carts between runs; leftovers would be ordered too. Remove them."""
    removed = 0
    for _ in range(max_clicks):
        if _cart(p)["count"] == 0:
            break
        qty = p.locator(STEPPER_QTY)
        if qty.count() == 0:
            break
        minus = qty.first.locator("xpath=preceding::*[local-name()='svg'][1]")
        minus.scroll_into_view_if_needed()
        minus.click()
        p.wait_for_timeout(700)
        removed += 1
    return removed


def _add_item(p, name: str, qty: int) -> dict:
    real_name, card = _item_card(p, name)
    price = _rupees(card.inner_text())
    card.get_by_text("ADD", exact=True).first.click()
    p.wait_for_timeout(1200)
    # Items with options open a customisation sheet: accept defaults with its add button.
    sheet_btn = p.locator("button:visible, div[role='button']:visible").filter(has_text=re.compile(r"^\s*Add item", re.I))
    if sheet_btn.count() > 0:
        sheet_btn.first.click()
        p.wait_for_timeout(1200)
    for _ in range(qty - 1):
        card.locator(QTY_SPAN).first.locator("xpath=following::*[local-name()='svg'][1]").click()
        p.wait_for_timeout(700)
        _repeat_prompt(p)
    shown = card.locator(QTY_SPAN)
    shown_qty = int(shown.first.inner_text()) if shown.count() else 0
    return {"name": real_name, "qty": qty, "unit_price": price, "qty_on_card": shown_qty}


def _cart(p) -> dict:
    body = p.inner_text("body")
    n = re.search(r"Your Order\s*\((\d+)\)", body)
    sub = re.search(r"Subtotal:\s*₹\s*([\d,]+(?:\.\d+)?)", body)
    return {"count": int(n.group(1)) if n else 0,
            "subtotal": float(sub.group(1).replace(",", "")) if sub else None}


def _confirmation_seen(p) -> str:
    text = p.inner_text("body") if p else ""
    hit = re.search(r"(Order (?:placed|confirmed|is being prepared)[^\n]{0,80}|Order ID[:\s#]*\w+)", text, re.I)
    if hit or re.search(r"/order[-_]?(?:status|details|track)", p.url or "", re.I):
        return hit.group(0) if hit else p.url
    return ""


def _run(restaurant, city, items, place, wait_for_payment_s):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = webpage.page()
    url = _open_restaurant(p, restaurant, city)
    cleared = _clear_cart(p)
    if _cart(p)["count"] != 0:
        return {"ok": False, "stage": "cart", "error": f"Could not empty the existing cart ({_cart(p)}); not continuing."}
    added = [_add_item(p, it["name"], int(it.get("qty", 1))) for it in items]
    p.wait_for_timeout(1000)
    cart = _cart(p)
    expected_count = sum(a["qty"] for a in added)
    expected_sub = sum((a["unit_price"] or 0) * a["qty"] for a in added)
    shot = os.path.join(OUT_DIR, f"zomato_cart_{time.strftime('%Y%m%d_%H%M%S')}.png")
    p.screenshot(path=shot)
    checks = {
        "each_item_quantity_matches": all(a["qty_on_card"] == a["qty"] for a in added),
        "cart_count_matches": cart["count"] == expected_count,
        "subtotal_matches_menu_prices": cart["subtotal"] is not None and abs(cart["subtotal"] - expected_sub) < 0.5,
    }
    out = {"restaurant_url": url, "items": added, "cart": cart, "expected_subtotal": expected_sub,
           "leftover_items_removed": cleared,
           "cart_checks": checks, "screenshot": shot, "path": shot, "stage": "cart_built"}
    if not all(checks.values()):
        out.update(ok=False, error=f"Cart does not match the request: {cart} vs expected {expected_count} items / "
                                   f"₹{expected_sub:.0f}. Not proceeding.")
        return out
    if not place:
        out.update(ok=True, note="Cart built and verified. Not ordering (place=false).")
        return out
    out["_needs_approval"] = True
    return out


def _open_checkout(address: str) -> dict:
    """Continue -> checkout page (free, nothing is bought), select the saved address, read the final total."""
    p = webpage.page()
    p.locator("text=/^\s*Continue\s*$/").first.click()
    p.wait_for_timeout(2000)
    body = p.inner_text("body")
    if re.search(r"Log in|Enter (?:your )?(?:phone|mobile)|Login to continue", body, re.I) and             not re.search(r"address", body, re.I):
        return {"ok": False, "stage": "login_required",
                "error": "Zomato needs you to log in first: python seguro/tools/zomato.py --login (you sign in yourself)."}
    # Saved addresses show their label (Home / Work / Other) as text on a selectable card.
    label = p.get_by_text(re.compile(rf"^\s*{re.escape(address)}\s*$", re.I))
    if label.count() == 0:
        change = p.get_by_text(re.compile(r"^\s*(Change|Select address|Add address)", re.I))
        if change.count():
            change.first.click(); p.wait_for_timeout(2000)
            label = p.get_by_text(re.compile(rf"^\s*{re.escape(address)}\s*$", re.I))
    if label.count() == 0:
        shot = os.path.join(OUT_DIR, f"zomato_checkout_{time.strftime('%Y%m%d_%H%M%S')}.png")
        p.screenshot(path=shot)
        return {"ok": False, "stage": "address_not_found", "path": shot,
                "error": f"No saved address labelled '{address}' on the checkout page. Nothing ordered."}
    # Each saved-address card has its own "Deliver here" button; clicking the label alone selects nothing.
    card = label.first.locator("xpath=ancestor::div[.//*[normalize-space()='Deliver here']][1]")
    if card.count():
        card.get_by_text("Deliver here", exact=True).first.click()
    else:
        label.first.click()
    p.wait_for_timeout(3000)
    body = p.inner_text("body")
    # Selected state (probed): "Delivery at Home" + the address line, and no "please select" prompt.
    m_sel = re.search(rf"Delivery at\s*{re.escape(address)}\s*\n+\s*([^\n]+)", body, re.I)
    selected = bool(m_sel) and "Please select a delivery address" not in body
    address_line = m_sel.group(1).strip() if m_sel else ""
    total = None
    for pat in (r"To Pay[^\d₹]*₹\s*([\d,]+(?:\.\d+)?)", r"Grand Total[^\d₹]*₹\s*([\d,]+(?:\.\d+)?)",
                r"Total[^\d₹]*₹\s*([\d,]+(?:\.\d+)?)"):
        m = re.search(pat, body, re.I)
        if m:
            total = float(m.group(1).replace(",", ""))
            break
    shot = os.path.join(OUT_DIR, f"zomato_checkout_{time.strftime('%Y%m%d_%H%M%S')}.png")
    p.screenshot(path=shot)
    return {"ok": bool(selected) and total is not None, "stage": "checkout_ready", "address": address,
            "address_line": address_line, "address_selected": bool(selected), "final_total": total, "path": shot,
            **({} if selected and total is not None else
               {"error": f"Checkout not confirmed: address '{address}' selected={bool(selected)}, total={total}. Nothing ordered."})}


def _wait_for_order(wait_for_payment_s: int) -> dict:
    """The USER presses pay and completes payment; Seguro only watches for Zomato's confirmation."""
    p = webpage.page()

    deadline = time.time() + wait_for_payment_s
    while time.time() < deadline:
        seen = _confirmation_seen(p)
        if seen:
            shot = os.path.join(OUT_DIR, f"zomato_confirmation_{time.strftime('%Y%m%d_%H%M%S')}.png")
            p.screenshot(path=shot)
            return {"ok": True, "stage": "order_confirmed", "confirmation": seen, "path": shot}
        time.sleep(3)
    return {"ok": False, "stage": "awaiting_payment",
            "error": f"No order confirmation within {wait_for_payment_s}s. Nothing is claimed as ordered; "
                     f"finish or cancel payment in the Brave window."}


@registry.register(
    name="zomato_order",
    description=("Builds a Zomato cart (restaurant, city, items with quantities) and verifies it against menu prices. "
                 "With place=true it asks the user to approve the exact cart, opens checkout, lets the USER pay, "
                 "and reports the order only after Zomato shows a confirmation. Never enters payment details."),
    parameters={
        "type": "object",
        "properties": {
            "restaurant": {"type": "string"},
            "city": {"type": "string", "description": "Zomato city slug, e.g. pune"},
            "items": {"type": "array", "items": {"type": "object"}, "description": "[{name, qty}]"},
            "place": {"type": "boolean", "description": "true = go on to checkout after approval (default false)"},
            "wait_for_payment_s": {"type": "integer", "description": "How long to wait for the user to pay (default 300)"},
            "address": {"type": "string", "description": "Label of a saved Zomato address (default 'Home')"}
        },
        "required": ["restaurant", "city", "items"]
    }
)
def zomato_order(restaurant: str, city: str, items: list, place: bool = False, wait_for_payment_s: int = 300,
                 address: str = "Home") -> dict:
    import asyncio
    import json
    items = [json.loads(i) if isinstance(i, str) else i for i in items]
    if not items or not all(isinstance(i, dict) and i.get("name") for i in items):
        return {"ok": False, "error": "items must be a list like [{'name': 'Misal Slice', 'qty': 2}]"}
    try:
        res = webpage.run_on_browser_thread(_run, restaurant, city, items, place, wait_for_payment_s)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "stage": "cart"}
    if not res.pop("_needs_approval", False):
        return res
    # Checkout page (free) with the saved address selected and the final payable total read back.
    try:
        chk = webpage.run_on_browser_thread(_open_checkout, address)
    except Exception as e:
        chk = {"ok": False, "stage": "checkout", "error": f"{type(e).__name__}: {e}"}
    res.update({k: v for k, v in chk.items() if k != "ok"})
    if not chk.get("ok"):
        res["ok"] = False
        return res
    # Purchase gate with the VERIFIED cart, address and final total, every time. Never auto-approved.
    from seguro.core.workflow.gates import broker
    summary = (f"Place a Zomato order at {restaurant} ({city}) to your '{address}' address "
               f"({chk.get('address_line', '')[:70]}): " +
               ", ".join(f"{a['qty']} x {a['name']} (₹{a['unit_price']:.0f})" for a in res["items"]) +
               f" | items ₹{res['cart']['subtotal']:.0f}, total to pay ₹{chk['final_total']:.0f}. "
               f"You will complete the payment yourself in the Brave window.")
    try:
        loop = asyncio.new_event_loop()
        approved = loop.run_until_complete(broker.confirm(summary, tool="zomato_place_order",
                                                          details={"cart": res["cart"], "total": chk["final_total"]}))
        loop.close()
    except Exception as e:
        approved = False
        res["approval_error"] = str(e)

    if not approved:
        res.update(ok=False, stage="checkout_ready_not_approved",
                   note="You did not approve, so nothing was ordered. Checkout is open in Brave if you change your mind.")
        return res
    print(f"\n💳 Approved. Complete the payment yourself in the Brave window (Seguro never enters payment details). "
          f"Waiting up to {wait_for_payment_s}s for Zomato's order confirmation...")
    try:
        done = webpage.run_on_browser_thread(_wait_for_order, int(wait_for_payment_s))
    except Exception as e:
        done = {"ok": False, "stage": "awaiting_payment", "error": f"{type(e).__name__}: {e}"}
    res.update(done)
    return res


if __name__ == "__main__" and "--login" in sys.argv:
    import subprocess
    from seguro.tools.browser import _get_brave_path
    os.makedirs(webpage.PROFILE_DIR, exist_ok=True)
    proc = subprocess.Popen([_get_brave_path(), f"--user-data-dir={webpage.PROFILE_DIR}", "--no-first-run",
                             "https://www.zomato.com/"])
    input("Log in to Zomato in the Brave window that opened (Seguro profile) and set your delivery address.\n"
          "Then CLOSE that Brave window and press Enter here... ")
    print("Saved." if proc.poll() is not None else "Close that Brave window so Seguro can use the profile.")
