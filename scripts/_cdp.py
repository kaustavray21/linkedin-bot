"""Minimal Chrome DevTools Protocol driver — no new dependencies.

Uses the Chrome already installed at /usr/bin/google-chrome and the `websockets`
package already in bot-env. Exists for the S1 spike only.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time

import httpx
import websockets

CHROME = "/usr/bin/google-chrome"

LAUNCH_FLAGS = [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--mute-audio",
    "--window-size=1280,2000",
    "--remote-debugging-port=0",
]


class Browser:
    def __init__(self, profile_dir: str | None = None):
        self.profile_dir = profile_dir or tempfile.mkdtemp(prefix="s1-chrome-")
        self.proc: subprocess.Popen | None = None
        self.ws_url: str | None = None
        self._next_id = 0

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        args = [CHROME, *LAUNCH_FLAGS, f"--user-data-dir={self.profile_dir}", "about:blank"]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        port_file = os.path.join(self.profile_dir, "DevToolsActivePort")
        for _ in range(200):
            if os.path.exists(port_file):
                raw = open(port_file).read().split("\n")
                if len(raw) >= 2:
                    port = raw[0].strip()
                    async with httpx.AsyncClient() as c:
                        r = await c.get(f"http://127.0.0.1:{port}/json/version")
                    self.ws_url = r.json()["webSocketDebuggerUrl"]
                    self.port = port
                    return
            await asyncio.sleep(0.1)
        raise RuntimeError("Chrome did not expose a debugging port")

    async def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile_dir, ignore_errors=True)

    # ------------------------------------------------------------------- memory
    def rss_kb(self) -> int:
        """Peak-ish RSS across the whole Chrome process tree, in KB."""
        if not self.proc:
            return 0
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "--ppid", str(self.proc.pid)],
                capture_output=True, text=True,
            ).stdout
            children = sum(int(x) for x in out.split() if x.strip().isdigit())
            own = int(open(f"/proc/{self.proc.pid}/statm").read().split()[1]) * 4
            return children + own
        except Exception:
            return 0


class Page:
    """One tab, driven over a flattened CDP session."""

    def __init__(self, browser: Browser):
        self.browser = browser
        self.ws = None
        self.session_id = None
        self.target_id = None
        self._id = 0
        self._frame_id = None
        self._events: list[dict] = []

    async def __aenter__(self):
        self.ws = await websockets.connect(
            self.browser.ws_url, max_size=200 * 1024 * 1024, ping_interval=None
        )
        res = await self._send("Target.createTarget", {"url": "about:blank"})
        self.target_id = res["targetId"]
        res = await self._send(
            "Target.attachToTarget", {"targetId": self.target_id, "flatten": True}
        )
        self.session_id = res["sessionId"]
        await self._send("Page.enable", session=True)
        await self._send("Network.enable", session=True)
        return self

    async def __aexit__(self, *exc):
        try:
            await self._send("Target.closeTarget", {"targetId": self.target_id})
        except Exception:
            pass
        if self.ws:
            await self.ws.close()

    async def _send(self, method: str, params: dict | None = None, session: bool = False):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session or (self.session_id and method != "Target.attachToTarget"
                       and not method.startswith("Target.")):
            msg["sessionId"] = self.session_id
        await self.ws.send(json.dumps(msg))
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get("id") == self._id:
                if "error" in raw:
                    raise RuntimeError(f"{method}: {raw['error']}")
                return raw.get("result", {})
            self._events.append(raw)

    async def _drain(self, seconds: float):
        """Collect events for a fixed window."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(
                    self.ws.recv(), timeout=max(0.05, end - time.monotonic())
                )
            except asyncio.TimeoutError:
                break
            self._events.append(json.loads(raw))

    # ------------------------------------------------------------------ actions
    async def goto(self, url: str, settle: float = 6.0, timeout: float = 45.0):
        """Navigate, wait for load, then let client-side hydration settle."""
        t0 = time.monotonic()
        self._events.clear()
        nav = await self._send("Page.navigate", {"url": url}, session=True)
        self._frame_id = nav.get("frameId")

        loaded = False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=end - time.monotonic())
            except asyncio.TimeoutError:
                break
            evt = json.loads(raw)
            self._events.append(evt)
            if evt.get("method") == "Page.loadEventFired":
                loaded = True
                break

        # Hydration happens after load; this is the window counts appear in.
        await self._drain(settle)
        elapsed = time.monotonic() - t0

        status, final_url = self._main_response(url)
        return {"loaded": loaded, "elapsed": elapsed, "status": status,
                "final_url": final_url}

    def _main_response(self, url: str):
        """Status + final URL of the MAIN-FRAME document response.

        Subframes (ad iframes, Google sign-in widgets) also emit Document
        responses; taking the last one reports an unrelated third-party URL as
        the page's own. Filter to the frame Page.navigate returned.
        """
        status, final_url = None, url
        for evt in self._events:
            if evt.get("method") != "Network.responseReceived":
                continue
            p = evt.get("params", {})
            if p.get("type") != "Document":
                continue
            if self._frame_id and p.get("frameId") != self._frame_id:
                continue
            r = p.get("response", {})
            if status is None:
                status = r.get("status")
            final_url = r.get("url", final_url)
        return status, final_url

    async def html(self) -> str:
        res = await self._send(
            "Runtime.evaluate",
            {"expression": "document.documentElement.outerHTML", "returnByValue": True},
            session=True,
        )
        return res.get("result", {}).get("value") or ""

    async def evaluate(self, expression: str):
        res = await self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            session=True,
        )
        return res.get("result", {}).get("value")
