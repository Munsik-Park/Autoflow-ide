"""Browser control (#86) in a real browser: the page the server serves, in Playwright's Chromium.

Run like tests/test_app.py, locally and not in CI, with the `browser-test` extra and `python -m playwright install
chromium`. The IDE runs with app.run_test() and the stubs of tests/test_browser.py, on the same event loop as
Playwright's async API, so nothing reaches tmux.

The page, as these tests use it:

- Three columns, as the IDE's screen: `#left` holds `#issues`; `#middle` holds `#info` and `#steps`; `#right`
  holds `#sessions`. An issue's row is `#issues [data-row="<row key>"]`, a step's `#steps [data-step]`, a work
  session's `#sessions [data-session="<name>"]`; each row has one child element per cell, in the terminal's order.
- Clicking an issue's row chooses it, and its coordination session (as in tests/test_browser.py); clicking a work
  session's row chooses that session.
- Buttons: `#start` (s), `#new-session` (S), `#go` (the chosen session's view), `#records-open` (its records),
  `#view-close`. The instructions box is `#instructions`, its field `#instructions-text`; Enter in it submits.
- `#messages` holds what the IDE says to this page, one element each. `#server-state` says `server stopped` once
  the server stopped.
- The view is `#view`, an xterm.js terminal. The records are `#records`: `#records-about`, `#screen-text`, and a
  `.transcript` per file in `#transcripts`, each with its `.notes` and its records.
- The input is `#input`: `#message-text`, `#message-send` and `#message-limit` for a coordination session; key
  buttons `#keys [data-key="<tmux key name>"]`; and `#input-note`.
"""
import asyncio
import os
import re
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

from autoflow_ide.model import Issue, Run, Step
from autoflow_ide.windows import Window
from test_browser import (CHOICE, CSP, REPO, SHAPE, SID, TURN_END, WIDE, Harness, PaneWindows, browser_on,
                          coordination_harness, drafted, issue_rows, session_panes, settle)

FIXTURES = Path(__file__).parent / "fixtures"
PAYLOAD = '<img src=x onerror="window.__pwned=1">'
REQUESTS = {}


def requests_of(page, method=None):
    """The (method, url) of every request the page made."""
    return [(m, u) for m, u in REQUESTS[page] if method is None or m == method]


class Chromium:
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.page = await self.browser.new_page()
        self.requests = []
        self.page.on("request", lambda request: self.requests.append((request.method, request.url)))
        REQUESTS[self.page] = self.requests
        await self.page.add_init_script(
            "window.__violations = []; document.addEventListener('securitypolicyviolation',"
            " e => window.__violations.push(e.violatedDirective + ' ' + e.blockedURI));")
        return self.page

    async def __aexit__(self, *exc):
        await self.browser.close()
        await self.playwright.stop()


async def rows_of(page, selector):
    return await page.eval_on_selector_all(
        selector, "rows => rows.map(r => [r.dataset.row || r.dataset.step || r.dataset.session,"
                  " Array.from(r.children).map(c => c.textContent)])")


async def until(predicate, seconds=5.0):
    """Wait for `predicate` (a coroutine function) to hold, as the page applies a push."""
    end = asyncio.get_running_loop().time() + seconds
    while True:
        if await predicate():
            return
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("not shown in time")
        await asyncio.sleep(0.1)


async def messages(page):
    return await page.eval_on_selector_all("#messages > *", "ms => ms.map(m => m.textContent)")


# Server start and the issue list, in the IDE's layout (#86)

