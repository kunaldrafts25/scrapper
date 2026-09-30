"""Local browser smoke against a mocked fetcher; no external requests."""
import json
import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import websocket

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verified_extraction import api
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
from verified_extraction.store import Store

temporary = tempfile.TemporaryDirectory(prefix="ve-browser-")
root = Path(temporary.name)
api.store = Store(str(root / "browser_test.sqlite3"))
os.environ["VE_KEYS"] = json.dumps({"browser": "browser-secret"})
os.environ["VE_LOCAL_UI"] = "1"

class Fetcher:
    def fetch(self, url):
        return Page(url, '<title>Team plans</title><meta name="description" content="Plans for growing teams.">'
                    '<h1>Team plans</h1><article><h2>Team</h2><p>Price: USD 29 per user/month, billed annually</p></article>'
                    '<p>Support: Email</p><script>alert(1)</script>', 'now', [])

api.run_hard = lambda request, on_tick=None: run_job(request, Fetcher())
server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=18764, log_level="error"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(50):
    try:
        urllib.request.urlopen("http://127.0.0.1:18764/review", timeout=1).close()
        break
    except OSError:
        time.sleep(.1)

chrome = shutil.which("chrome") or shutil.which("chromium") or shutil.which("google-chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
profile = root / "chrome-browser-test"
process = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-first-run",
    "--no-default-browser-check", "--remote-debugging-port=18765", "--remote-allow-origins=*",
    f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

try:
    pages = None
    for _ in range(100):
        try:
            pages = json.load(urllib.request.urlopen("http://127.0.0.1:18765/json", timeout=1))
            break
        except OSError:
            time.sleep(.1)
    assert pages
    ws = websocket.create_connection(next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page"), timeout=10)
    serial = 0
    def command(method, params=None):
        global serial
        serial += 1
        ws.send(json.dumps({"id":serial,"method":method,"params":params or {}}))
        while True:
            event = json.loads(ws.recv())
            if event.get("id") == serial:
                if "error" in event:
                    raise RuntimeError(event["error"])
                return event.get("result", {})
    def evaluate(expr):
        out = command("Runtime.evaluate", {"expression":expr,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in out:
            raise RuntimeError(out["exceptionDetails"])
        return out["result"].get("value")
    command("Page.navigate", {"url":"http://127.0.0.1:18764/review"})
    for _ in range(50):
        if evaluate("document.readyState") == "complete":
            break
        time.sleep(.1)
    assert evaluate("document.querySelectorAll('#fields .field-row').length") == 3
    evaluate("document.querySelector('#quick-url').value='https://example.org/pricing'; document.querySelector('#quick-form').requestSubmit()")
    for _ in range(100):
        if evaluate("document.querySelectorAll('.auto-fact').length >= 3"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelectorAll('.auto-fact').length >= 3")
    for _ in range(30):
        if evaluate("document.querySelector('#result-panel').getBoundingClientRect().top < window.innerHeight"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelector('#result-panel').getBoundingClientRect().top < window.innerHeight")
    assert evaluate("document.querySelector('#automatic-result').textContent.includes('Plans for growing teams.')")
    assert evaluate("document.querySelector('#recent-list').textContent.includes('example.org/pricing')")
    assert evaluate("document.querySelector('#automatic-result').innerHTML.includes('<script>alert(1)</script>')") is False
    if os.environ.get("VE_BROWSER_SCREENSHOT"):
        command("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 1700,
                "deviceScaleFactor": 1, "mobile": False})
        Path(os.environ["VE_BROWSER_SCREENSHOT"]).write_bytes(base64.b64decode(
            command("Page.captureScreenshot", {"format": "png"})["data"]))
    evaluate("document.querySelector('#advanced-workflow').open=true")
    evaluate("document.querySelector('#url').value='https://example.org/pricing'; document.querySelector('#key').value='browser-secret'; document.querySelector('#reviewer').value='analyst'; document.querySelector('#create-form').requestSubmit();")
    for _ in range(100):
        if evaluate("document.querySelectorAll('.review-card').length === 3"):
            break
        time.sleep(.1)
    assert evaluate("!document.querySelector('#result-panel').hidden")
    assert evaluate("document.querySelectorAll('.review-card').length") == 3
    assert evaluate("document.querySelector('#result-panel').innerHTML.includes('<script>alert(1)</script>')") is False
    assert evaluate("document.querySelector('#result-panel').textContent.includes('Support: Email')")
    job_id = evaluate("document.querySelector('#job').value")
    assert job_id
    evaluate("document.querySelector('#open-form').requestSubmit()")
    for _ in range(30):
        if evaluate("document.querySelector('#notice').textContent.includes('Loaded 3 fields')"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelector('#notice').textContent.includes('Loaded 3 fields')")
    evaluate("document.querySelector('.review-card .session button').click()")
    for _ in range(30):
        if evaluate("document.querySelector('.review-card .hint:last-child').textContent.includes('active')"):
            break
        time.sleep(.1)
    evaluate("document.querySelectorAll('.review-card .session button')[1].click()")
    time.sleep(.25)
    evaluate("document.querySelector('.review-card select').value='correct'; document.querySelectorAll('.review-card .session button')[2].click()")
    for _ in range(40):
        if evaluate("document.querySelector('#history').textContent.includes('correct')"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelector('#history').textContent.includes('correct')")
    evaluate("document.querySelector('#export').click()")
    time.sleep(.3)
    assert evaluate("document.querySelector('#notice').textContent.includes('downloaded')")
    evaluate("document.querySelector('#key').value='bad-key'; document.querySelector('#open-form').requestSubmit()")
    for _ in range(30):
        if evaluate("document.querySelector('#notice').textContent.includes('UNAUTHORIZED')"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelector('#notice').textContent.includes('UNAUTHORIZED')")
    command("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
    assert evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
    evaluate("document.querySelector('#advanced-workflow').open=true; document.querySelector('#url').focus()")
    assert evaluate("document.activeElement.id") == "url"
    command("Input.dispatchKeyEvent", {"type":"keyDown","key":"Tab","code":"Tab","windowsVirtualKeyCode":9})
    command("Input.dispatchKeyEvent", {"type":"keyUp","key":"Tab","code":"Tab","windowsVirtualKeyCode":9})
    assert evaluate("document.activeElement.id") == "target-plan"
    command("Input.dispatchKeyEvent", {"type":"keyDown","key":"Tab","code":"Tab","windowsVirtualKeyCode":9})
    command("Input.dispatchKeyEvent", {"type":"keyUp","key":"Tab","code":"Tab","windowsVirtualKeyCode":9})
    assert evaluate("document.activeElement.id") == "key"
    evaluate("document.querySelector('#key').value='browser-secret'; document.querySelector('#delete-job').click()")
    for _ in range(50):
        if evaluate("document.querySelector('#result-panel').hidden"):
            break
        time.sleep(.1)
    assert evaluate("document.querySelector('#result-panel').hidden")
    print(json.dumps({"browser":"Chrome headless","submitted":bool(job_id),"reopened":True,
        "evidence_visible":True,"fields":3,"reviewed":True,"history":True,"exported":True,
        "unauthorized_error":True,"mobile_no_horizontal_overflow":True,"keyboard_tab":True,"deleted":True}))
finally:
    process.terminate()
    process.wait(timeout=5)
    server.should_exit = True
    thread.join(timeout=5)
    temporary.cleanup()
