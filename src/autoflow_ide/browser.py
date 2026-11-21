"""Browser control (#86): a web server on 127.0.0.1, in the IDE's own process and on its event loop, that serves one
page showing the IDE's screen and acts through the same code as the keys.

Only the page of the IDE that started it is served or obeyed: every path starts with a token made at each start and
shown only in the IDE's status line; the Host must be 127.0.0.1:<port> or localhost:<port>; the WebSocket and every
action carry the server's own Origin; actions are POSTs of JSON. A program of the same OS user can drive tmux itself,
so these checks keep out other pages and other users, not it.

The page's URLs, under http://127.0.0.1:<port>/<token>/:
- `` the page, `app.js`, `app.css`, `xterm/xterm.js`, `xterm/xterm.css`;
- `state` the snapshot; `records?row=` or `records?run=&session=` a session's screen text and transcripts;
- `ws` the page's WebSocket, which only receives: hello, state, message, instructions, view, frame, message-done,
  stopped;
- `action` POST {"page", "action", ...}: select, start, new-session, instructions, go, close-view, message, keys.
"""
import asyncio
import hmac
import json
import logging
import secrets
import sys
from pathlib import Path
from typing import Dict, Optional

from aiohttp import WSMsgType, web

from autoflow_ide.app import IdeApp, IssueKey, Reporter, issue_key
from autoflow_ide.inputs import InputRefused, NoPrompt, answer_prompt, enter_message
from autoflow_ide.start import StartError, printable, run as run_command
from autoflow_ide.transcripts import (TranscriptUnknown, coordination_folder, read_tail, runs_folder,
                                      transcript_files, work_transcripts)
from autoflow_ide.windows import WindowError

WEB = Path(__file__).parent / "web"
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
          "img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
FILES = {"": ("index.html", "text/html"), "app.js": ("app.js", "application/javascript"),
         "app.css": ("app.css", "text/css"), "xterm/xterm.js": ("xterm/xterm.js", "application/javascript"),
         "xterm/xterm.css": ("xterm/xterm.css", "text/css")}
# How often an open view reads its pane. (#86)
VIEW_POLL_SEC = 0.5
# How long a stop waits for an input under way to finish, so no pasted text is left unsubmitted (#86).
INPUT_STOP_WAIT_SEC = 5.0
CLEAR_NOTE = ("after /clear, or claude started again in this pane, the session writes another transcript, "
              "which the IDE does not know")


