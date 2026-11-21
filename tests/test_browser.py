"""Browser control (#86), with the IDE's screen: the local server `b` starts, reached as the page reaches it.

Run like tests/test_app.py, locally and not in CI, in a venv with the `browser` extra (aiohttp). Every tmux read and
write goes to the stub PaneWindows, and the starter and focuser are stubs, so nothing reaches tmux.

The server as these tests use it, which the page's own script uses too:

- `b` (or the command palette's `Browser control: start` / `Browser control: stop`) starts and stops it. While it
  runs, the status line's `browser` slot is `browser http://127.0.0.1:<port>/<token>/`, the base below.
- A coordination session is named by its row key in the terminal's issues table: `<number>/<pane>` under an issue
  with several, or the issue's own row `<number>` for an issue's one session, as `s` on that row goes to it.
- `GET <base>`: the page. `GET <base>state`: the snapshot. `GET <base>records?row=<row key>` (a coordination
  session's row) or `?run=<run name>&session=<name>` (a work session): the session's records.
- `GET <base>ws`: the page's WebSocket. Its first message is `{"type": "hello", "page": <page id>}`; then
  `{"type": "state", "state": <snapshot>}` at once and after each read the screen shows; `{"type": "message",
  "text": ...}` for what the IDE says to this page; `{"type": "instructions", "repository", "number", "opener"}` when
  this page's instructions box opens; `{"type": "frame", "pane", "rows", "cursor": [x, y], "cursor_visible",
  "width", "height"}` for the open view; `{"type": "stopped"}` at stop.
- `POST <base>action`, `Content-Type: application/json`, `Origin` the server's own: `{"page": <page id>, "action":
  <name>, ...}`, answered `200` once accepted; what it does is told to that page over its WebSocket. The actions:
  `select` {row}; `start` {row} (s); `new-session` {row} (S); `instructions` {repository, number, opener, text}
  (Enter in the page's box); `go` {row} or {run, session}; `close-view`; `message` {row or run, session; text};
  `keys` {row or run, session; key}.
- The snapshot: `{"issues": [{"key": <row key>, "cells": [<text>, ...]}, ...], "chosen": {"key", "info": [[label,
  value], ...], "steps": [[<cell>, ...], ...], "sessions": [[<cell>, ...], ...]}}`, the rows as the terminal's
  tables have them.
- The records: `{"screen": <capture-pane -p>, "about": [<line>, ...], "files": [{"name", "modified", "data",
  "notes"}, ...]}`, or `{"error": <text>}`.
"""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest
from textual.widgets import DataTable, Input, Static
from textual.worker import WorkerCancelled, WorkerState

from autoflow_ide.app import IdeApp
from autoflow_ide.issues import IssueSourceError
from autoflow_ide.model import Issue
from autoflow_ide.windows import Window, WindowError
from test_app import RECORDED_RUNS

FIXTURES = Path(__file__).parent / "fixtures"
SHAPE = (FIXTURES / "tmux-pane-shape.txt").read_text()
CAPTURE = (FIXTURES / "tmux-capture-e.txt").read_text()
TURN_END = (FIXTURES / "screens" / "turn-end.txt").read_text()
CHOICE = (FIXTURES / "screens" / "choice-prompt.txt").read_text()
NBSP = "\xa0"
REPO = "Munsik-Park/autoflow-sandbox"
WIDE = (200, 40)
RUN_3 = "autoflow-sandbox-3-sample"          # issue 3's run in RECORDED_RUNS: S1 in %1, S2 in %2 (#86)
SID = "00000000-0000-4000-8000-0000000000aa"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def drafted(screen, text):
    return screen.replace(f"\n❯{NBSP}\n", f"\n❯{NBSP}{text}\n")


class PaneWindows:
    """TmuxWindows as the IDE uses it, without tmux: the coordination session windows by issue, and each pane's
    reads (#86). A pane in `gone` fails every read as tmux does (`can't find pane: %N`), except its option, which
    reads empty; the others give `frames`, `texts` (capture-pane -p, in turn, then the last), `ids` and `modes`."""

    def __init__(self, windows=None):
        self.open = {n: list(ws) for n, ws in (windows or {}).items()}
        self.gone, self.broken = set(), {}
        self.frames, self.texts, self.ids, self.modes = {}, {}, {}, {}
        self.window_screens = {}
        self.reads, self.writes = [], []
        self.find_delay, self.find_error, self.on_find = 0.0, None, None
        self.text_reads = {}

    async def find(self, workspace):
        if self.on_find:
            hook, self.on_find = self.on_find, None
            hook()
        if self.find_delay:
            await asyncio.sleep(self.find_delay)
        if self.find_error:
            raise self.find_error
        return {n: list(ws) for n, ws in self.open.items() if ws}

    async def screens(self, window):
        return [self.window_screens.get(window.id, "> ")] + ["> "] * (len(window.panes) - 1)

    async def screen(self, window):
        return "\n".join(await self.screens(window))

    async def close(self, window):
        for ws in self.open.values():
            if window in ws:
                ws.remove(window)

    def read(self, what, pane):
        self.reads.append((what, pane))
        if pane in self.broken:
            raise self.broken[pane]
        if pane in self.gone and what != "session_id":
            raise WindowError(f"can't find pane: {pane}")

    async def frame(self, pane):
        from autoflow_ide.windows import parse_frame
        self.read("frame", pane)
        return parse_frame(*self.frames.get(pane, (SHAPE, CAPTURE)))

    async def pane_screen(self, pane):
        self.read("screen", pane)
        shown = self.texts.get(pane, [TURN_END])
        n = self.text_reads[pane] = self.text_reads.get(pane, 0) + 1
        return shown[min(n, len(shown)) - 1]

    async def session_id(self, pane):
        self.read("session_id", pane)
        return "" if pane in self.gone else self.ids.get(pane, "")

    async def in_mode(self, pane):
        self.read("in_mode", pane)
        return self.modes.get(pane, False)

    async def paste(self, pane, text):
        self.writes.append(("paste", pane, text))

    async def send_keys(self, pane, *keys):
        self.writes.append(("send-keys", pane, " ".join(keys)))

    def frame_reads(self, pane=None):
        return [p for what, p in self.reads if what == "frame" and (pane is None or p == pane)]