async def test_the_page_at_the_address_shows_the_terminals_rows_in_its_layout():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            response = await page.goto(base)
            assert response.status == 200
            await until(lambda: rows_of(page, "#issues [data-row]"))
            assert [(key, cells) for key, cells in await rows_of(page, "#issues [data-row]")] == [
                (key, cells) for key, cells in issue_rows(t.app)]
            boxes = {name: await page.locator(f"#{name}").bounding_box() for name in ("left", "middle", "right")}
            assert boxes["left"]["x"] + boxes["left"]["width"] <= boxes["middle"]["x"]
            assert boxes["middle"]["x"] + boxes["middle"]["width"] <= boxes["right"]["x"]
            for column, parts in (("left", ["issues"]), ("middle", ["info", "steps"]), ("right", ["sessions"])):
                for part in parts:
                    assert await page.locator(f"#{column} #{part}").count() == 1, part
            await page.click('#issues [data-row="3"]')
            await until(lambda: rows_of(page, "#steps [data-step]"))
            assert [cells for _, cells in await rows_of(page, "#steps [data-step]")] == [
                ["1", "analysis-1", "S1", "pass", "pass"], ["2", "review-1", "S1", "fail", "missed"],
                ["3", "build-1 (rerun)", "S2", "noted", "noted"]]
            assert [key for key, _ in await rows_of(page, "#sessions [data-session]")] == ["S1", "S2"]


# Starting a session (#86)

async def test_starting_a_session_from_the_page():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.click("#start")
            await page.wait_for_selector("#instructions-text", state="visible")
            await page.fill("#instructions-text", "focus on the tests")
            await page.press("#instructions-text", "Enter")
            await until(lambda: page.evaluate("() => document.querySelector('#messages').textContent.includes("
                                              "'#4: coordination session in %7')"))
            await settle(t.app, pilot)
            assert t.starts == [((REPO, 4, "focus on the tests"), {"detached": True})]
            assert session_panes(t.app, 4) == ["%7"]
            assert "%7" in await page.text_content("#info")


async def test_a_refusal_shows_on_the_page():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="3"]')
            await page.click("#start")
            await until(lambda: page.evaluate("() => document.querySelector('#messages').textContent.includes("
                                              "'#3 already has a run: autoflow-sandbox-3-sample')"))
            assert not t.app.messages.get("start")


# Run status updates (#86)

async def test_a_step_that_changes_shows_without_a_reload():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="3"]')
            await until(lambda: rows_of(page, "#steps [data-step]"))
            loads = len([u for _, u in requests_of(page, "GET") if u == base])
            run = t.runs[0]
            t.runs = [Run(run.name, [Step(s.label, "running" if n == 2 else s.state, s.verdict, s.session)
                                     for n, s in enumerate(run.steps)], run.sessions, issue=run.issue)]
            t.app.refresh_runs()
            await settle(t.app, pilot)

            async def shown():
                return (await rows_of(page, "#steps [data-step]"))[2][1][3] == "running"
            await until(shown)
            assert len([u for _, u in requests_of(page, "GET") if u == base]) == loads


# Going to a session (#86)

async def test_the_view_draws_the_panes_screen_at_its_size_and_takes_no_keys():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.click("#go")
            await page.wait_for_selector("#view .xterm-rows")

            async def drawn():
                rows = await page.eval_on_selector_all("#view .xterm-rows > div", "rs => rs.map(r => r.textContent)")
                return len(rows) == 8 and rows[0].rstrip() == "GREEN plain"
            await until(drawn)
            rows = await page.eval_on_selector_all("#view .xterm-rows > div", "rs => rs.map(r => r.textContent)")
            assert [r.rstrip() for r in rows] == ["GREEN plain", "BOLD RED", ">", "", "", "", "", ""]
            cursor_row = await page.evaluate(
                "() => Array.from(document.querySelectorAll('#view .xterm-rows > div'))"
                ".findIndex(r => r.querySelector('.xterm-cursor'))")
            assert cursor_row == 2
            columns = await page.evaluate("""() => {
                const measure = document.querySelector('#view .xterm-char-measure-element');
                const cell = measure.getBoundingClientRect().width / measure.textContent.length;
                return Math.round(document.querySelector('#view .xterm-screen').getBoundingClientRect().width / cell);
            }""")
            assert columns == 40
            posts = len(requests_of(page, "POST"))
            await page.click("#view")
            await page.keyboard.type("abc")
            await page.keyboard.press("Enter")
            await page.keyboard.press("Control+C")
            await asyncio.sleep(0.5)
            await settle(t.app, pilot)
            assert t.windows.writes == [] and t.focused == []
            assert len(requests_of(page, "POST")) == posts