class Page:
    """One connected page: its WebSocket, the row it chose, and its view."""

    def __init__(self, server: "BrowserServer", ws: web.WebSocketResponse):
        self.server, self.ws = server, ws
        self.id = secrets.token_hex(8)
        self.chosen: Optional[str] = None
        self.view = 0
        self.closes = 0
        self.queue: "asyncio.Queue[Optional[dict]]" = asyncio.Queue()
        self.closed = False
        self.sender = asyncio.ensure_future(self.send_all())

    def put(self, message: dict) -> None:
        if not self.closed:
            self.queue.put_nowait(message)

    async def send_all(self) -> None:
        while True:
            message = await self.queue.get()
            if message is None:
                return
            try:
                await self.ws.send_str(json.dumps(message))
            except (ConnectionError, RuntimeError):
                return

    def say(self, text: str) -> None:
        self.put({"type": "message", "text": text})

    @property
    def view_group(self) -> str:
        return f"browser-view-{self.id}"

    def open_view(self, pane: str, failed) -> None:
        """Show `pane` in this page's view, replacing the one it had; `failed(error)` when the pane cannot be read.
        Each view has its own number, sent before its frames and with each, so the page draws no frame of a view it
        closed (#86)."""
        self.view += 1
        self.put({"type": "view", "view": self.view, "pane": pane})
        self.server.app.run_worker(self.poll(self.view, pane, failed), group=self.view_group, exclusive=True)

    def close_view(self) -> None:
        """Stop the view; a go-to or start asked before this opens no view after it (#86)."""
        self.closes += 1
        self.server.app.workers.cancel_group(self.server.app, self.view_group)

    def asking(self) -> "PageReporter":
        """The reporter of one action of this page: it opens a view only while the page has not closed its view
        since the action was asked (#86)."""
        return PageReporter(self)

    async def poll(self, view: int, pane: str, failed) -> None:
        """Read the pane every VIEW_POLL_SEC and send each screen that differs from the last; no tmux client is
        attached, so no window's size changes."""
        last = None
        while not self.closed:
            try:
                frame = await self.server.app.windows.frame(pane)
            except WindowError as e:
                failed(str(e))
                return
            message = {"type": "frame", "view": view, "pane": pane, "rows": frame.rows, "cursor": list(frame.cursor),
                       "cursor_visible": frame.cursor_visible, "width": frame.width, "height": frame.height}
            if message != last:
                self.put(message)
                last = message
            await asyncio.sleep(VIEW_POLL_SEC)

    async def close(self) -> None:
        if self.closed:
            return
        self.close_view()
        self.closed = True
        self.queue.put_nowait(None)
        try:
            await asyncio.wait_for(self.sender, 2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.sender.cancel()


class PageReporter(Reporter):
    """The start and go-to checks' answers to one page; no terminal slot is written."""
    detached = True

    def __init__(self, page: Page):
        self.page = page
        self.closes = page.closes

    @property
    def closed_since(self) -> bool:
        """Whether the page closed its view after this action was asked (#86)."""
        return self.page.closes != self.closes

    def say(self, slot: str, text: str) -> None:
        self.page.say(text)

    def ask(self, key: IssueKey, opener: str) -> None:
        self.page.put({"type": "instructions", "repository": key[0], "number": key[1], "opener": opener})

    async def go(self, key: IssueKey, pane: str) -> None:
        if self.closed_since:
            return
        app = self.page.server.app
        self.page.open_view(pane, lambda error: app.pane_gone(key, pane, error, self))


class BrowserServer:
    def __init__(self, app: IdeApp, port: int = 0):
        self.app, self.port = app, port
        self.token = secrets.token_urlsafe(32)
        self.pages: Dict[str, Page] = {}
        self.runner: Optional[web.AppRunner] = None
        self.push_pending = False
        self.input_locks: Dict[str, asyncio.Lock] = {}
        self.stopping = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    async def start(self) -> None:
        # aiohttp's records go nowhere: not to the terminal the screen is drawn in (the last-resort handler). (#86)
        logger = logging.getLogger("aiohttp")
        if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
            logger.addHandler(logging.NullHandler())
        application = web.Application(middlewares=[self.guard])
        base = f"/{self.token}/"
        for path in FILES:
            application.router.add_get(base + path, self.file)
        application.router.add_get(base + "state", self.state)
        application.router.add_get(base + "records", self.records)
        application.router.add_get(base + "ws", self.websocket)
        application.router.add_post(base + "action", self.action)
        application.on_response_prepare.append(self.headers)
        # No access log: its request line holds the token. (#86)
        self.runner = web.AppRunner(application, access_log=None, shutdown_timeout=1.0)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        try:
            await site.start()
        except OSError:
            await self.runner.cleanup()
            raise
        self.port = self.runner.addresses[0][1]

    async def stop(self) -> None:
        """Tell each page that the server stopped and close it, stop the browser's work, then the server. An input
        already under way in a pane finishes first, so no text is left pasted and not submitted; one not begun is
        refused (#86)."""
        self.stopping = True
        for page in list(self.pages.values()):
            page.put({"type": "stopped"})
            await page.close()
            await page.ws.close()
        self.pages.clear()
        await self.inputs_done()
        self.app.workers.cancel_group(self.app, "browser")
        if self.runner is not None:
            await self.runner.cleanup()

    async def inputs_done(self) -> None:
        """Wait, up to INPUT_STOP_WAIT_SEC each, until no pane's input lock is held (#86)."""
        for lock in list(self.input_locks.values()):
            if not lock.locked():
                continue
            try:
                await asyncio.wait_for(lock.acquire(), INPUT_STOP_WAIT_SEC)
            except asyncio.TimeoutError:
                continue
            lock.release()

    # Checks (#86)

    @web.middleware
    async def guard(self, request: web.Request, handler):
        host = request.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return web.Response(status=403)
        first = request.path[1:].split("/", 1)[0]
        if not hmac.compare_digest(first.encode(), self.token.encode()) or not request.path.startswith(f"/{first}/"):
            return web.Response(status=404)
        changing = request.method != "GET" or request.path.endswith("/ws")
        if changing and request.headers.get("Origin") != f"http://{host}":
            return web.Response(status=403)
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception as e:  # answered to the page, not logged with the request's path (#86)
            return web.json_response({"error": f"{type(e).__name__}: {e}"})

    async def headers(self, request: web.Request, response: web.StreamResponse) -> None:
        response.headers["Content-Security-Policy"] = POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"

    # The page and its data (#86)

    async def file(self, request: web.Request) -> web.Response:
        name, kind = FILES[request.path[len(self.token) + 2:]]
        return web.Response(body=(WEB / name).read_bytes(), content_type=kind, charset="utf-8")

    async def state(self, request: web.Request) -> web.Response:
        return web.json_response(self.app.snapshot(None))

    def changed(self) -> None:
        if not self.push_pending and self.pages:
            self.push_pending = True
            asyncio.get_running_loop().call_soon(self.push)

    def push(self) -> None:
        self.push_pending = False
        for page in list(self.pages.values()):
            self.send_state(page)

    def send_state(self, page: Page) -> None:
        page.put({"type": "state", "state": self.app.snapshot(page.chosen)})

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        page = Page(self, ws)
        self.pages[page.id] = page
        page.put({"type": "hello", "page": page.id})
        self.send_state(page)
        try:
            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break  # the page sends nothing: the view is read-only (#86)
        finally:
            self.pages.pop(page.id, None)
            await page.close()
        return ws

    # Actions (#86)

    async def action(self, request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.Response(status=415)
        try:
            body = await request.json()
        except ValueError:
            return web.Response(status=400)
        page = self.pages.get(body.get("page")) if isinstance(body, dict) else None
        if page is None:
            return web.Response(status=400)
        name, reporter, app = body.get("action"), page.asking(), self.app
        if name == "select":
            page.chosen = body.get("row")
            self.send_state(page)
        elif name == "start":
            app.run_worker(app.browser_start(str(body.get("row")), reporter), group="browser")
        elif name == "new-session":
            app.browser_new_session(str(body.get("row")), reporter)
        elif name == "instructions":
            try:
                key = (str(body["repository"]), int(body["number"]))
            except (KeyError, TypeError, ValueError):
                return web.Response(status=400)
            opener = body.get("opener") if body.get("opener") in ("s", "S") else "s"
            app.decide_open(key, opener, printable(str(body.get("text") or "")).strip(), reporter)
        elif name == "go":
            app.run_worker(self.go(page, body, reporter), group="browser")
        elif name == "close-view":
            page.close_view()
        elif name == "message":
            app.run_worker(self.message_done(page, body), group="browser")
        elif name == "keys":
            app.run_worker(self.keys(page, body, str(body.get("key") or "")), group="browser")
        else:
            return web.Response(status=400)
        return web.json_response({"ok": True})

    async def coordination(self, row: str, keep: bool = True):
        """The issue and coordination pane `row` names, or why none, with the issue's windows read again first, as
        `s` reads them, so a session the periodic window check has not found yet is found. With `keep` false (a
        GET), the windows read answer only this call: the IDE's state is left as it was. A started pane that this
        read drops, its window gone, is said to be gone, as a go-to on it says (#86)."""
        target = self.app.row_targets.get(row)
        if target is None:
            return self.app.coordination_target(row)
        key = issue_key(target[0])
        if keep:
            _, pane, _ = self.app.coordination_target(row)
            started = list(self.app.started.get(key, []))
            await self.app.read_windows_of(key)
            if pane in started and pane not in self.app.started.get(key, []):
                try:
                    await self.app.windows.pane_screen(pane)
                except WindowError as e:
                    return key, None, f"{self.app.label(key)}: {pane}: {e}; press s to open it again"
            return self.app.coordination_target(row)
        _, windows = await self.app.find_windows_of(key)
        return self.app.coordination_target(row, windows)

    def input_lock(self, pane: str) -> asyncio.Lock:
        """The lock that lets one input action at a time check and enter into `pane` (#86)."""
        return self.input_locks.setdefault(pane, asyncio.Lock())

    def input_allowed(self) -> None:
        """Refuse an input that reaches its pane's lock once browser control is stopping (#86)."""
        if self.stopping:
            raise InputRefused("browser control stopped: nothing entered")

    def work_session(self, body: dict):
        """The run and work session `body` names, and its pane, or why there is none."""
        run = next((r for r in self.app.runs if r.name == body.get("run")), None)
        session = next((s for s in run.sessions if s.name == body.get("session")), None) if run else None
        if session is None:
            return run, None, None, f"{body.get('session')}: not a session of {body.get('run')}"
        if not session.surface:
            return run, session, None, f"{session.name}: no pane"
        return run, session, session.surface, None

    async def go(self, page: Page, body: dict, reporter: PageReporter) -> None:
        if "run" in body:
            _, session, pane, why = self.work_session(body)
            if why:
                page.say(why)
                return
            if reporter.closed_since:
                return
            page.open_view(pane, lambda error: page.say(f"{session.name}: {error}"))
            return
        key, pane, why = await self.coordination(str(body.get("row")))
        if why:
            page.say(why)
        else:
            await reporter.go(key, pane)

    async def message_done(self, page: Page, body: dict) -> None:
        """A message action, then the page is told it ended, so its Send is offered again (#86)."""
        try:
            await self.message(page, body, str(body.get("text") or ""))
        finally:
            page.put({"type": "message-done"})

    async def message(self, page: Page, body: dict, text: str) -> None:
        if "run" in body:
            page.say(f"{body.get('session')}: no message to a work session, only the keys that answer a prompt on "
                     "its screen; the coordination session is not told of them")
            return
        key, pane, why = await self.coordination(str(body.get("row")))
        if why:
            page.say(why)
            return
        label = self.app.label(key)
        # Each tab, line break or other space as a plain space and every other control or non-printable character
        # dropped, as for the run's instructions: a CR, Ctrl-C or escape sequence would act as a key press (#86).
        text = printable(text).strip()
        if not text:
            page.say(f"{label}: nothing to enter")
            return
        try:
            async with self.input_lock(pane):
                self.input_allowed()
                await enter_message(self.app.windows, pane, text)
        except (InputRefused, WindowError) as e:
            page.say(f"{label}: {e}")
            return
        page.say(f"{label}: message entered in {pane}")

    async def keys(self, page: Page, body: dict, key: str) -> None:
        if "run" in body:
            _, session, pane, why = self.work_session(body)
            name, note = (session.name if session else None), "; the coordination session is not told of these keys"
        else:
            issue, pane, why = await self.coordination(str(body.get("row")))
            name, note = (self.app.label(issue) if issue else None), ""
        if why:
            page.say(why)
            return
        try:
            async with self.input_lock(pane):
                self.input_allowed()
                await answer_prompt(self.app.windows, pane, key)
        except NoPrompt:
            page.say(f"no prompt on {name}'s screen")
            return
        except (InputRefused, WindowError) as e:
            page.say(f"{name}: {e}")
            return
        page.say(f"{name}: {key} sent{note}")

    # Records (#86)

    async def records(self, request: web.Request) -> web.Response:
        if "run" in request.query:
            return web.json_response(await self.work_records(dict(request.query)))
        return web.json_response(await self.coordination_records(request.query.get("row", "")))

    async def coordination_records(self, row: str) -> dict:
        key, pane, why = await self.coordination(row, keep=False)
        if why:
            return {"error": why}
        try:
            screen = await self.app.windows.pane_screen(pane)
        except WindowError as e:
            # A GET leaves the session state as it was (the started panes, the windows found, the rows, the marks):
            # the pane is dropped from its issue by an action on it (a go-to, a message, keys) or the next window
            # check, not by this read (#86).
            return {"error": f"{self.app.label(key)}: {pane}: {e}"}
        session_id = await self.app.windows.session_id(pane)
        if not session_id:
            return {"screen": screen, "about": ["transcript not known: this session was not opened by the IDE"],
                    "files": []}
        try:
            files = transcript_files(coordination_folder(self.app.coordinator, Path.home()), session_id)
        except TranscriptUnknown as e:
            return {"screen": screen, "about": [str(e)], "files": []}
        about = [f"session {session_id}, as the IDE started it in this pane", CLEAR_NOTE]
        if not files[0].is_file():
            return {"screen": screen, "about": about + [f"no transcript of {session_id} yet"], "files": []}
        return {"screen": screen, "about": about, "files": [shown(path, files[0].parent) for path in files]}

    async def work_records(self, query: dict) -> dict:
        run, session, pane, why = self.work_session(query)
        if why:
            return {"error": why}
        try:
            screen = await self.app.windows.pane_screen(pane)
        except WindowError as e:
            return {"error": f"{session.name}: {e}"}
        try:
            config = await run_command(sys.executable, str(Path(self.app.coordinator).expanduser() / "tools" / "config.py"))
            runs = runs_folder(config)
            if runs is None:
                raise TranscriptUnknown("transcript not known: the coordinator names no runs folder")
            files = work_transcripts(runs, run, session.name, Path.home())
        except (StartError, TranscriptUnknown) as e:
            return {"screen": screen, "about": [str(e)], "files": []}
        about = [f"session {files[0].stem}, as {run.name}'s sessions.tsv names it"]
        if not files[0].is_file():
            return {"screen": screen, "about": about + [f"no transcript of {files[0].stem} yet"], "files": []}
        return {"screen": screen, "about": about, "files": [shown(path, files[0].parent) for path in files]}


def shown(path: Path, folder: Path) -> dict:
    """A transcript file as the page shows it: its whole records from its end, and what was left out."""
    tail = read_tail(path)
    try:
        name = str(path.relative_to(folder))
    except ValueError:
        name = path.name
    return {"name": name, "modified": path.stat().st_mtime, "data": tail.data.decode(errors="replace"),
            "notes": tail.notes}