async def open_lookup(repository, number):
    return Issue(number, f"issue {number}", state="open")


class Harness:
    """An IdeApp with stubs, and what they were asked: the starts, with their keyword arguments, and the focus
    moves; `issues` and `runs` are what the next reads give (#86)."""

    def __init__(self, windows=None, issues=None, runs=None, lookup=open_lookup, coordinator="/c", gate=None, **kw):
        self.windows = windows or PaneWindows()
        self.issues = list(issues) if issues is not None else [Issue(4, "four"), Issue(3, "three")]
        self.runs = list(runs) if runs is not None else list(RECORDED_RUNS)
        self.starts, self.focused, self.run_reads = [], [], 0
        self.gate = gate

        async def loader(repository):
            return [Issue(i.number, i.title, state=i.state, pull=i.pull) for i in self.issues]

        async def run_loader(coordinator):
            self.run_reads += 1
            return list(self.runs)

        async def starter(coordinator, repository, number, instructions="", **options):
            self.starts.append(((repository, number, instructions), options))
            if self.gate is not None:
                await self.gate.wait()
            return f"%{number + 3}"

        async def focuser(surface):
            self.focused.append(surface)

        async def finder(coordinator, repository):
            return "autoflow-sandbox"

        async def places():
            return {}

        options = dict(run_refresh_sec=1000, issue_refresh_sec=1000, window_check_sec=1000)
        options.update(kw)
        self.app = IdeApp(repository=REPO, loader=loader, coordinator=coordinator, run_loader=run_loader,
                          lookup=lookup, starter=starter, focuser=focuser, windows=self.windows,
                          workspace_finder=finder, placer=places, browser_port=options.pop("browser_port", 0),
                          **options)


async def settle(app, pilot, pause=0.1):
    """Wait for every worker but a view's poll, which runs while the view is open, then for the page's messages."""
    for _ in range(3):
        for worker in [w for w in app.workers if not str(w.group).startswith("browser-view")]:
            try:
                await asyncio.wait_for(worker.wait(), 10)
            except WorkerCancelled:
                pass
        await pilot.pause(pause)


def table_keys(app):
    table = app.query_one("#issues", DataTable)
    return [table.coordinate_to_cell_key((n, 0)).row_key.value for n in range(table.row_count)]


def table_rows(app, name):
    table = app.query_one(f"#{name}", DataTable)
    return [[str(c) for c in table.get_row_at(n)] for n in range(table.row_count)]


def issue_rows(app):
    return list(zip(table_keys(app), table_rows(app, "issues")))


async def highlight(app, pilot, key):
    app.query_one("#issues", DataTable).move_cursor(row=table_keys(app).index(key))
    await settle(app, pilot)


def session_panes(app, number):
    """The panes of issue `number`'s coordination sessions, as the terminal shows them."""
    return [s.pane for s in app.sessions(app.find_issue((REPO, number)))]


async def chosen_session(app, pilot, page, row):
    """The session the page's info shows for `row`, once the page chooses it (#86)."""
    await page.act("select", row=row)
    await settle(app, pilot)
    return dict(page.state()["chosen"]["info"]).get("session")


def cursor_key(app):
    table = app.query_one("#issues", DataTable)
    return table_keys(app)[table.cursor_row]


ADDRESS = re.compile(r"browser (http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]+)/)")


async def browser_on(app, pilot):
    """Press b; the page's address, from the status line."""
    await pilot.press("b")
    await settle(app, pilot)
    found = ADDRESS.fullmatch(app.messages.get("browser", ""))
    assert found, app.messages.get("browser")
    return found[1]


def origin_of(base):
    return base.split("/", 3)[0] + "//" + base.split("/", 3)[2]


class Page:
    """A page connected as the IDE's page connects: its WebSocket, and its actions (#86)."""

    def __init__(self, base):
        self.base, self.origin = base, origin_of(base)
        self.got, self.closed = [], False

    async def __aenter__(self):
        self.http = aiohttp.ClientSession()
        self.ws = await self.http.ws_connect(self.base + "ws", headers={"Origin": self.origin})
        hello = await self.ws.receive_json(timeout=5)
        assert hello["type"] == "hello", hello
        self.id = hello["page"]
        self.reader = asyncio.ensure_future(self.read())
        return self

    async def read(self):
        async for message in self.ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                self.got.append(json.loads(message.data))
        self.closed = True

    async def __aexit__(self, *exc):
        self.reader.cancel()
        await self.ws.close()
        await self.http.close()

    async def act(self, action, **body):
        async with self.http.post(self.base + "action", json={"page": self.id, "action": action, **body},
                                  headers={"Origin": self.origin}) as response:
            assert response.status == 200, (response.status, await response.text())

    async def get(self, path, **params):
        async with self.http.get(self.base + path, params=params) as response:
            assert response.status == 200, (response.status, await response.text())
            return await response.json()

    def of(self, kind):
        return [m for m in self.got if m["type"] == kind]

    def texts(self):
        return [m["text"] for m in self.of("message")]

    def state(self):
        return self.of("state")[-1]["state"]

    def rows(self):
        return [(r["key"], r["cells"]) for r in self.state()["issues"]]


# Browser control on (#86)