# Server stop (#86)

async def test_the_page_says_the_server_stopped():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await pilot.press("b")
            await settle(t.app, pilot)
            await until(lambda: page.evaluate(
                "() => (document.querySelector('#server-state') || {}).textContent?.includes('server stopped')"))


# Records on the page (#86)

async def test_the_page_shows_a_sessions_screen_text_and_transcript_with_what_was_left_out(tmp_path, monkeypatch):
    t, folder = coordination_harness(tmp_path, monkeypatch)
    folder.mkdir(parents=True)
    record = (FIXTURES / "transcripts" / "00000000-0000-4000-8000-000000000001.jsonl").read_bytes()
    data = record * (600 * 1024 // len(record) + 1)
    main = folder / f"{SID}.jsonl"
    main.write_bytes(data)
    os.utime(main, (1_700_000_000, 1_700_000_000))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.click("#records-open")
            await page.wait_for_selector("#transcripts .transcript")
            assert "the screen of %7" in await page.text_content("#screen-text")
            about = await page.text_content("#records-about")
            assert f"session {SID}, as the IDE started it in this pane" in about
            assert "after /clear, or claude started again in this pane" in about
            text = await page.text_content("#transcripts")
            left_out = re.search(r"(\d+) bytes before this were left out", text)
            assert left_out and 0 < int(left_out[1]) < len(data) - 500 * 1024
            assert "2023-11-1" in await page.text_content("#records")          # the file's last write time (#86)


# Input on the page (#86)

async def test_the_page_states_the_limit_and_a_refusal_and_offers_no_message_to_a_work_session():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    t.windows.texts["%7"] = [drafted(TURN_END, "my draft")]
    t.windows.texts["%1"] = [CHOICE]
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.wait_for_selector("#message-text", state="visible")
            assert "700" in await page.text_content("#message-limit")
            await page.fill("#message-text", "look at the second step")
            await page.click("#message-send")
            await until(lambda: page.evaluate(
                "() => document.querySelector('#messages').textContent.includes('the input area is not empty')"))
            assert t.windows.writes == []
            await page.click('#issues [data-row="3"]')
            await page.click('#sessions [data-session="S1"]')
            await page.wait_for_selector('#keys [data-key="1"]', state="visible")
            assert not await page.is_visible("#message-text")
            assert "the coordination session is not told of these keys" in await page.text_content("#input-note")
            await page.click('#keys [data-key="1"]')
            await until(lambda: asyncio.sleep(0, result=t.windows.writes == [("send-keys", "%1", "1")]))


# Text from others stays text, and the policy holds (#86)

async def test_text_from_others_is_shown_as_text(tmp_path, monkeypatch):
    t, folder = coordination_harness(tmp_path, monkeypatch)
    t.issues = [Issue(4, PAYLOAD), Issue(3, "three")]
    run = t.runs[0]
    t.runs = [Run(run.name, [Step(s.label, s.state, PAYLOAD if n == 0 else s.verdict, s.session)
                             for n, s in enumerate(run.steps)], run.sessions, issue=run.issue)]
    folder.mkdir(parents=True)
    (folder / f"{SID}.jsonl").write_text('{"type":"user","message":{"role":"user","content":%s}}\n'
                                        % __import__("json").dumps(PAYLOAD))
    t.windows.texts["%7"] = [PAYLOAD + "\n"]
    t.windows.frames["%7"] = (SHAPE, PAYLOAD + "\n")
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            assert PAYLOAD in await page.text_content('#issues [data-row="4"]')
            await page.click('#issues [data-row="3"]')
            await until(lambda: rows_of(page, "#steps [data-step]"))
            assert PAYLOAD in await page.text_content("#steps")
            await page.click('#issues [data-row="4"]')
            await page.click("#records-open")
            await page.wait_for_selector("#transcripts .transcript")
            assert PAYLOAD in await page.text_content("#transcripts")
            assert PAYLOAD in await page.text_content("#screen-text")
            await page.click("#go")
            await page.wait_for_selector("#view .xterm-rows")
            await until(lambda: page.evaluate(
                "() => document.querySelector('#view .xterm-rows').textContent.includes('<img src=x')"))
            await asyncio.sleep(0.5)
            for area in ("#left", "#middle", "#right", "#records", "#view", "#messages"):
                assert await page.locator(f"{area} img").count() == 0, area
            assert await page.evaluate("() => window.__pwned") is None


async def test_the_policy_lets_the_view_draw_its_colours_and_blocks_nothing_the_page_needs():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            response = await page.goto(base)
            assert response.headers.get("content-security-policy") == CSP
            assert response.headers.get("x-content-type-options") == "nosniff"
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.click("#go")
            await until(lambda: page.evaluate(
                "() => (document.querySelector('#view .xterm-rows') || {}).textContent?.includes('BOLD RED')"))
            colours = await page.evaluate("""() => {
                const spans = Array.from(document.querySelectorAll('#view .xterm-rows span'));
                const of = text => getComputedStyle(spans.find(s => s.textContent.includes(text))).color;
                return [of('BOLD RED'), of('plain')];
            }""")
            assert colours[0] != colours[1]
            assert await page.evaluate("() => window.__violations") == []


# A page of another origin in the same browser (#86)

async def test_a_page_of_another_localhost_port_can_neither_act_nor_read():
    t = Harness()
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        ws_url = "ws" + base[len("http"):] + "ws"
        other = web.Application()
        html = f"""<!doctype html><script>
            window.got = []; window.done = false;
            fetch({base + 'action'!r}, {{method: 'POST', mode: 'no-cors', headers: {{'Content-Type': 'text/plain'}},
                  body: JSON.stringify({{page: 'x', action: 'instructions', repository: {REPO!r}, number: 4,
                                         opener: 's', text: ''}})}}).catch(() => null);
            const ws = new WebSocket({ws_url!r});
            ws.onmessage = e => window.got.push(e.data);
            ws.onclose = () => window.done = true; ws.onerror = () => window.done = true;
        </script>"""
        other.router.add_get("/", lambda request: web.Response(text=html, content_type="text/html"))
        runner = web.AppRunner(other)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            async with Chromium() as page:
                await page.goto(f"http://127.0.0.1:{port}/")
                await until(lambda: page.evaluate("() => window.done"))
                await asyncio.sleep(0.5)
                assert await page.evaluate("() => window.got") == []
            await settle(t.app, pilot)
            assert t.starts == []
        finally:
            await runner.cleanup()


# One message at a time, and no frame of a closed view (#86)

class GatedPaste(PaneWindows):
    """PaneWindows whose paste waits for `gate`, so a message stays in flight until the test lets it go (#86)."""

    def __init__(self, windows):
        super().__init__(windows)
        self.gate, self.pasting = asyncio.Event(), asyncio.Event()

    async def paste(self, pane, text):
        self.pasting.set()
        await self.gate.wait()
        await super().paste(pane, text)


async def test_send_is_not_sent_again_while_a_message_is_in_flight():
    t = Harness(windows=GatedPaste({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    t.windows.texts["%7"] = [TURN_END, drafted(TURN_END, "hello")]
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            await page.click('#issues [data-row="4"]')
            await page.fill("#message-text", "hello")
            await until(lambda: asyncio.sleep(0, result=bool(requests_of(page, "POST"))))
            posts = len(requests_of(page, "POST"))
            await page.evaluate("() => { const b = document.querySelector('#message-send'); b.click(); b.click(); }")
            await asyncio.wait_for(t.windows.pasting.wait(), 5)
            assert await page.is_disabled("#message-send")
            await page.evaluate("() => document.querySelector('#message-send').click()")
            await asyncio.sleep(0.3)
            assert len(requests_of(page, "POST")) == posts + 1
            t.windows.gate.set()
            await until(lambda: page.evaluate(
                "() => document.querySelector('#messages').textContent.includes('#4: message entered in %7')"))
            await until(lambda: page.is_enabled("#message-send"))
            await settle(t.app, pilot)
            assert t.windows.writes == [("paste", "%7", "hello"), ("send-keys", "%7", "Enter")]


async def test_a_frame_delivered_after_close_draws_nothing():
    t = Harness(windows=PaneWindows({4: [Window("@4", "autoflow-sandbox#4", ["%7"])]}))
    async with t.app.run_test(size=WIDE) as pilot:
        await settle(t.app, pilot)
        base = await browser_on(t.app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, "#issues [data-row]"))
            served = next(iter(t.app.browser.pages.values()))
            sent, put = [], served.put
            served.put = lambda message: (sent.append(message), put(message))
            await page.click('#issues [data-row="4"]')
            await page.click("#go")
            await page.wait_for_selector("#view .xterm-rows")
            frame = next(m for m in sent if m["type"] == "frame")
            await page.click("#view-close")
            await until(lambda: asyncio.sleep(0, result=not [w for w in t.app.workers
                                                             if str(w.group) == served.view_group
                                                             and w.is_running]))
            await until(lambda: page.evaluate("() => !document.querySelector('#view .xterm')"))
            put(frame)                                                   # one queued before the Close (#86)
            await asyncio.sleep(0.5)
            assert await page.locator("#view .xterm").count() == 0
            assert await page.text_content("#view-name") == ""
            await page.click("#go")                                       # a view opened again draws (#86)
            await page.wait_for_selector("#view .xterm-rows")


# One instructions box per issue of each repository (#86)

class ReadByWorkspace(PaneWindows):
    """PaneWindows whose read of one workspace's windows takes `delays[workspace]` seconds (#86)."""

    def __init__(self, delays):
        super().__init__()
        self.delays = delays

    async def find(self, workspace):
        await asyncio.sleep(self.delays.get(workspace, 0))
        return await super().find(workspace)


async def test_a_late_box_of_the_same_number_in_another_repository_does_not_replace_the_chosen_one():
    # Every workspace: Start on autoflow-coordinator#4, then on autoflow-sandbox#4; the first one's window read ends
    # last. Its box does not replace the second's, and Enter starts only the issue chosen last (#86).
    from test_app import every_app
    windows = ReadByWorkspace({"autoflow-coordinator": 1.0})
    starts = []

    async def loader(repository, limit):
        return [Issue(4, "four")] if repository != "Munsik-Park/gone" else []

    async def starter(coordinator, repository, number, instructions="", **options):
        starts.append(((repository, number, instructions), options))
        return "%7"

    async def focuser(surface):
        pass

    async def places():
        return {}

    app = every_app(loader=loader, starter=starter, focuser=focuser, windows=windows, placer=places,
                    run_refresh_sec=1000, issue_refresh_sec=1000, window_check_sec=1000)
    first, second = "Munsik-Park/autoflow-coordinator#4", "Munsik-Park/autoflow-sandbox#4"
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        base = await browser_on(app, pilot)
        async with Chromium() as page:
            await page.goto(base)
            await until(lambda: rows_of(page, f'#issues [data-row="{second}"]'))
            await page.click(f'#issues [data-row="{first}"]')
            await page.click("#start")
            await page.click(f'#issues [data-row="{second}"]')
            await page.click("#start")
            await page.wait_for_selector("#instructions-text", state="visible")
            assert await page.text_content("#instructions-title") == "Instructions for autoflow-sandbox#4 (s)"
            await until(lambda: page.evaluate(
                "() => document.querySelector('#messages').textContent.includes("
                "'autoflow-coordinator#4: the instructions box is not opened, a later start was asked')"))
            assert await page.text_content("#instructions-title") == "Instructions for autoflow-sandbox#4 (s)"
            await page.fill("#instructions-text", "the second")
            await page.press("#instructions-text", "Enter")
            await until(lambda: asyncio.sleep(0, result=bool(starts)))
            await settle(app, pilot)
            assert starts == [(("Munsik-Park/autoflow-sandbox", 4, "the second"), {"detached": True})]
