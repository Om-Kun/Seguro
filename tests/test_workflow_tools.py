"""Tests for tools added for workflows. Network tests skip when offline."""
import os
import socket
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import seguro.core.orchestrator  # noqa: F401
from seguro.core.verifier import verifier
from seguro.tools.registry import registry

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _online():
    try:
        socket.create_connection(("www.bing.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_pptx_create_with_image_is_verified(tmp_path):
    from seguro.tools.charts import chart_png
    img = os.path.join(DESKTOP, "seguro_t_chart.png")
    deck = os.path.join(DESKTOP, "seguro_t_deck.pptx")
    try:
        chart_png([["k", "v"], ["a", 1], ["b", 2]], img)
        res = await registry.dispatch({"tool": "pptx_create", "args": {
            "filename": deck, "slides": [{"title": "One", "text": "hello", "image": img}, {"title": "Two"}]}})
        assert res["ok"], res
        v = verifier.verify_action("pptx_create", {}, res)
        assert v["verified"] and "2 slide(s)" in v["evidence"]
    finally:
        for p in (img, deck):
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.asyncio
async def test_process_start_waits_for_port_and_stops():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    res = await registry.dispatch({"tool": "process_start", "args": {
        "name": "t_http", "command": f"python -m http.server {port} --bind 127.0.0.1", "cwd": "~/Desktop",
        "wait_port": port}})
    try:
        assert res["ok"], res
        assert verifier.verify_action("process_start", {}, res)["verified"]
    finally:
        stop = await registry.dispatch({"tool": "process_stop", "args": {"name": "t_http"}})
    assert stop["ok"]


@pytest.mark.asyncio
async def test_process_start_reports_crash_honestly():
    res = await registry.dispatch({"tool": "process_start", "args": {
        "name": "t_bad", "command": "python -c \"import sys; sys.exit(3)\"", "cwd": "~/Desktop", "wait_port": 59999,
        "timeout_s": 5}})
    assert res["ok"] is False and "exited" in res["error"]


@pytest.mark.asyncio
async def test_browser_page_fill_click_text_screenshot_on_local_page():
    from seguro.tools import webpage
    page = os.path.join(DESKTOP, "seguro_t_form.html")
    shot = os.path.join(DESKTOP, "seguro_t_form.png")
    _write(page, "<title>Form</title><input id='note' placeholder='New note'>"
                          "<button onclick=\"var li=document.createElement('li');li.textContent="
                          "document.getElementById('note').value;document.getElementById('l').appendChild(li)\">Add</button>"
                          "<ul id='l'></ul>")
    try:
        r = await registry.dispatch({"tool": "browser_page_open", "args": {"url": page}})
        assert r["ok"] and r["data"]["title"] == "Form", r
        r = await registry.dispatch({"tool": "browser_page_fill", "args": {"target": "New note", "text": "buy milk"}})
        assert r["ok"] and verifier.verify_action("browser_page_fill", {"text": "buy milk"}, r)["verified"]
        r = await registry.dispatch({"tool": "browser_page_click", "args": {"target": "Add"}})
        assert r["ok"] and "buy milk" in r["data"]["text"]
        r = await registry.dispatch({"tool": "browser_page_screenshot", "args": {"filename": shot}})
        assert r["ok"] and verifier.verify_action("browser_page_screenshot", {}, r)["verified"]
    finally:
        webpage.close_browser()
        for p in (page, shot):
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.asyncio
async def test_browser_click_refuses_send_buttons():
    from seguro.tools import webpage
    from seguro.core.workflow.gates import broker
    async def deny(reason, details):
        return False
    broker.configure(responder=deny, auto_approve=set())
    page = os.path.join(DESKTOP, "seguro_t_send.html")
    _write(page, "<title>S</title><button id='b' onclick=\"document.title='SENT'\">Send</button>")
    try:
        await registry.dispatch({"tool": "browser_page_open", "args": {"url": page}})
        r1 = await registry.dispatch({"tool": "browser_page_click", "args": {"target": "Send"}})   # gate by name
        r2 = await registry.dispatch({"tool": "browser_page_click", "args": {"target": "#b"}})    # label at click time
        assert r1["ok"] is False and r2["ok"] is False
        t = await registry.dispatch({"tool": "browser_page_text", "args": {}})
        assert t["data"]["title"] == "S"
    finally:
        webpage.close_browser()
        os.remove(page)


@pytest.mark.skipif(not _online(), reason="offline")
@pytest.mark.asyncio
async def test_news_search_returns_real_articles():
    r = await registry.dispatch({"tool": "news_search", "args": {"query": "artificial intelligence", "count": 5}})
    assert r["ok"], r
    t = r["data"]["table"]
    assert t[0][:3] == ["Title", "Source", "Summary"] and len(t) == 6
    assert all(row[0] and row[1] for row in t[1:])