async def test_b_starts_the_server_and_its_address_stays_on_the_status_line_until_b_stops_it():
    t = Harness(notice_sec=0.2)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        assert base in str(t.app.query_one("#status", Static).render())
        async with aiohttp.ClientSession() as http:
            async with http.get(base) as response:
                assert response.status == 200 and response.content_type == "text/html"
        await pilot.pause(0.5)                       # past notice_sec: the address is no notice (#86)
        assert ADDRESS.fullmatch(t.app.messages["browser"])
        await pilot.press("b")
        await settle(t.app, pilot)
        assert not t.app.messages.get("browser")
        assert base not in str(t.app.query_one("#status", Static).render())
        async with aiohttp.ClientSession() as http:
            with pytest.raises(aiohttp.ClientConnectorError):
                await http.get(base)


async def test_each_start_has_a_token_of_its_own():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        first = await browser_on(t.app, pilot)
        await pilot.press("b")
        await settle(t.app, pilot)
        second = await browser_on(t.app, pilot)
        assert ADDRESS.fullmatch("browser " + first)[3] != ADDRESS.fullmatch("browser " + second)[3]
        assert len(ADDRESS.fullmatch("browser " + second)[3]) >= 43         # token_urlsafe(32) (#86)


async def test_the_command_palette_starts_and_stops_it_as_b_does():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)

        def command(title):
            found = [c for c in t.app.get_system_commands(t.app.screen) if c.title == title]
            assert len(found) == 1, [c.title for c in t.app.get_system_commands(t.app.screen)]
            return found[0]
        command("Browser control: start").callback()
        await settle(t.app, pilot)
        assert ADDRESS.fullmatch(t.app.messages.get("browser", ""))
        assert not [c for c in t.app.get_system_commands(t.app.screen) if c.title == "Browser control: start"]
        command("Browser control: stop").callback()
        await settle(t.app, pilot)
        assert not t.app.messages.get("browser")
        command("Browser control: start")


async def test_a_port_that_is_taken_starts_nothing_and_says_why():
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    try:
        t = Harness(browser_port=port)
        async with t.app.run_test(size=WIDE) as pilot:
            await settle(t.app, pilot)
            await pilot.press("b")
            await settle(t.app, pilot)
            said = t.app.messages.get("browser", "")
            assert "in use" in said.lower() and "http://" not in said
            assert said in str(t.app.query_one("#status", Static).render())
            # Nothing was left running: b tries to start again, and says why again. (#86)
            assert [c.title for c in t.app.get_system_commands(t.app.screen) if c.title.startswith("Browser")] == [
                "Browser control: start"]
            await pilot.press("b")
            await settle(t.app, pilot)
            assert "in use" in t.app.messages.get("browser", "").lower()
    finally:
        held.close()


async def test_without_aiohttp_b_says_what_to_install_and_the_ide_keeps_running(monkeypatch):
    for name in [n for n in sys.modules if n == "aiohttp" or n.startswith("aiohttp.") or n == "autoflow_ide.browser"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "aiohttp", None)
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        await pilot.press("b")
        await settle(t.app, pilot)
        assert t.app.messages.get("browser") == "browser control needs aiohttp: pip install -e '.[browser]'"
        await pilot.press("down")
        await settle(t.app, pilot)
        assert t.app.is_running and t.app.current.number == 4


def test_the_ide_imports_without_aiohttp():
    # Only the browser command imports the server, and so aiohttp (#86).
    code = ("import importlib.util, sys; sys.modules['aiohttp'] = None; import autoflow_ide.app; "
            "assert importlib.util.find_spec('autoflow_ide.browser') is not None, 'no server module'; "
            "assert 'autoflow_ide.browser' not in sys.modules")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr


# Localhost only (#86)

async def test_the_server_listens_on_127_0_0_1_only():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        port = int(ADDRESS.fullmatch("browser " + base)[2])
        listed = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-a", "-p", str(os.getpid())],
                                capture_output=True, text=True).stdout
        names = re.findall(r"TCP (\S+) \(LISTEN\)", listed)
        assert names == [f"127.0.0.1:{port}"], listed
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))                          # no packet is sent; picks the outbound address (#86)
            other = probe.getsockname()[0]
        except OSError:
            other = None
        finally:
            probe.close()
        if other and other != "127.0.0.1":
            with pytest.raises(OSError):
                socket.create_connection((other, port), timeout=1).close()


# Only the IDE's page (#86)

async def test_requests_without_the_token_or_with_another_host_or_origin_do_nothing():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        root, token = base.rsplit("/", 2)[0], base.rsplit("/", 2)[1]
        port = int(ADDRESS.fullmatch("browser " + base)[2])
        async with Page(base) as page:
            await page.act("start", row="4")
            await settle(t.app, pilot)
            enter = {"page": page.id, "action": "instructions", "repository": REPO, "number": 4, "opener": "s",
                     "text": ""}
            async with aiohttp.ClientSession() as http:
                for url in (root + "/", root + "/state", root + "/" + "x" * len(token) + "/state",
                            root + "/" + token[:-1] + "/state", root + "/" + token + "x/state"):
                    async with http.get(url) as response:
                        assert response.status == 404 and await response.read() == b"", url
                for host in (f"rebind.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}"):
                    async with http.get(base + "state", headers={"Host": host}) as response:
                        assert response.status == 403 and await response.read() == b"", host
                    async with http.post(base + "action", json=enter,
                                         headers={"Host": host, "Origin": f"http://{host}"}) as response:
                        assert response.status == 403
                for headers in ({}, {"Origin": "http://evil.example"}, {"Origin": f"http://localhost:{port + 1}"},
                                {"Origin": "null"}):
                    async with http.post(base + "action", json=enter, headers=headers) as response:
                        assert response.status == 403, headers
                # The request a page of another origin can send without asking: text/plain, with its own Origin. (#86)
                async with http.post(base + "action", data=json.dumps(enter),
                                     headers={"Content-Type": "text/plain", "Origin": page.origin}) as response:
                    assert response.status != 200
                async with http.get(base + "action", params={k: str(v) for k, v in enter.items()},
                                    headers={"Origin": page.origin}) as response:
                    assert response.status != 200
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await http.ws_connect(base + "ws", headers={"Origin": "http://evil.example"})
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await http.ws_connect(base + "ws")
                await settle(t.app, pilot)
                assert t.starts == []
                # Done right, the same Enter is accepted; localhost is the other Host the address can be typed as. (#86)
                async with http.post(f"http://localhost:{port}/{token}/action", json=enter,
                                     headers={"Origin": f"http://localhost:{port}"}) as response:
                    assert response.status == 200
                await settle(t.app, pilot)
                assert [s for s, _ in t.starts] == [(REPO, 4, "")]


async def test_every_response_carries_the_policy_and_no_referrer():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with aiohttp.ClientSession() as http:
            async with http.get(base) as response:
                html = await response.text()
                for name, value in (("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                                    ("Referrer-Policy", "no-referrer")):
                    assert response.headers.get(name) == value, name
            scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
            styles = re.findall(r'<link[^>]*\bhref="([^"]+)"', html)
            assert scripts
            for path in scripts + styles + ["state"]:
                assert not re.match(r"[a-z]+:|//", path), path        # nothing from another origin (#86)
                async with http.get(base + path) as response:
                    assert response.status == 200, path
                    assert response.headers.get("Content-Security-Policy") == CSP, path
                    assert response.headers.get("X-Content-Type-Options") == "nosniff", path


async def test_the_pages_own_script_puts_text_only_and_the_page_has_no_inline_script():
    # Strings the server sends (issue titles, transcripts, screens) are put in the DOM as text only (#86).
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with aiohttp.ClientSession() as http:
            async with http.get(base) as response:
                html = await response.text()
            assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline script"
            assert not re.search(r"\son[a-z]+\s*=", html, re.I), "inline handler"
            own = [p for p in re.findall(r'<script[^>]*\bsrc="([^"]+)"', html) if "xterm" not in p]
            assert own
            for path in own:
                async with http.get(base + path) as response:
                    source = await response.text()
                for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                             "new Function", "setAttribute(\"on", "setAttribute('on"):
                    assert sink not in source, (path, sink)


# The token kept out of logs and off the terminal (#86)

async def test_the_token_is_in_no_log_record_and_nothing_reaches_stderr(caplog, capfd):
    caplog.set_level(logging.DEBUG)
    t = Harness()
    t.windows.open = {4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}
    t.windows.broken["%7"] = RuntimeError("a stub that raises")
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        token = ADDRESS.fullmatch("browser " + base)[3]
        capfd.readouterr()
        async with Page(base):                               # a WebSocket session (#86)
            async with aiohttp.ClientSession() as http:
                async with http.get(base) as response:
                    assert response.status == 200
                async with http.get(base.replace(token, "x" * len(token)) + "state") as response:
                    assert response.status == 404
                async with http.get(base + "records", params={"row": "4"}) as response:
                    answer = await response.json()
            await settle(t.app, pilot)
        assert answer.get("error") and token not in answer["error"]
        for record in caplog.records:
            assert token not in record.getMessage()
            assert token not in (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        assert capfd.readouterr().err == ""


# Browser control off (#86)

async def test_stop_closes_the_page_and_its_view_and_the_ide_keeps_running():
    t = Harness(run_refresh_sec=0.1)
    t.windows.open = {4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("go", row="4")
            await pilot.pause(0.7)
            assert page.of("frame")
            await pilot.press("b")
            await settle(t.app, pilot)
            await pilot.pause(0.3)
            assert page.got[-1]["type"] == "stopped" and page.closed
            reads, run_reads = len(t.windows.frame_reads()), t.run_reads
            await pilot.pause(1.2)
            assert len(t.windows.frame_reads()) == reads                # no poll left running (#86)
            assert t.run_reads > run_reads                               # the IDE's timers still run (#86)
        assert t.app.is_running
        async with aiohttp.ClientSession() as http:
            with pytest.raises(aiohttp.ClientConnectorError):
                await http.get(base)


async def test_quitting_the_ide_stops_the_server():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        page = await Page(base).__aenter__()
        await pilot.press("q")
        await pilot.pause(0.3)
    await asyncio.sleep(0.2)
    assert page.of("stopped") and page.closed
    await page.__aexit__(None, None, None)
    async with aiohttp.ClientSession() as http:
        with pytest.raises(aiohttp.ClientConnectorError):
            await http.get(base)


async def test_stop_cancels_the_browsers_workers_and_answers_no_closed_page():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            t.windows.find_delay = 2.0                   # the start entry's read of the windows is under way (#86)
            await page.act("start", row="4")
            await pilot.pause(0.2)
            await pilot.press("b")
            await pilot.pause(0.3)
            assert not [w for w in t.app.workers if str(w.group).startswith("browser")
                        and w.state in (WorkerState.PENDING, WorkerState.RUNNING)]
            await pilot.pause(2.2)
            kinds = [m["type"] for m in page.got]
            assert "stopped" in kinds and "instructions" not in kinds[kinds.index("stopped"):]


# The same actions as the terminal, each side told its own reasons (#86)

def several_windows():
    return PaneWindows({4: [Window("@1", "autoflow-sandbox#4", ["%7"]), Window("@2", "autoflow-sandbox#4", ["%8"])]})


async def failing_lookup(repository, number):
    raise IssueSourceError("HTTP 502")


async def closed_lookup(repository, number):
    return Issue(number, f"issue {number}", state="closed")


def no_longer_listed(t):
    t.issues = [Issue(3, "three")]
    t.app.action_refresh_issues()


def a_session_meanwhile(t):
    t.windows.open[4] = [Window("@1", "autoflow-sandbox#4", ["%7"])]


def another_open_during_the_reads(t):
    key = (REPO, 4)
    t.windows.on_find = lambda: t.app.opening.__setitem__(key, t.app.opening.get(key, 0) + 1)


def open_in_progress(t):
    t.app.opening[(REPO, 4)] = 1


def windows_unreadable(t):
    t.windows.find_error = WindowError("no server running on /tmp/tmux-501/default")


# (case, Harness arguments, set before the action, set while the box is open (None: refused before the box), row, (#86)
# the terminal's text) (#86)
REFUSALS = [
    ("no coordinator", dict(coordinator=None), None, None, "4", "start: needs --coordinator and --repo or --workspace"),
    ("pull request", dict(issues=[Issue(5, "a pull request", pull=True), Issue(4, "four")]), None, None, "5",
     "#5 is a pull request"),
    ("closed", dict(issues=[Issue(6, "six", state="closed"), Issue(4, "four")]), None, None, "6", "#6 is closed"),
    ("open in progress", {}, open_in_progress, None, "4", "#4: opening the coordination session"),
    ("windows unread", {}, windows_unreadable, None, "4",
     "#4: nothing opened: its windows could not be read: no server running on /tmp/tmux-501/default"),
    ("several sessions", dict(windows=None, several=True), None, None, "4",
     "#4 has 2 sessions: highlight one, or S for another"),
    ("a run", {}, None, None, "3", "#3 already has a run: autoflow-sandbox-3-sample"),
    ("no longer listed at Enter", {}, None, no_longer_listed, "4", "#4 is no longer listed"),
    ("lookup failed at Enter", dict(lookup=failing_lookup), None, (lambda t: None), "4",
     "#4: nothing opened: its state could not be read: HTTP 502"),
    ("closed at Enter", dict(lookup=closed_lookup), None, (lambda t: None), "4", "#4 is closed"),
    ("a session at Enter", {}, None, a_session_meanwhile, "4", "#4 has a session: s goes to it, or S for another"),
    ("another open at Enter", {}, None, another_open_during_the_reads, "4", "#4: opening the coordination session"),
]


def harness(arguments):
    arguments = dict(arguments)
    if arguments.pop("several", False):
        arguments["windows"] = several_windows()
    return Harness(**arguments)


async def terminal_says(arguments, before, enter, row):
    t = harness(arguments)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        await highlight(t.app, pilot, row)
        if before:
            before(t)
        await pilot.press("s")
        await settle(t.app, pilot)
        if enter:
            assert t.app.instructing == (REPO, int(row))
            enter(t)
            await settle(t.app, pilot)
            await pilot.press("enter")
            await settle(t.app, pilot)
        return t.app.messages.get("start")


@pytest.mark.parametrize("case, arguments, before, enter, row, expected", REFUSALS, ids=[r[0] for r in REFUSALS])
async def test_a_refusal_says_the_terminals_words_to_the_page_only(case, arguments, before, enter, row, expected):
    assert await terminal_says(arguments, before, enter, row) == expected
    t = harness(arguments)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            if before:
                before(t)
            await page.act("start", row=row)
            await settle(t.app, pilot)
            if enter:
                assert page.of("instructions")[-1] == {"type": "instructions", "repository": REPO,
                                                       "number": int(row), "opener": "s"}
                enter(t)
                await settle(t.app, pilot)
                await page.act("instructions", repository=REPO, number=int(row), opener="s", text="")
                await settle(t.app, pilot)
            else:
                assert not page.of("instructions")
            assert page.texts()[-1] == expected
            assert not t.app.messages.get("start") and not t.app.messages.get("focus")
            assert t.starts == [] and t.focused == []
            assert not t.app.query_one("#find", Input).display          # the page's box is the page's own (#86)


async def test_a_browser_start_opens_detached_and_moves_neither_the_terminals_cursor_nor_its_box():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        await highlight(t.app, pilot, "3")
        before = (t.app.current, t.app.current_session, t.app.identity)
        async with Page(base) as page:
            await page.act("start", row="4")
            await settle(t.app, pilot)
            assert page.of("instructions") == [{"type": "instructions", "repository": REPO, "number": 4, "opener": "s"}]
            assert not t.app.query_one("#find", Input).display and t.app.instructing is None
            await page.act("instructions", repository=REPO, number=4, opener="s", text="  focus\ton tests ")
            await settle(t.app, pilot)
            assert t.starts == [((REPO, 4, "focus on tests"), {"detached": True})]
            assert (t.app.current, t.app.current_session, t.app.identity) == before and cursor_key(t.app) == "3"
            assert session_panes(t.app, 4) == ["%7"]                          # it shows in both (K2) (#86)
            assert await chosen_session(t.app, pilot, page, "4") == "%7"
            assert page.texts()[-1] == "#4: coordination session in %7"
            assert not t.app.messages.get("start")
            assert t.focused == []


async def test_the_terminals_start_is_as_before():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        await browser_on(t.app, pilot)
        await highlight(t.app, pilot, "4")
        await pilot.press("s")
        await settle(t.app, pilot)
        await pilot.press("enter")
        await settle(t.app, pilot)
        assert t.starts == [((REPO, 4, ""), {})]
        assert cursor_key(t.app) == "4" and t.app.messages["start"] == "#4: coordination session in %7"


async def test_S_from_the_page_opens_the_box_for_another_session():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("new-session", row="3")                          # #3 has a run: S opens anyway (#86)
            await settle(t.app, pilot)
            assert page.of("instructions") == [{"type": "instructions", "repository": REPO, "number": 3, "opener": "S"}]
            await page.act("instructions", repository=REPO, number=3, opener="S", text="")
            await settle(t.app, pilot)
            assert t.starts == [((REPO, 3, ""), {"detached": True})]


async def test_start_on_a_session_row_opens_its_view_and_moves_no_focus():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("start", row="4")             # one session, no run: s goes to it (#86)
            await settle(t.app, pilot)
            await pilot.pause(0.3)
            assert page.of("frame") and page.of("frame")[-1]["pane"] == "%7"
            assert not page.of("instructions")
            assert t.focused == [] and t.starts == []


async def test_a_terminal_message_stays_through_a_browser_action_and_a_page_message_through_a_terminal_one():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await highlight(t.app, pilot, "3")
            await pilot.press("s")
            await settle(t.app, pilot)
            terminal = t.app.messages["start"]
            assert terminal == "#3 already has a run: autoflow-sandbox-3-sample"
            await page.act("start", row="4")
            await page.act("start", row="3")
            await settle(t.app, pilot)
            assert t.app.messages["start"] == terminal
            assert page.texts()[-1] == terminal
            said = list(page.texts())
            await highlight(t.app, pilot, "4")
            await pilot.press("s")
            await settle(t.app, pilot)
            await pilot.press("escape")
            await settle(t.app, pilot)
            assert page.texts() == said


# One opening at a time, from either side (#86)

@pytest.mark.parametrize("first", ["terminal", "page"])
async def test_a_second_open_from_the_other_side_is_refused_while_one_is_in_progress(first):
    gate = asyncio.Event()
    t = Harness(gate=gate)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await highlight(t.app, pilot, "4")
            if first == "terminal":
                await pilot.press("s")
                await settle(t.app, pilot)
                await pilot.press("enter")
                await pilot.pause(0.3)
                await page.act("start", row="4")
                await pilot.pause(0.3)
                assert page.texts()[-1] == "#4: opening the coordination session" and not page.of("instructions")
            else:
                await page.act("start", row="4")
                await settle(t.app, pilot)
                await page.act("instructions", repository=REPO, number=4, opener="s", text="")
                await pilot.pause(0.3)
                await pilot.press("s")
                await pilot.pause(0.3)
                assert t.app.messages["start"] == "#4: opening the coordination session"
                assert t.app.instructing is None
            assert len(t.starts) == 1
            gate.set()
            await settle(t.app, pilot)
            assert session_panes(t.app, 4) == ["%7"]
            assert await chosen_session(t.app, pilot, page, "4") == "%7"


# The browser's workers run beside the terminal's, cancelling none (#86)

class SlowWindows(PaneWindows):
    def __init__(self):
        super().__init__()
        self.find_delay = 0.5


def recording_workers(app):
    started = []
    run_worker = app.run_worker

    def run(*args, **kwargs):
        worker = run_worker(*args, **kwargs)
        started.append(worker)
        return worker
    app.run_worker = run
    return started


@pytest.mark.parametrize("order", ["terminal first", "page first", "two pages"])
async def test_the_browsers_start_runs_in_its_own_group_beside_the_terminals(order):
    t = Harness(windows=SlowWindows(), issues=[Issue(4, "four"), Issue(2, "two"), Issue(3, "three")], runs=[])
    async with t.app.run_test(size=WIDE) as pilot:
        await pilot.pause(1.2)
        base = await browser_on(t.app, pilot)
        await highlight(t.app, pilot, "4")
        workers = recording_workers(t.app)
        async with Page(base) as one, Page(base) as two:
            if order == "terminal first":
                await pilot.press("s")
                await pilot.pause(0.1)
                await one.act("start", row="2")
            elif order == "page first":
                await one.act("start", row="2")
                await pilot.pause(0.1)
                await pilot.press("s")
            else:
                await one.act("start", row="2")
                await pilot.pause(0.1)
                await two.act("start", row="3")
            await pilot.pause(1.5)
            await settle(t.app, pilot)
            if order != "two pages":
                assert t.app.instructing == (REPO, 4)                     # the terminal's box, where it pressed s (#86)
            assert [m["number"] for m in one.of("instructions")] == [2]
            if order == "two pages":
                assert [m["number"] for m in two.of("instructions")] == [3]
            assert not [w for w in workers if w.state == WorkerState.CANCELLED]
            groups = [w.group for w in workers]
            assert groups.count("start-focus") == (0 if order == "two pages" else 1)     # the terminal's s only (#86)
            assert groups.count("browser") >= (2 if order == "two pages" else 1)


# Going to a session from the page (#86)

async def test_going_to_a_started_session_whose_pane_is_gone_drops_it_as_the_terminal_does():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("start", row="4")
            await settle(t.app, pilot)
            await page.act("instructions", repository=REPO, number=4, opener="s", text="")
            await settle(t.app, pilot)
            assert t.app.started[(REPO, 4)] == ["%7"] and session_panes(t.app, 4) == ["%7"]
            t.windows.gone.add("%7")
            await page.act("go", row="4")
            await settle(t.app, pilot)
            assert page.texts()[-1] == "#4: %7: can't find pane: %7; press s to open it again"
            assert "%7" not in t.app.started.get((REPO, 4), [])
            assert session_panes(t.app, 4) == [] and await chosen_session(t.app, pilot, page, "4") is None
            assert t.focused == [] and not page.of("frame")


async def test_going_to_a_found_session_or_a_work_session_whose_pane_is_gone():
    t = Harness(windows=PaneWindows({4: [Window("@9", "autoflow-sandbox#4", ["%9"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        t.windows.gone |= {"%9", "%1"}
        async with Page(base) as page:
            await page.act("go", row="4")
            await settle(t.app, pilot)
            assert page.texts()[-1] == "#4: %9: can't find pane: %9"
            assert session_panes(t.app, 4) == ["%9"]                       # nothing else changes (#86)
            await page.act("go", run=RUN_3, session="S1")
            await settle(t.app, pilot)
            assert page.texts()[-1] == "S1: can't find pane: %1"
            assert t.focused == [] and not page.of("frame")
            assert not t.app.messages.get("start") and not t.app.messages.get("focus")


async def test_a_view_whose_pane_goes_stops_and_says_so():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("start", row="4")
            await settle(t.app, pilot)
            await page.act("instructions", repository=REPO, number=4, opener="s", text="")
            await settle(t.app, pilot)
            await page.act("go", row="4")
            await pilot.pause(0.3)
            assert page.of("frame")
            t.windows.gone.add("%7")
            await pilot.pause(1.2)
            await settle(t.app, pilot)
            assert page.texts()[-1] == "#4: %7: can't find pane: %7; press s to open it again"
            assert session_panes(t.app, 4) == []
            reads = len(t.windows.frame_reads())
            await pilot.pause(1.2)
            assert len(t.windows.frame_reads()) == reads


# The session view (#86)

def frame_of(message):
    return {k: message[k] for k in ("pane", "rows", "cursor", "cursor_visible", "width", "height")}


async def test_the_view_starts_from_the_current_screen_and_sends_each_change_once():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("go", row="4")
            await pilot.pause(0.3)
            assert [frame_of(m) for m in page.of("frame")] == [{
                "pane": "%7", "rows": ["\x1b[32mGREEN\x1b[39m plain", "\x1b[1m\x1b[31mBOLD RED\x1b[0m", ">",
                                       "", "", "", "", ""],
                "cursor": [2, 2], "cursor_visible": True, "width": 40, "height": 8}]
            await pilot.pause(1.2)
            assert len(page.of("frame")) == 1                             # the same screen is not sent again (#86)
            assert len(t.windows.frame_reads("%7")) >= 3                  # read every half second (#86)
            t.windows.frames["%7"] = ("3 0 0 30 4 0 0\n", "one\ntwo\nthree\nfour\n")
            await pilot.pause(0.8)
            assert frame_of(page.of("frame")[-1]) == {"pane": "%7", "rows": ["one", "two", "three", "four"],
                                                      "cursor": [3, 0], "cursor_visible": False,
                                                      "width": 30, "height": 4}
            assert len(page.of("frame")) == 2
            assert t.windows.writes == [] and t.focused == []


async def test_one_view_per_page_and_closing_it_stops_its_reads():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page, Page(base) as other:
            await page.act("go", row="4")
            await other.act("go", row="4")
            await pilot.pause(0.3)
            await page.act("go", run=RUN_3, session="S2")                 # another session: %2 (#86)
            await pilot.pause(0.3)
            before = len(t.windows.frame_reads("%7"))
            await pilot.pause(1.1)
            assert len(t.windows.frame_reads("%7")) - before <= 3        # only the other page's view reads %7 (#86)
            assert t.windows.frame_reads("%2")
            await page.act("close-view")
            await other.act("close-view")
            await pilot.pause(0.3)
            reads = len(t.windows.frame_reads())
            await pilot.pause(1.2)
            assert len(t.windows.frame_reads()) == reads
            await page.act("go", row="4")
            await pilot.pause(0.3)
        await pilot.pause(0.3)                                            # the page closed (#86)
        reads = len(t.windows.frame_reads())
        await pilot.pause(1.2)
        assert len(t.windows.frame_reads()) == reads


# The state the terminal shows, at the same read (#86)

async def test_the_page_gets_the_terminals_rows_at_each_read():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}), window_check_sec=0.1)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await settle(t.app, pilot)
            assert page.rows() == issue_rows(t.app)
            # A read of the runs: issue 3's run is gone. (#86)
            t.runs = []
            t.app.refresh_runs()
            await settle(t.app, pilot)
            assert page.rows() == issue_rows(t.app) and "●" not in [c[0] for _, c in page.rows()]
            # A read of the issues: issue 7 is listed. (#86)
            t.issues = t.issues + [Issue(7, "seven <b>bold</b>")]
            t.app.action_refresh_issues()
            await settle(t.app, pilot)
            assert page.rows() == issue_rows(t.app) and "7" in [key for key, _ in page.rows()]
            # A window check: issue 4's window shows a prompt. (#86)
            t.windows.window_screens["@4"] = CHOICE
            await pilot.pause(0.4)
            await settle(t.app, pilot)
            assert dict(issue_rows(t.app))["4"][0] == "?"
            assert page.rows() == issue_rows(t.app)


def info_shown(app):
    return str(app.query_one("#info", Static).render())


async def test_the_page_shows_what_the_terminal_shows_for_the_row_it_chose():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            for row in ("3", "4"):
                await page.act("select", row=row)
                await settle(t.app, pilot)
                await highlight(t.app, pilot, row)
                chosen = page.state()["chosen"]
                assert chosen["key"] == row
                assert "\n".join(label.ljust(10) + value for label, value in chosen["info"]) == info_shown(t.app)
                assert chosen["steps"] == table_rows(t.app, "run")
                assert chosen["sessions"] == table_rows(t.app, "sessions")
            await highlight(t.app, pilot, "4")
            await page.act("select", row="3")                            # the page's choice moves no cursor (#86)
            await settle(t.app, pilot)
            assert cursor_key(t.app) == "4" and page.state()["chosen"]["key"] == "3"
            # A step that changes shows in the page's choice at the same read. (#86)
            run = RECORDED_RUNS[0]
            changed = type(run)(run.name, [type(s)(s.label, "running" if n == 2 else s.state, s.verdict, s.session)
                                           for n, s in enumerate(run.steps)], run.sessions, issue=run.issue)
            t.runs = [changed]
            t.app.refresh_runs()
            await settle(t.app, pilot)
            assert page.state()["chosen"]["steps"][2][3] == "running"


# Session records (#86)

def clone_folder(home, clone):
    return home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(clone.resolve()))


def coordination_harness(tmp_path, monkeypatch, sid=SID):
    home, clone = tmp_path / "home", tmp_path / "coordinator"
    clone.mkdir()
    monkeypatch.setenv("HOME", str(home))
    windows = PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]})
    if sid is not None:
        windows.ids["%7"] = sid
    windows.texts["%7"] = ["the screen of %7\n"]
    return Harness(windows=windows, coordinator=str(clone)), clone_folder(home, clone)


async def test_a_restarted_ide_finds_the_transcript_by_the_id_on_the_pane(tmp_path, monkeypatch):
    # The IDE never started this session: the id is read from the pane (#86).
    t, folder = coordination_harness(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    main = folder / f"{SID}.jsonl"
    main.write_bytes((FIXTURES / "transcripts" / "00000000-0000-4000-8000-000000000001.jsonl").read_bytes())
    os.utime(main, (1_700_000_000, 1_700_000_000))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            records = await page.get("records", row="4")
            assert records["screen"] == "the screen of %7\n"
            assert records["about"] == [f"session {SID}, as the IDE started it in this pane",
                                        "after /clear, or claude started again in this pane, the session writes "
                                        "another transcript, which the IDE does not know"]
            [shown] = records["files"]
            assert shown["data"] == main.read_text() and shown["notes"] == []
            assert shown["modified"] == pytest.approx(1_700_000_000)
            assert ("session_id", "%7") in t.windows.reads


async def test_no_transcript_yet(tmp_path, monkeypatch):
    t, folder = coordination_harness(tmp_path, monkeypatch)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            records = await page.get("records", row="4")
            assert f"no transcript of {SID} yet" in records["about"] and records["files"] == []


async def test_a_pane_without_the_id_was_not_opened_by_the_ide(tmp_path, monkeypatch):
    t, folder = coordination_harness(tmp_path, monkeypatch, sid=None)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            records = await page.get("records", row="4")
            assert records["about"] == ["transcript not known: this session was not opened by the IDE"]
            assert records["files"] == [] and records["screen"] == "the screen of %7\n"


@pytest.mark.parametrize("value", ["../../x", "ABC", "00000000-0000-4000-8000-0000000000AA"])
async def test_a_pane_id_that_is_not_a_uuid_is_said_as_text(tmp_path, monkeypatch, value):
    t, folder = coordination_harness(tmp_path, monkeypatch, sid=value)
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            records = await page.get("records", row="4")
            assert records["about"] == [f"transcript not known: the session id {value} is not a UUID"]
            assert records["files"] == []


async def test_the_records_of_a_gone_pane_say_it_is_gone_and_drop_it(tmp_path, monkeypatch):
    t, folder = coordination_harness(tmp_path, monkeypatch, sid=None)
    t.windows.open = {}
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("start", row="4")
            await settle(t.app, pilot)
            await page.act("instructions", repository=REPO, number=4, opener="s", text="")
            await settle(t.app, pilot)
            t.windows.gone.add("%7")
            records = await page.get("records", row="4")
            await settle(t.app, pilot)
            assert records == {"error": "#4: %7: can't find pane: %7; press s to open it again"}
            assert session_panes(t.app, 4) == []


# Input to a session (#86)

def input_harness():
    windows = PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]})
    return Harness(windows=windows)


@pytest.mark.parametrize("target, mode, screen, text, reason", [
    ("coordination", True, TURN_END, "hello", "%7 is in copy mode: leave it (q) and retry"),
    ("coordination", False, drafted(TURN_END, "my draft"), "hello", "the input area is not empty"),
    ("coordination", False, CHOICE, "hello", "the input area is not empty"),                # a dialog (#86)
    ("coordination", False, TURN_END, "x" * 701, "701 bytes is over 700: put it in a file"),
    ("work", False, TURN_END, "hello", None),                                               # no message to a work session (#86)
])
async def test_a_message_the_pane_would_not_take_enters_nothing(target, mode, screen, text, reason):
    t = input_harness()
    pane = "%7" if target == "coordination" else "%1"
    t.windows.modes[pane], t.windows.texts[pane] = mode, [screen]
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            where = dict(row="4") if target == "coordination" else dict(run=RUN_3, session="S1")
            await page.act("message", text=text, **where)
            await settle(t.app, pilot)
            assert t.windows.writes == []
            assert page.texts(), "the page is told why"
            if reason:
                assert reason in page.texts()[-1]


async def test_a_message_to_an_empty_input_area_is_pasted_and_submitted():
    t = input_harness()
    t.windows.texts["%7"] = [TURN_END, drafted(TURN_END, "look at the second step")]
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("message", row="4", text="look at the second step")
            await settle(t.app, pilot)
            assert t.windows.writes == [("paste", "%7", "look at the second step"), ("send-keys", "%7", "Enter")]
            assert not t.app.messages.get("start") and not t.app.messages.get("focus")


async def test_prompt_keys_reach_only_a_screen_that_shows_a_prompt():
    t = input_harness()
    t.windows.texts["%1"] = [TURN_END]
    t.windows.texts["%7"] = [CHOICE]
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Page(base) as page:
            await page.act("keys", run=RUN_3, session="S1", key="1")
            await settle(t.app, pilot)
            assert t.windows.writes == [] and "no prompt on S1's screen" in page.texts()[-1]
            t.windows.texts["%1"] = [CHOICE]
            t.windows.text_reads.clear()
            await page.act("keys", run=RUN_3, session="S1", key="1")
            await settle(t.app, pilot)
            assert t.windows.writes == [("send-keys", "%1", "1")]
            assert any("the coordination session is not told of these keys" in text for text in page.texts())
            await page.act("keys", row="4", key="Down")
            await settle(t.app, pilot)
            assert t.windows.writes[-1] == ("send-keys", "%7", "Down")
            t.windows.modes["%7"] = True
            await page.act("keys", row="4", key="Enter")
            await page.act("keys", row="4", key="C-c")
            t.windows.modes["%7"] = False
            await settle(t.app, pilot)
            assert t.windows.writes[-1] == ("send-keys", "%7", "Down")
