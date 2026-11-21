import os

import pytest
from textual.widgets import DataTable, Input, Static
from textual.worker import WorkerCancelled

from autoflow_ide.app import IdeApp
from autoflow_ide.model import Issue, Run, Session, Step


def sample():
    return [
        Issue(1, "first", Run("run-1", [Step("analysis", "pass", "pass")], [Session("S1", "working")])),
        Issue(2, "second"),
    ]


async def test_three_panes_and_first_issue_shown():
    app = IdeApp(issues=sample(), repository="owner/repo")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#issues", DataTable).row_count == 2
        assert app.query_one("#run", DataTable).row_count == 1
        assert app.query_one("#sessions", DataTable).row_count == 1
        assert "run-1" in str(app.query_one("#status", Static).render())


async def test_moving_to_an_issue_without_a_run_clears_the_run():
    app = IdeApp(issues=sample())
    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.pause()
        assert app.query_one("#run", DataTable).row_count == 0
        assert app.query_one("#sessions", DataTable).row_count == 0
        assert "no run" in str(app.query_one("#status", Static).render())


async def test_number_keys_focus_panes():
    app = IdeApp(issues=sample())
    async with app.run_test() as pilot:
        for key, pane in (("2", "run"), ("3", "sessions"), ("1", "issues")):
            await pilot.press(key)
            assert app.focused is app.query_one(f"#{pane}", DataTable)


async def test_no_issues():
    app = IdeApp(issues=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "no issue" in str(app.query_one("#status", Static).render())


from autoflow_ide.issues import IssueSourceError


def status(app):
    return str(app.query_one("#status", Static).render())


async def test_loads_issues_of_the_repository():
    calls = []

    async def loader(repository):
        calls.append(repository)
        return [Issue(3, "three"), Issue(4, "four")]

    app = IdeApp(repository="owner/name", loader=loader)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == ["owner/name"]
        assert app.query_one("#issues", DataTable).row_count == 2
        assert "2 open issues" in status(app) and "#3" in status(app)
        await pilot.press("r")
        await app.workers.wait_for_complete()
        assert calls == ["owner/name", "owner/name"]


async def test_load_failure_shows_on_the_status_line():
    async def loader(repository):
        raise IssueSourceError("gh is not installed")

    app = IdeApp(repository="owner/name", loader=loader)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "issues: gh is not installed" in status(app)
        assert app.query_one("#issues", DataTable).row_count == 0


async def test_without_a_repository_nothing_is_loaded():
    async def loader(repository):
        raise AssertionError("must not load")

    app = IdeApp(loader=loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "no repository" in status(app)


from pathlib import Path

from autoflow_ide.focus import FocusError
from autoflow_ide.runs import RunSourceError, parse_status

RECORDED_RUNS = parse_status((Path(__file__).parent / "fixtures" / "status-run.json").read_text())


def coordinated_app(focuser=None, run_loader=None, **kw):
    async def loader(repository):
        return [Issue(4, "four"), Issue(3, "three")]

    async def runs(coordinator):
        return RECORDED_RUNS

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/coordinator",
                  run_loader=run_loader or runs, focuser=focuser, **kw)


async def settle(app, pilot):
    for worker in list(app.workers):
        try:
            await worker.wait()
        except WorkerCancelled:
            pass  # replaced by a newer worker of its exclusive group, as a 50 ms tick does (#16)
    await pilot.pause()


async def test_issue_with_a_run_comes_first_with_a_mark():
    app = coordinated_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert [str(issues.get_row_at(i)[1]) for i in range(2)] == ["3", "4"]
        assert str(issues.get_cell("3", "run")) == "●" and str(issues.get_cell("4", "run")) == ""
        assert app.current.number == 3
        assert app.query_one("#run", DataTable).row_count == 3
        assert app.query_one("#sessions", DataTable).row_count == 2
        assert "autoflow-sandbox-3-sample" in status(app) and "1 active runs" in status(app)


async def test_enter_on_a_session_focuses_its_pane():
    focused = []

    async def focuser(surface):
        focused.append(surface)

    app = coordinated_app(focuser=focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("3", "down", "enter")
        await settle(app, pilot)
        assert focused == ["%2"]


async def test_focus_failure_shows_on_the_status_line():
    async def focuser(surface):
        raise FocusError("can't find pane: %1")

    app = coordinated_app(focuser=focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("3", "enter")
        await settle(app, pilot)
        assert "S1: can't find pane: %1" in status(app)


async def test_run_failure_and_periodic_refresh():
    calls = []

    async def runs(coordinator):
        calls.append(coordinator)
        raise RunSourceError("no /coordinator/tools/status.py")

    app = coordinated_app(run_loader=runs, run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert "runs: no /coordinator/tools/status.py" in status(app)
        assert len(calls) >= 2


async def test_refresh_keeps_the_highlighted_issue():
    app = coordinated_app(run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert app.current.number == 4
        assert app.query_one("#issues", DataTable).cursor_row == 1


async def test_a_redraw_before_a_key_press_is_handled_keeps_the_key():
    # The key moves the cursor at once, and its RowHighlighted reaches the app later; a runs read redrawing the
    # table in between must not move the cursor back to the issue shown before (#16).
    app = coordinated_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        table = app.query_one("#issues", DataTable)
        assert (table.cursor_row, app.current.number) == (0, 3)
        table.action_cursor_down()
        assert (table.cursor_row, app.current.number) == (1, 3)
        app.show_runs(app.runs)
        await settle(app, pilot)
        assert (table.cursor_row, app.current.number) == (1, 4)


async def test_a_run_ending_reloads_the_issues():
    issue_calls, runs_seen = [], []

    async def loader(repository):
        issue_calls.append(repository)
        return [Issue(4, "four"), Issue(3, "three")] if len(issue_calls) == 1 else [Issue(4, "four")]

    async def runs(coordinator):
        runs_seen.append(coordinator)
        return RECORDED_RUNS if len(runs_seen) == 1 else []

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs, run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.3)
        await settle(app, pilot)
        assert len(issue_calls) == 2
        assert app.query_one("#issues", DataTable).row_count == 1
        assert app.current.number == 4


async def test_long_text_fits_the_pane_with_an_ellipsis():
    long = "a title much longer than the issues pane can show in one line of the table"
    app = IdeApp(issues=[Issue(1, long)])
    async with app.run_test(size=(100, 20)) as pilot:
        await pilot.pause()
        table = app.query_one("#issues", DataTable)
        title = str(table.get_cell("1", "title"))
        assert title.endswith("…") and len(title) < len(long)
        assert table.virtual_size.width <= table.size.width


async def test_a_new_run_does_not_move_away_from_another_run():
    second = parse_status((Path(__file__).parent / "fixtures" / "status-run.json").read_text())[0]
    second.issue, second.name = "4", "autoflow-sandbox-4-sample"
    seen = []

    async def runs(coordinator):
        seen.append(coordinator)
        return RECORDED_RUNS if len(seen) == 1 else RECORDED_RUNS + [second]

    app = coordinated_app(run_loader=runs, run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.current.number == 3
        await pilot.pause(0.3)
        await settle(app, pilot)
        assert app.current.number == 3


async def test_runs_of_other_repositories_are_counted_apart_and_not_attached():
    from dataclasses import replace
    mine = RECORDED_RUNS[0]
    other = replace(mine, name="autoflow-coordinator-3-x")     # same issue number, another repository
    prefix = replace(mine, name="autoflow-3-y")                # a repository whose name is a prefix of ours

    async def loader(repository):
        return [Issue(3, "three")]

    async def runs(coordinator):
        return [other, prefix, mine]

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.current.run.name == "autoflow-sandbox-3-sample"
        assert "1 active runs (2 in other repositories)" in status(app)


# A run whose issue is no longer open (#12): merging the PR closes the issue before the coordination session
# closes the run.

from autoflow_ide.issues import parse_issue

FIXTURES = Path(__file__).parent / "fixtures"
CLOSED_3 = parse_issue((FIXTURES / "gh-issue-closed.json").read_text())


def merged_app(issue_lists, lookup=None, run_loader=None, **kw):
    """Issue #3 has the recorded run; the loader gives each of `issue_lists` in turn, then the last one."""
    issue_calls = []

    async def loader(repository):
        issue_calls.append(repository)
        return issue_lists[min(len(issue_calls), len(issue_lists)) - 1]

    async def runs(coordinator):
        return RECORDED_RUNS

    async def found(repository, number):
        return CLOSED_3

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c",
                 run_loader=run_loader or runs, lookup=lookup or found, **kw)
    return app, issue_calls


async def test_a_run_whose_issue_closed_keeps_a_row_marked_closed():
    lookups = []

    async def lookup(repository, number):
        lookups.append((repository, number))
        return CLOSED_3

    app, _ = merged_app([[Issue(4, "four")]], lookup=lookup, run_refresh_sec=0.05)
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.2)
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert [str(issues.get_row_at(i)[1]) for i in range(2)] == ["3", "4"]
        assert str(issues.get_cell("3", "run")) == "●"
        assert str(issues.get_cell("3", "title")).startswith("(closed) word_")   # cut to the issues pane
        assert app.current.number == 3 and app.current.closed
        assert app.query_one("#run", DataTable).row_count == 3
        assert app.query_one("#sessions", DataTable).row_count == 2
        assert "#3 (closed)" in status(app) and "autoflow-sandbox-3-sample" in status(app)
        assert lookups == [("Munsik-Park/autoflow-sandbox", 3)]   # once, not on every run refresh


async def test_an_issue_that_left_the_list_keeps_its_title_while_looked_up():
    import asyncio
    release = asyncio.Event()

    async def lookup(repository, number):
        await release.wait()
        return CLOSED_3

    app, calls = merged_app([[Issue(4, "four"), Issue(3, "three")], [Issue(4, "four")]], lookup=lookup)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause(0.1)
        issues = app.query_one("#issues", DataTable)
        assert len(calls) == 2 and issues.row_count == 2
        assert str(issues.get_cell("3", "title")) == "three"
        assert not app.current.closed
        release.set()
        await settle(app, pilot)
        assert str(issues.get_cell("3", "title")).startswith("(closed) word_")   # cut to the issues pane


async def test_a_failed_lookup_shows_on_the_status_line_and_is_retried_on_refresh():
    attempts = []

    async def lookup(repository, number):
        attempts.append(number)
        raise IssueSourceError("gh: Not Found (HTTP 404)")

    app, _ = merged_app([[Issue(4, "four")]], lookup=lookup, run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert "issue #3: gh: Not Found (HTTP 404)" in status(app)
        assert attempts == [3]
        assert app.query_one("#issues", DataTable).row_count == 2    # the run is still shown
        await pilot.press("r")
        await settle(app, pilot)
        assert attempts == [3, 3]


async def test_no_row_for_a_run_before_the_open_issues_are_read():
    async def loader(repository):
        raise IssueSourceError("gh is not installed")

    async def lookup(repository, number):
        raise AssertionError("must not look up")

    async def runs(coordinator):
        return RECORDED_RUNS

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs,
                 lookup=lookup)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one("#issues", DataTable).row_count == 0
        assert "1 active runs" in status(app)


async def test_the_closed_row_goes_when_its_run_ends():
    seen = []

    async def runs(coordinator):
        seen.append(coordinator)
        return RECORDED_RUNS if len(seen) <= 2 else []

    app, calls = merged_app([[Issue(4, "four")]], run_loader=runs, run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.4)
        await settle(app, pilot)
        assert app.query_one("#issues", DataTable).row_count == 1
        assert app.current.number == 4
        assert len(calls) >= 2      # the run ending reloaded the issues


# Starting a run (#6)

from autoflow_ide.start import StartError


async def open_lookup(repository, number):
    """GitHub as the state read at Enter sees an issue: open (#85)."""
    return Issue(number, f"issue {number}", state="open")


def starting_app(starter, focuser=None):
    async def loader(repository):
        return [Issue(4, "four"), Issue(3, "three")]

    async def runs(coordinator):
        return RECORDED_RUNS      # issue 3 has a run

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs,
                  starter=starter, focuser=focuser, lookup=open_lookup)


async def test_s_starts_a_run_for_the_issue_and_goes_back_to_it_the_second_time():
    starts, focused = [], []

    async def starter(coordinator, repository, number, instructions=""):
        starts.append((coordinator, repository, number))
        return "%7"

    async def focuser(surface):
        focused.append(surface)

    app = starting_app(starter, focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down", "s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert starts == [("/c", "Munsik-Park/autoflow-sandbox", 4)]
        assert "#4: coordination session in %7" in status(app)
        await pilot.press("s")
        await settle(app, pilot)
        assert starts == [("/c", "Munsik-Park/autoflow-sandbox", 4)] and focused == ["%7"]


async def test_s_on_an_issue_with_a_run_or_a_failure_says_why():
    async def starter(coordinator, repository, number, instructions=""):
        raise StartError("no workspace for Munsik-Park/autoflow-sandbox in /base")

    app = starting_app(starter)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("s")                     # #3 has a run
        await settle(app, pilot)
        assert "#3 already has a run: autoflow-sandbox-3-sample" in status(app)
        await pilot.press("down", "s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert "start #4: no workspace for Munsik-Park/autoflow-sandbox in /base" in status(app)


async def test_s_without_a_coordinator():
    async def starter(coordinator, repository, number, instructions=""):
        raise AssertionError("must not start")

    app = IdeApp(issues=[Issue(4, "four")], starter=starter)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert "start: needs --coordinator" in status(app)


# The run pane's Enter: what happened in a step (#20)

from textual.widgets import Markdown

from autoflow_ide.detail import StepDetail, describe
from autoflow_ide.model import Step

FIXTURES = Path(__file__).parent / "fixtures"
CYCLE = parse_status((FIXTURES / "status-cycle.json").read_text())
REVIEWS = parse_status((FIXTURES / "status-reviews.json").read_text())
# The records of a run of autoflow-coordinator 0.8 (tests/fixtures/README.md, #83).
RECORDS = FIXTURES / "records" / "autoflow-coordinator-119-2026-11-19-180359-3cbd8c18"


def review_step(label, **kw):
    """A step of the recorded run whose work folder is in the fixtures, as status.py gives it (#83)."""
    [step] = [s for s in REVIEWS[0].steps if s.label == label]
    return replace(step, out=str(RECORDS / label), **kw)


def test_describe_a_running_step():
    rows = dict(describe(Step("build-1", "running", session="S2", module="build", session_state="working",
                              idle_sec=42.4, out="/r/build-1")))
    assert rows["Session"] == "S2 (working, idle 42s)" and rows["Verdict"] == "-" and rows["Result"] == "-"
    assert rows["Folder"] == "/r/build-1"
    # the end record's status, as status.py gives it (#83)
    assert dict(describe(Step("review-1", "pass", "met", "S2", "review", out="/r/review-1", result="ended")))[
        "Result"] == "ended"


def run_pane_names(app):
    table = app.query_one("#run", DataTable)
    return [str(table.get_row_at(n)[1]) for n in range(table.row_count)]


def results_app(steps):
    async def loader(repository):
        return [Issue(6, "six")]

    async def runs(coordinator):
        return [Run("autoflow-sandbox-6-detail", steps, issue="6")]

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs)


def detail_text(app):
    """The detail's text: its fields, section titles, cells, notes and entries, and the source of each Markdown part."""
    parts = [str(w.render()) for w in app.screen.query("#detail-body Static")]
    parts += [w.source for w in app.screen.query("#detail-body Markdown")]
    return "\n".join(parts)


async def test_the_run_pane_marks_a_return_and_a_rerun_with_their_cause():
    async def loader(repository):
        return [Issue(5, "five")]

    async def runs(coordinator):
        return CYCLE

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert run_pane_names(app) == ["analysis-1", "build-1", "review-1", "↩ build-2 ← review-1", "↺ review-2 ← review-1"]


async def test_enter_on_a_step_opens_it_and_c_goes_to_its_cause():
    steps = [review_step("review-1"), review_step("review-2")]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "down", "enter")                       # review-2
        await pilot.pause()
        assert isinstance(app.screen, StepDetail)
        assert app.screen.query_one("#detail").border_title == "Step 2  ↺ review-2 ← review-1"
        text = detail_text(app)
        assert "Result   ended" in text and "1  report.md" in text and "2  reviews/" in text
        assert "after the end" not in text                              # the end record lists every file (#83)
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, StepDetail)
        assert app.query_one("#run", DataTable).cursor_row == 0           # review-1
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#detail").border_title == "Step 1  review-1"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, StepDetail)


async def test_the_detail_notes_what_it_cannot_show(tmp_path):
    # The notes of the 0.8 coordinator's work folder and end record (#83).
    running = tmp_path / "review-4"
    running.mkdir()
    (running / "draft.md").write_text("so far\n")
    broken = tmp_path / "review-5"
    broken.mkdir()
    (broken / "draft.md").write_text("so far\n")
    (tmp_path / "review-5.ended.json").write_text("{not json")
    steps = [Step("CI check (gh)", "pass", "pass", "X1"),
             Step("review-4", "running", None, None, "review", "rerun", "review-1", out=str(running)),
             Step("review-5", "awaiting", "awaiting verdict", None, "review", "rerun", "review-1", out=str(broken)),
             Step("review-6", "running", None, None, "review", out=str(tmp_path / "review-6"))]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        assert "No module run: this step has no work folder." in detail_text(app)
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "not ended yet" in text and "1  draft.md" in text and "checked out" not in text
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "Could not read" in text and "review-5.ended.json" in text and "1  draft.md" in text
        assert "not ended yet" not in text and "after the end" not in text
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        assert "The work folder is not there yet." in detail_text(app)


async def test_the_detail_scrolls():
    steps = [review_step("review-2")]                                 # long Verdict and Notes cells
    app = results_app(steps)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        body = app.screen.query_one("#detail-body")
        assert app.focused is body and body.max_scroll_y > 0
        await pilot.press("pagedown")
        await pilot.pause()
        assert body.scroll_y > 0


# Reading the open issues again (#22)

from dataclasses import replace


def counting_app(run_loader, **kw):
    calls = []

    async def loader(repository):
        calls.append(repository)
        return [Issue(4, "four"), Issue(3, "three")]

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=run_loader,
                 **kw)
    return app, calls


async def test_issues_are_read_again_on_the_interval():
    async def runs(coordinator):
        return []

    app, calls = counting_app(runs, issue_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.3)
        await settle(app, pilot)
        assert len(calls) >= 3


def with_last_step(run, **changes):
    return replace(run, steps=run.steps[:-1] + [replace(run.steps[-1], **changes)])


async def test_issues_are_read_again_when_a_step_changes_not_when_only_idle_time_does():
    run = RECORDED_RUNS[0]
    reads = [
        [run],
        [with_last_step(run, state="running", verdict=None, session_state="working", idle_sec=3.0)],
        [with_last_step(run, state="running", verdict=None, session_state="working", idle_sec=8.0)],
        [with_last_step(run, state="pass", verdict="pass")],
    ]
    seen = []

    async def runs(coordinator):
        seen.append(coordinator)
        return reads[min(len(seen), len(reads)) - 1]

    app, calls = counting_app(runs, run_refresh_sec=10)   # read the runs only when the test asks
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert len(seen) == 1 and len(calls) == 1      # a run seen for the first time is not a change
        expected = [2, 2, 3]                           # step state changed; only idle time; state again
        for count in expected:
            app.refresh_runs()
            await settle(app, pilot)
            assert len(calls) == count


# Closing the coordination session window of a finished issue (#24)

from autoflow_ide.windows import Window


class FakeWindows:
    """Windows by issue; `screens` gives each issue's screens in turn, then the last one. A screen is a string,
    the window's one pane, or a list of its panes' screens (#79)."""

    def __init__(self, numbers, screens=None):
        self.open = {n: Window(f"@{n}", f"autoflow-sandbox#{n}", [f"%{n}"]) for n in numbers}
        self.given = screens or {}
        self.reads = {}
        self.closed = []

    async def find(self, workspace):
        assert workspace == "autoflow-sandbox"
        return {n: [w] for n, w in self.open.items()}      # every window of a name, a list each (#85)

    async def screens(self, window):
        number = int(window.name.split("#")[1])
        self.reads[number] = self.reads.get(number, 0) + 1
        shown = self.given.get(number, ["> "])
        screen = shown[min(self.reads[number], len(shown)) - 1]
        if isinstance(screen, Exception):
            raise screen
        return [screen] if isinstance(screen, str) else list(screen)

    async def close(self, window):
        number = int(window.name.split("#")[1])
        del self.open[number]
        self.closed.append(window.name)


def windows_app(windows, open_issues, runs, lookup, idle=0.2, check=0.05, **kw):
    """`open_issues` and `runs` are lists, or functions that give them at each read."""
    async def loader(repository):
        return open_issues() if callable(open_issues) else open_issues

    async def run_loader(coordinator):
        return runs() if callable(runs) else runs

    async def workspace_finder(coordinator, repository):
        return "autoflow-sandbox"

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=run_loader,
                  lookup=lookup, windows=windows, workspace_finder=workspace_finder,
                  window_check_sec=check, window_idle_sec=idle, **kw)


def lookup_states(states, asked):
    """Each issue's state as GitHub gives it: `states` by number, open for the others (#85)."""
    async def lookup(repository, number):
        asked.append(number)
        return Issue(number, f"issue {number}", state=states.get(number, "open"))
    return lookup


async def test_closes_the_window_of_a_closed_issue_without_a_run_after_its_screen_is_still():
    asked = []
    windows = FakeWindows([8], {8: ["writing", "report", "report"]})
    app = windows_app(windows, [Issue(4, "four")], [], lookup_states({8: "closed"}, asked), idle=0.5)
    async with app.run_test(size=(160, 24)) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.1)
        assert windows.closed == []                # the screen changed less than window_idle_sec ago
        await pilot.pause(0.8)
        await settle(app, pilot)
        assert windows.closed == ["autoflow-sandbox#8"]
        assert asked == [8, 8]                     # found closed at the first check, asked again to close it
        assert "closed autoflow-sandbox#8: #8 is closed and has no run" in status(app)


async def test_keeps_the_window_of_a_closed_issue_while_it_has_a_run():
    asked = []
    windows = FakeWindows([3])
    app = windows_app(windows, [Issue(4, "four")], RECORDED_RUNS, lookup_states({3: "closed"}, asked))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.7)                     # more than three times window_idle_sec
        await settle(app, pilot)
        assert windows.closed == []


def timed(lookup, times):
    async def timed_lookup(repository, number):
        times.append(time.monotonic())
        return await lookup(repository, number)
    return timed_lookup


def spaced(times, idle):
    return all(b - a >= idle - 0.02 for a, b in zip(times, times[1:]))


async def test_keeps_the_window_of_an_open_issue():
    asked, times = [], []
    windows = FakeWindows([4, 9])
    # 4 is in the open list; 9 is not (more open issues than listed), and GitHub says it is open.
    app = windows_app(windows, [Issue(4, "four")], [], timed(lookup_states({9: "open"}, asked), times))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert windows.closed == []
        assert 4 not in asked
        assert asked.count(9) >= 1 and spaced(times, 0.2)   # asked again only after window_idle_sec


async def test_keeps_a_window_whose_screen_keeps_changing():
    asked = []
    windows = FakeWindows([8], {8: [str(n) for n in range(100)]})
    app = windows_app(windows, [Issue(4, "four")], [], lookup_states({8: "closed"}, asked))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert windows.closed == []


async def test_keeps_windows_while_the_runs_cannot_be_read():
    asked = []
    windows = FakeWindows([8])

    async def run_loader(coordinator):
        raise RunSourceError("status.py exited with status 1")

    app = windows_app(windows, [Issue(4, "four")], [], lookup_states({8: "closed"}, asked))
    app.run_loader = run_loader
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert windows.closed == [] and asked == []


# The wait counts from the later of the screen's last change and the issue first found closed with no run

import asyncio
import time

from autoflow_ide.app import WINDOW_IDLE_SEC


class TimedWindows(FakeWindows):
    """FakeWindows that keeps when each screen was first read and when each window was closed."""

    def __init__(self, numbers, screens=None):
        super().__init__(numbers, screens)
        self.changed = {}
        self.closed_at = {}
        self.last = {}

    async def screens(self, window):
        shown = "\n".join(await super().screens(window))
        number = int(window.name.split("#")[1])
        if self.last.get(number) != shown:
            self.last[number] = shown
            self.changed[number] = time.monotonic()
        return [shown]

    async def close(self, window):
        self.closed_at[int(window.name.split("#")[1])] = time.monotonic()
        await super().close(window)


def test_the_wait_stays_five_minutes():
    assert WINDOW_IDLE_SEC == 300.0


async def test_a_waiting_session_keeps_its_window_for_the_whole_wait_after_its_issue_closes():
    idle, refresh, check = 0.5, 0.1, 0.05
    windows = TimedWindows([8])                    # the session waits for input: its screen does not change
    start = time.monotonic()
    closed_after = 0.8                             # a merge closes #8: it leaves the open list, GitHub says closed
    found_closed = []

    def is_closed():
        return time.monotonic() - start >= closed_after

    def open_issues():
        return [Issue(4, "four")] + ([] if is_closed() else [Issue(8, "eight")])

    async def lookup(repository, number):
        closed = is_closed()
        if closed and not found_closed:
            found_closed.append(time.monotonic())
        return Issue(number, "eight", state="closed" if closed else "open")

    app = windows_app(windows, open_issues, [], lookup, idle=idle, issue_refresh_sec=refresh)
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause(closed_after + idle - 0.1)
        assert windows.closed == []                # the screen has been still for longer than the wait
        await pilot.pause(refresh + 2 * check + 0.3)
        await settle(app, pilot)
        assert windows.closed == ["autoflow-sandbox#8"]
        closed_at = start + closed_after
        assert windows.closed_at[8] - found_closed[0] >= idle - 0.02
        assert idle - 0.02 <= windows.closed_at[8] - closed_at <= idle + refresh + 2 * check + 0.15
        assert "closed autoflow-sandbox#8: #8 is closed and has no run" in status(app)


async def test_the_wait_counts_from_the_screen_when_it_changes_after_the_issue_is_found_closed():
    idle = 0.3
    # #8 is closed from the start; its screen goes on changing for about half a second.
    windows = TimedWindows([8], {8: [str(n) for n in range(10)]})
    asked = []
    app = windows_app(windows, [Issue(4, "four")], [], lookup_states({8: "closed"}, asked), idle=idle)
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause(1.5)
        await settle(app, pilot)
        assert windows.closed == ["autoflow-sandbox#8"]
        assert asked[0] == 8 and windows.closed_at[8] - windows.changed[8] >= idle - 0.02


async def test_the_wait_counts_from_the_end_of_the_run_of_a_closed_issue():
    idle = 0.5
    windows = TimedWindows([3])
    start = time.monotonic()
    run_ends = 0.6
    asked = []

    def runs():
        return RECORDED_RUNS if time.monotonic() - start < run_ends else []

    app = windows_app(windows, [Issue(4, "four")], runs, lookup_states({3: "closed"}, asked), idle=idle,
                      run_refresh_sec=0.05)
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause(run_ends + 1.0)
        await settle(app, pilot)
        assert windows.closed == ["autoflow-sandbox#3"]
        assert windows.closed_at[3] - (start + run_ends) >= idle - 0.02


async def test_the_wait_counts_from_when_a_slow_lookup_returns():
    idle, check, delay = 0.5, 0.3, 0.2
    windows = TimedWindows([8])
    returned = []

    async def lookup(repository, number):
        if not returned:
            await asyncio.sleep(delay)             # gh is slow to answer the first time
        returned.append(time.monotonic())
        return Issue(number, "eight", state="closed")

    app = windows_app(windows, [Issue(4, "four")], [], lookup, idle=idle, check=check)
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause(check + delay + idle + 2 * check)
        await settle(app, pilot)
        assert windows.closed == ["autoflow-sandbox#8"]
        assert windows.closed_at[8] - returned[0] >= idle - 0.02


async def test_keeps_the_window_of_an_issue_reopened_during_the_wait():
    asked = []
    windows = FakeWindows([8])
    # #8 is not in the open list (more open issues than listed): GitHub says closed, then open again.

    async def lookup(repository, number):
        asked.append(number)
        return Issue(number, "eight", state="closed" if len(asked) == 1 else "open")

    app = windows_app(windows, [Issue(4, "four")], [], lookup, idle=0.2)
    async with app.run_test(size=(160, 24)) as pilot:
        await pilot.pause(0.6)
        await settle(app, pilot)
        assert windows.closed == []
        assert asked[:2] == [8, 8]


async def test_a_failing_lookup_is_asked_at_most_once_per_wait():
    for screens, first in (([str(n) for n in range(100)], None), (None, "closed")):
        # A screen that keeps changing and a lookup that always fails; a closed issue whose lookup then fails.
        asked, times = [], []
        windows = FakeWindows([8], {8: screens} if screens else None)

        async def lookup(repository, number):
            asked.append(number)
            if first and len(asked) == 1:
                return Issue(number, "eight", state=first)
            raise IssueSourceError("gh: could not connect")

        app = windows_app(windows, [Issue(4, "four")], [], timed(lookup, times), idle=0.2)
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause(0.7)
            await settle(app, pilot)
            assert windows.closed == []
            assert 2 <= len(times) <= 5 and spaced(times, 0.2)


async def test_info_above_the_run_shows_the_workspace_and_where_the_run_is():
    from autoflow_ide.start import StartError, Workspace

    run = Run("repo-1-k", [Step("analysis", "running")], [Session("S1", "working", "%5")], issue="1",
              branch="issue-1-k", reference="review-only", coordination="%4", checkout="/nowhere/wt-1-k")

    async def loader(repository):
        return [Issue(1, "one"), Issue(2, "two")]

    async def runs(coordinator):
        return [run]

    async def workspace(coordinator, repository):
        return Workspace("repo", repository, "/nowhere/clone", "main")

    async def places():
        return {"%4": "2:1 repo#1", "%5": "2:1 repo#1"}

    app = IdeApp(repository="owner/repo", loader=loader, coordinator="/c", run_loader=runs,
                 workspace_reader=workspace, placer=places)
    async with app.run_test(size=(160, 30)) as pilot:
        for _ in range(3):
            await app.workers.wait_for_complete()
            await pilot.pause()
        info = str(app.query_one("#info", Static).render())
        for line in ("repo      owner/repo", "workspace repo", "clone     /nowhere/clone (main)", "run       repo-1-k",
                     "workflow  review-only", "branch    issue-1-k", "worktree  /nowhere/wt-1-k",
                     "session   tmux 2:1 repo#1"):
            assert line in info
        assert "records" not in info                                  # null: the line is left out
        await pilot.press("down")
        await pilot.pause()
        info = str(app.query_one("#info", Static).render())
        assert "run       none" in info and "branch" not in info

    async def no_workspace(coordinator, repository):
        raise StartError("no workspace for owner/repo in /base")

    app = IdeApp(repository="owner/repo", loader=loader, coordinator="/c", run_loader=runs,
                 workspace_reader=no_workspace, placer=places)
    async with app.run_test(size=(160, 30)) as pilot:
        for _ in range(3):
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert "workspace no workspace for owner/repo in /base" in str(app.query_one("#info", Static).render())


# A long run name or path is cut in the middle (#36, #38)

from autoflow_ide.app import cut_middle, info_text


def test_cut_middle():
    assert cut_middle("short", 10) == "short"
    assert cut_middle("abcdefghijklmnopqrstuvwxyz", 11) == "abcd…uvwxyz"     # 40% of 10 from the start
    assert cut_middle("가나다라마바사", 9) == "가…마바사"                      # two cells a character
    assert cut_middle("abc", 1) == "…"


def test_info_cuts_run_and_paths_in_the_middle_and_the_rest_at_their_end():
    name = "autoflow-coordinator-62-2026-11-06-165619-8a269048"
    worktree = "~/work/coordinator/autoflow-coordinator/autoflow-coordinator-wt-62-2026-11-06-165619-8a269048"
    workflow = "a-workflow-name-that-is-much-longer-than-the-room"
    run, tree, flow = info_text([("run", name), ("worktree", worktree), ("workflow", workflow)], 40).plain.splitlines()
    assert run == "run       " + name[:11] + "…" + name[-18:] and len(run) == 40
    assert tree == "worktree  " + worktree[:11] + "…" + worktree[-18:]
    assert flow == "workflow  " + workflow                     # cut at its end by the widget, as before
    assert info_text([("run", name)]).plain == "run       " + name   # width not known yet: nothing cut


async def test_info_keeps_the_start_and_end_of_the_worktree_at_121_columns():
    run = Run("autoflow-coordinator-62-2026-11-06-165619-8a269048", [Step("x", "running")], issue="62",
              checkout="/nowhere/coordinator/autoflow-coordinator/autoflow-coordinator-wt-62-2026-11-06-165619-8a269048")

    async def loader(repository):
        return [Issue(62, "sixty-two")]

    async def runs(coordinator):
        return [run]

    app = IdeApp(repository="Munsik-Park/autoflow-coordinator", loader=loader, coordinator="/c", run_loader=runs)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        lines = str(app.query_one("#info", Static).render()).splitlines()
        worktree = next(l for l in lines if l.startswith("worktree")).rstrip()
        assert worktree.startswith("worktree  /nowhere/coord") and "…" in worktree and worktree.endswith("165619-8a269048")
        assert next(l for l in lines if l.startswith("run")).rstrip().endswith("165619-8a269048")
        assert app.query_one("#run-pane").border_title == "Run"          # the run name is in the info


# The row's Verdict and Notes cells in the detail (#40)

from autoflow_ide.detail import row_cells


def test_row_cells_show_what_the_verdict_and_the_table_leave_out():
    step = Step("F1 fix (outside AutoFlow)", "pass", "pass", "X1",
                verdict_text="pass, head 253901c pushed (= PR head); COORDINATOR.md line 58 only", notes="")
    assert row_cells(step) == [("Verdict, as the run record has it",
                                "pass, head 253901c pushed (= PR head); COORDINATOR.md line 58 only")]
    plain = Step("s1", "pass", "pass", "S1", "review", verdict_text="Pass", notes="pr #65, timeout 1800")
    assert row_cells(plain) == [("Notes", "pr #65, timeout 1800")]          # the cell says no more than pass
    assert row_cells(Step("no row yet", "running", None, "X1")) == []          # no row: both null
    assert row_cells(Step("s2", "running", None, "S1", "review", verdict_text="", notes=None)) == []


async def test_the_detail_shows_the_row_cells_above_the_work_folder():
    steps = [Step("F1 fix (outside AutoFlow)", "pass", "pass", "X1", verdict_text="pass, [head] 253901c pushed",
                  notes="x1-2-instruction.txt"),
             review_step("review-1", verdict_text="goal missed: F1 holds (Medium)", notes="F1 fixed by X1, then re-review")]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "pass, [head] 253901c pushed" in text                          # as written, not read as markup
        assert "Notes" in text and "x1-2-instruction.txt" in text and "No module run" in text
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert text.index("F1 fixed by X1") < text.index("1  report.md")      # the work folder's entries (#83)


async def test_the_ide_version_is_in_the_bottom_right_corner():
    app = IdeApp(issues=sample(), version="68afb41")
    async with app.run_test(size=(121, 37)) as pilot:
        await pilot.pause()
        assert app.query_one("#sessions-pane").border_subtitle == "ide 68afb41"
        bottom = app.export_screenshot()
    app = IdeApp(issues=sample())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.query_one("#sessions-pane").border_subtitle


# Every workspace of the base (#44)

import json
import tempfile

from autoflow_ide.app import RUN_MARK
from autoflow_ide.start import Workspace as WorkspaceEntry

EVERY = [WorkspaceEntry("autoflow-coordinator", "Munsik-Park/autoflow-coordinator", "~/c", "main"),
         WorkspaceEntry("autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/s", "main"),
         WorkspaceEntry("gone", "Munsik-Park/gone", None, None)]


def every_app(runs=(), recent=("autoflow-coordinator", "autoflow-sandbox", "gone"), state_file=None, run_loader=None,
              loader=None, workspaces_reader=None, **kw):
    """Every workspace of EVERY; `recent` are the workspaces chosen recently (all of them: all unfolded)."""
    if state_file is None:
        state_file = Path(tempfile.mkdtemp()) / "state.json"
        state_file.write_text(json.dumps({"recent": list(recent)}))
    given_loader = loader

    async def workspaces(coordinator):
        return EVERY

    async def loader(repository, limit):
        assert limit == 5
        if repository == "Munsik-Park/gone":
            raise IssueSourceError("Could not resolve to a Repository")
        if repository == "Munsik-Park/autoflow-coordinator":
            return [Issue(67, "instructions"), Issue(24, "abnormal states")]
        return [Issue(4, "four"), Issue(3, "three")]

    async def given_runs(coordinator):
        return list(runs)

    kw.setdefault("lookup", open_lookup)  # not the real gh (#85)
    return IdeApp(coordinator="/c", loader=given_loader or loader, run_loader=run_loader or given_runs,
                  workspaces_reader=workspaces_reader or workspaces, state_file=state_file, **kw)


def keys(table):
    return [table.coordinate_to_cell_key((n, 0)).row_key.value for n in range(table.row_count)]


SANDBOX_3 = Run("autoflow-sandbox-3-k", [Step("analysis", "pass", "pass")], [Session("S1", "working")], issue="3")
COORD_3 = Run("autoflow-coordinator-3-k", [Step("review", "running")], issue="3")


async def test_every_workspace_under_its_row_with_its_runs():
    app = every_app(runs=[SANDBOX_3])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert keys(issues) == ["Munsik-Park/autoflow-coordinator", "Munsik-Park/autoflow-coordinator#67",
                                "Munsik-Park/autoflow-coordinator#24", "Munsik-Park/autoflow-sandbox",
                                "Munsik-Park/autoflow-sandbox#3", "Munsik-Park/autoflow-sandbox#4",
                                "Munsik-Park/gone"]
        # The run's issue first in its workspace, marked; the same number in another repository is not.
        assert str(issues.get_cell("Munsik-Park/autoflow-sandbox#3", "run")) == RUN_MARK
        # The pane is narrow: the workspace's row starts with its name and why its issues could not be read.
        assert str(issues.get_cell("Munsik-Park/gone", "title")).startswith("gone: Could not reso")
        assert "1 active runs" in status(app)
        assert "gone: Could not resolve" in status(app)


async def test_every_workspace_tells_the_same_number_apart():
    app = every_app(runs=[COORD_3])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        # Issue 3 of the coordinator is not in its open list: a row for its run, first in its workspace.
        assert keys(issues)[1:4] == ["Munsik-Park/autoflow-coordinator#3", "Munsik-Park/autoflow-coordinator#67",
                                     "Munsik-Park/autoflow-coordinator#24"]
        assert str(issues.get_cell("Munsik-Park/autoflow-sandbox#3", "run")) == ""


async def test_a_workspace_row_shows_no_run_and_its_workspace():
    app = every_app(runs=[SANDBOX_3])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox"))
        await pilot.pause()
        assert app.current is None
        assert app.query_one("#run", DataTable).row_count == 0
        info = str(app.query_one("#info", Static).render())
        assert "Munsik-Park/autoflow-sandbox" in info and "autoflow-sandbox" in info
        await pilot.press("s")      # nothing to start on a workspace row
        await settle(app, pilot)
        assert "start" not in app.messages or not app.messages["start"]
        app.refresh_runs()          # a refresh keeps the cursor on the row
        await settle(app, pilot)
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox"
        await pilot.press("down")
        assert app.current.number == 3 and app.current.repository == "Munsik-Park/autoflow-sandbox"
        assert app.query_one("#run", DataTable).row_count == 1


async def test_a_redraw_before_a_key_press_onto_a_workspace_row_is_handled_keeps_the_row():
    # As for an issue row: a runs read redrawing the table before the key's RowHighlighted is handled keeps the
    # cursor on the workspace row the key moved it to (#16).
    app = every_app(runs=[SANDBOX_3])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-coordinator#24"))
        await pilot.pause()
        assert app.current.number == 24
        issues.action_cursor_down()
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox" and app.current.number == 24
        app.show_runs(app.runs)
        await settle(app, pilot)
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox"
        assert app.on_header == "Munsik-Park/autoflow-sandbox" and app.current is None


async def test_s_starts_the_issue_of_its_own_repository():
    started = []

    async def starter(coordinator, repository, number, instructions=""):
        started.append((repository, number))
        return "%9"

    app = every_app(starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert started == [("Munsik-Park/autoflow-sandbox", 4)]
        assert "autoflow-sandbox#4: coordination session in %9" in status(app)


class EveryWindows(FakeWindows):
    def __init__(self, windows):
        super().__init__([])
        self.open = windows

    async def find(self, workspace):
        return {n: [w] for (ws, n), w in self.open.items() if ws == workspace}     # a list each (#85)

    async def screens(self, window):
        return ["still"]

    async def close(self, window):
        self.closed.append(window.name)
        self.open = {k: w for k, w in self.open.items() if w is not window}


async def test_closes_finished_windows_in_every_workspace():
    windows = EveryWindows({("autoflow-sandbox", 9): Window("@1", "autoflow-sandbox#9", ["%1"]),
                            ("autoflow-coordinator", 9): Window("@2", "autoflow-coordinator#9", ["%2"]),
                            ("autoflow-coordinator", 67): Window("@3", "autoflow-coordinator#67", ["%3"])})

    async def lookup(repository, number):
        return Issue(number, "x", state="closed" if repository.endswith("sandbox") else "open")

    app = every_app(windows=windows, lookup=lookup, window_check_sec=0.05, window_idle_sec=0.2)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        # Sandbox #9 is closed; coordinator #9 is open on GitHub and #67 is in its open list.
        assert windows.closed == ["autoflow-sandbox#9"]


async def test_unfolded_at_start_with_a_run_or_chosen_recently(tmp_path):
    app = every_app(runs=[COORD_3], recent=["autoflow-sandbox"])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        # The coordinator has a run, the sandbox was chosen recently; gone is folded.
        assert keys(issues) == ["Munsik-Park/autoflow-coordinator", "Munsik-Park/autoflow-coordinator#3",
                                "Munsik-Park/autoflow-coordinator#67", "Munsik-Park/autoflow-coordinator#24",
                                "Munsik-Park/autoflow-sandbox", "Munsik-Park/autoflow-sandbox#4",
                                "Munsik-Park/autoflow-sandbox#3", "Munsik-Park/gone"]
        assert str(issues.get_cell("Munsik-Park/gone", "run")) == "▸"
        assert str(issues.get_cell("Munsik-Park/autoflow-sandbox", "run")) == "▾"


async def test_enter_folds_and_unfolds_a_workspace_and_remembers_it(tmp_path):
    state = tmp_path / "state.json"
    app = every_app(state_file=state)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert keys(issues)[0] == "Munsik-Park/autoflow-coordinator" and len(keys(issues)) == 3  # all folded
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox"))
        await pilot.press("enter")
        await settle(app, pilot)
        assert keys(issues)[1:4] == ["Munsik-Park/autoflow-sandbox", "Munsik-Park/autoflow-sandbox#4",
                                     "Munsik-Park/autoflow-sandbox#3"]
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox"
        assert json.loads(state.read_text()) == {"recent": ["autoflow-sandbox"]}
        await pilot.press("down")
        assert app.current.number == 4
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox"))
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(keys(issues)) == 3
        assert str(issues.get_cell("Munsik-Park/autoflow-sandbox", "title")) == "autoflow-sandbox  (2)"
        assert json.loads(state.read_text()) == {"recent": []}
    # The next start opens what was chosen: nothing now.
    app = every_app(state_file=state)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        assert len(keys(app.query_one("#issues", DataTable))) == 3


async def test_a_run_that_starts_unfolds_its_workspace(tmp_path):
    runs = []

    async def run_loader(coordinator):
        return list(runs)

    app = every_app(recent=[], run_loader=run_loader, run_refresh_sec=0.05)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert len(keys(issues)) == 3
        runs.append(SANDBOX_3)
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert "Munsik-Park/autoflow-sandbox#3" in keys(issues)
        assert app.current.number == 3 and app.current.repository == "Munsik-Park/autoflow-sandbox"


async def test_starting_a_run_remembers_its_workspace(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"recent": ["autoflow-coordinator", "autoflow-sandbox"]}))

    async def starter(coordinator, repository, number, instructions=""):
        return "%9"

    app = every_app(state_file=state, starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert json.loads(state.read_text()) == {"recent": ["autoflow-sandbox", "autoflow-coordinator"]}


async def test_a_shows_only_the_issues_with_a_run_and_back():
    app = every_app(runs=[SANDBOX_3, COORD_3], recent=[])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/gone"))
        await pilot.press("a")
        await settle(app, pilot)
        assert keys(issues) == ["Munsik-Park/autoflow-coordinator", "Munsik-Park/autoflow-coordinator#3",
                                "Munsik-Park/autoflow-sandbox", "Munsik-Park/autoflow-sandbox#3"]
        assert app.query_one("#issues-pane").border_title == "Workspaces · running"
        assert app.current.number == 3          # the cursor's workspace row is gone: the first issue
        issues.move_cursor(row=0)
        await pilot.press("enter")               # no folding in this mode
        await settle(app, pilot)
        assert len(keys(issues)) == 4
        app.refresh_runs()
        await settle(app, pilot)
        assert len(keys(issues)) == 4
        await pilot.press("a")
        await settle(app, pilot)
        assert app.query_one("#issues-pane").border_title == "Workspaces"
        assert "Munsik-Park/gone" in keys(issues) and "Munsik-Park/autoflow-coordinator#67" in keys(issues)


async def test_a_with_one_repository():
    app = coordinated_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert [str(issues.get_row_at(i)[1]) for i in range(issues.row_count)] == ["3"]
        assert app.query_one("#issues-pane").border_title == "Issues · running"


def shown_keys(app):
    """The footer's labels by key, as the active bindings give them."""
    return {key: b.binding.description for key, b in app.active_bindings.items() if b.enabled and b.binding.show}


async def test_the_left_pane_is_named_for_what_it_holds():
    one = coordinated_app()
    async with one.run_test() as pilot:
        await settle(one, pilot)
        assert one.query_one("#issues-pane").border_title == "Issues"
        assert shown_keys(one)["1"] == "Issues"
        assert [str(c.label) for c in one.query_one("#issues", DataTable).columns.values()] == ["", "#", "Title"]
    every = every_app()
    async with every.run_test(size=(121, 37)) as pilot:
        await settle(every, pilot)
        assert every.query_one("#issues-pane").border_title == "Workspaces"
        assert shown_keys(every)["1"] == "Workspaces"
        columns = every.query_one("#issues", DataTable).columns.values()
        assert [str(c.label) for c in columns] == ["", "Workspace / Issue"]
        every.query_one("#run", DataTable).focus()
        await pilot.press("1")
        assert every.focused is every.query_one("#issues", DataTable)


async def test_hash_finds_a_listed_issue_in_a_folded_workspace():
    app = every_app(recent=[])
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox"))
        await pilot.press("number_sign", "3", "enter")
        await settle(app, pilot)
        assert app.current.number == 3 and app.current.repository == "Munsik-Park/autoflow-sandbox"
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox#3"
        assert app.focused is issues


async def test_hash_looks_up_an_issue_not_listed_and_s_says_it_is_closed():
    asked, started = [], []

    async def lookup(repository, number):
        asked.append((repository, number))
        return Issue(number, "an old one", state="closed")

    async def starter(coordinator, repository, number, instructions=""):
        started.append(number)
        return "%9"

    app = every_app(lookup=lookup, starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("number_sign", "1", "2", "s", "a", "/", "enter")   # not digits: not typed, not keys
        await settle(app, pilot)
        assert asked == [("Munsik-Park/autoflow-sandbox", 12)] and started == []
        assert keys(issues)[issues.cursor_row] == "Munsik-Park/autoflow-sandbox#12"
        await pilot.press("s")
        await settle(app, pilot)
        assert started == [] and "autoflow-sandbox#12 is closed" in status(app)
        await pilot.press("escape")                                 # clears what was found
        await settle(app, pilot)
        assert "Munsik-Park/autoflow-sandbox#12" not in keys(issues)


async def test_escape_closes_the_find_box_without_finding():
    app = every_app()
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        await pilot.press("number_sign", "1", "escape")
        await settle(app, pilot)
        assert not app.query_one("#find").display and app.focused is app.query_one("#issues", DataTable)


async def test_hash_with_one_repository():
    async def lookup(repository, number):
        return Issue(number, "nine")

    app = coordinated_app(lookup=lookup)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("number_sign", "9", "enter")
        await settle(app, pilot)
        assert app.current.number == 9 and app.current.repository == "Munsik-Park/autoflow-sandbox"


async def test_a_with_no_run_says_so_until_one_starts():
    runs = []

    async def run_loader(coordinator):
        return list(runs)

    app = every_app(run_loader=run_loader, run_refresh_sec=0.05)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        assert keys(app.query_one("#issues", DataTable)) == [] and "no issue has a run" in status(app)
        runs.append(SANDBOX_3)
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert "no issue has a run" not in status(app)
        runs.clear()
        await pilot.pause(0.2)
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        assert "no issue has a run" not in status(app)


async def test_hash_goes_to_exactly_that_number():
    asked = []

    async def loader(repository):
        return [Issue(200, "two hundred"), Issue(20, "twenty"), Issue(2, "two")]

    async def lookup(repository, number):
        asked.append(number)
        return Issue(number, "x")

    async def runs(coordinator):
        return []

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs,
                 lookup=lookup)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("number_sign", "2", "0", "enter")
        await settle(app, pilot)
        assert app.current.number == 20 and asked == []
        assert app.query_one("#issues", DataTable).row_count == 3      # nothing added


async def test_hash_shows_what_the_number_is_and_s_starts_only_an_open_issue():
    started = []

    async def lookup(repository, number):
        return {29: Issue(29, "fix the tz", pull=True), 2: Issue(2, "old fix", state="closed", pull=True),
                12: Issue(12, "an old one", state="closed")}[number]

    async def starter(coordinator, repository, number, instructions=""):
        started.append(number)
        return "%9"

    app = every_app(lookup=lookup, starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        for number, shown in ((29, "29  (PR) "), (2, "2  (closed PR) "), (12, "12  (closed) ")):
            await pilot.press("number_sign", *str(number), "enter")
            await settle(app, pilot)
            row = f"Munsik-Park/autoflow-sandbox#{number}"
            assert keys(issues)[issues.cursor_row] == row
            assert str(issues.get_cell(row, "title")).startswith(shown)    # the title follows, cut to the pane
            await pilot.press("s")
            await settle(app, pilot)
        assert started == []


async def test_a_notice_shows_first_and_goes_while_counts_stay():
    async def starter(coordinator, repository, number, instructions=""):
        return "%9"

    app = every_app(starter=starter, notice_sec=0.3)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert status(app).startswith("autoflow-sandbox#4: coordination session in %9  |  Munsik-Park/autoflow-sandbox")
        await pilot.pause(0.5)
        assert "coordination session" not in status(app)
        assert status(app).startswith("Munsik-Park/autoflow-sandbox  |  #4")
        assert "active runs" in status(app)          # what is true now stays


async def test_a_newer_notice_in_the_same_slot_gets_its_own_time():
    app = every_app(notice_sec=0.3)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        app.set_message("find", "first")
        await pilot.pause(0.2)
        app.set_message("find", "second")
        await pilot.pause(0.2)
        assert "second" in status(app)               # the first one's timer does not clear the second
        await pilot.pause(0.3)
        assert "second" not in status(app)


async def test_the_status_line_says_what_the_number_is():
    async def lookup(repository, number):
        return Issue(number, "old fix", state="closed", pull=True)

    app = every_app(lookup=lookup)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        await pilot.press("number_sign", "2", "enter")
        await settle(app, pilot)
        assert "  |  #2 (closed PR)  |  " in status(app)


# The coordinator's base and where it was set (#58)

async def test_info_shows_the_base_and_where_it_was_set_read_again_with_the_issues():
    from autoflow_ide.start import Setting, StartError

    answers = [Setting("/nowhere/work/coordinator", "/nowhere/.autoflow/coordination.json"),
               Setting("/nowhere/other-base", None),
               Setting("/nowhere/fx/base", "/nowhere/coordination/coordination.local.json"),
               StartError("no /c/tools/config.py")]
    asked = []

    async def base(coordinator):
        asked.append(coordinator)
        answer = answers[min(len(asked), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def base_line(app):
        return next(l for l in str(app.query_one("#info", Static).render()).splitlines() if l.startswith("base")).rstrip()

    app = every_app(base_reader=base)
    async with app.run_test(size=(124, 36)) as pilot:
        await settle(app, pilot)
        assert base_line(app) == "base      /nowhere/work/coordinator (global settings)"
        await pilot.press("r")
        await settle(app, pilot)
        assert base_line(app) == "base      /nowhere/other-base (built-in default)"
        await pilot.press("r")
        await settle(app, pilot)
        assert base_line(app) == "base      /nowhere/fx/base (local settings)"
        await pilot.press("r")
        await settle(app, pilot)
        assert base_line(app) == "base      no /c/tools/config.py"
        assert asked == ["/c"] * 4


# Adding a workspace through a coordination session (#60)

async def test_w_asks_a_coordination_session_for_a_workspace_and_shows_it_unfolded_when_made():
    from autoflow_ide.focus import FocusError

    made = []
    workspaces = list(EVERY)
    created, focused = [], []

    async def read_workspaces(coordinator):
        return workspaces

    async def loader(repository, limit):
        return [Issue(1, "first")] if repository == "Munsik-Park/new" else [Issue(4, "four")]

    async def runs(coordinator):
        return []

    async def creator(coordinator, repository):
        created.append((coordinator, repository))
        return "%9"

    async def focuser(pane):
        focused.append(pane)
        if len(focused) > 1:
            raise FocusError("can't find pane")

    state = Path(tempfile.mkdtemp()) / "state.json"
    state.write_text(json.dumps({"recent": []}))
    app = IdeApp(coordinator="/c", loader=loader, run_loader=runs, workspaces_reader=read_workspaces,
                 state_file=state, workspace_creator=creator, focuser=focuser)
    async with app.run_test(size=(124, 36)) as pilot:
        await settle(app, pilot)
        await pilot.press("w", *"Munsik-Park/new", "enter")
        await settle(app, pilot)
        assert created == [("/c", "Munsik-Park/new")]
        assert "workspace for Munsik-Park/new: coordination session in %9" in app.messages["workspace"]
        assert app.query_one("#issues", DataTable).has_focus                   # the box closed
        await pilot.press("w", *"git@github.com:Munsik-Park/new.git", "enter")  # the same repository, as a clone address
        await settle(app, pilot)
        assert created == [("/c", "Munsik-Park/new")] and focused == ["%9"]       # back to that session
        await pilot.press("w", *"Munsik-Park/new", "enter")                    # its pane is gone
        await settle(app, pilot)
        assert "press w to open it again" in app.messages["workspace"]
        await pilot.press("w", *"https://github.com/Munsik-Park/new", "enter")
        await settle(app, pilot)
        assert created[1] == ("/c", "https://github.com/Munsik-Park/new")       # the address as typed
        workspaces.append(WorkspaceEntry("new", "Munsik-Park/new", "~/n", "main"))   # the session wrote it
        await pilot.press("r")
        await settle(app, pilot)
        assert "Munsik-Park/new" not in app.folded and app.creating == {}
        assert json.loads(state.read_text())["recent"][0] == "new"
        assert "Munsik-Park/new#1" in keys(app.query_one("#issues", DataTable))


async def test_w_opens_nothing_for_what_is_not_a_github_repository_or_a_workspace_that_exists():
    from autoflow_ide.start import StartError

    created = []

    async def creator(coordinator, repository):
        created.append(repository)
        raise StartError(f"{repository} already has a workspace: autoflow-sandbox")

    app = every_app(workspace_creator=creator)
    async with app.run_test(size=(124, 36)) as pilot:
        await settle(app, pilot)
        await pilot.press("w", *"autoflow", "enter")
        await settle(app, pilot)
        assert created == [] and app.messages["workspace"] == "add workspace: autoflow is not a GitHub repository"
        await pilot.press("w", *"Munsik-Park/autoflow-sandbox", "enter")
        await settle(app, pilot)
        assert app.messages["workspace"] == "add workspace: Munsik-Park/autoflow-sandbox already has a workspace: autoflow-sandbox"
        assert app.creating == {}
        await pilot.press("w", "a", "escape")                                 # Esc closes the box, opens nothing
        await settle(app, pilot)
        assert not app.query_one("#find", Input).display and len(created) == 1


# The highlighted step is kept when the issue list is redrawn (#65)

async def test_the_highlighted_step_is_kept_when_the_issue_list_is_redrawn():
    run = Run("autoflow-sandbox-3-k", [Step("a", "pass", "pass"), Step("b", "pass", "pass"),
                                       Step("c", "pass", "pass"), Step("d", "running")],
              [Session("S1", "working")], issue="3")
    app = every_app(runs=[run])
    async with app.run_test(size=(124, 36)) as pilot:
        await settle(app, pilot)
        issues, steps = app.query_one("#issues", DataTable), app.query_one("#run", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#3"))
        await settle(app, pilot)
        await pilot.press("2")
        steps.move_cursor(row=2)
        for redraw in (app.render_issues, lambda: app.show_runs(list(app.runs))):   # as every read of runs does
            redraw()
            await settle(app, pilot)
            await pilot.pause(0.2)
            assert steps.cursor_row == 2 and app.current.number == 3
        await pilot.press("r")                                                    # the issues read again
        await settle(app, pilot)
        await pilot.pause(0.2)
        assert steps.cursor_row == 2
        await pilot.press("1", "up")                                              # moving by hand still shows
        await settle(app, pilot)
        assert app.on_header == "Munsik-Park/autoflow-sandbox" and app.current is None


# Opening a listed output (#67), the work folder's entries and the folders among them (#83)

async def test_a_number_opens_an_entry_a_folder_opens_into_its_entries_and_esc_goes_back_one_level():
    from autoflow_ide.detail import OutputView

    app = results_app([review_step("review-1")])
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, StepDetail)
        await pilot.press("1")                                              # report.md
        await pilot.pause()
        assert isinstance(app.screen, OutputView)
        assert app.screen.query_one("#detail").border_title == "report.md"
        assert [m.source for m in app.screen.query("#detail-body Markdown")] == [
            (RECORDS / "review-1" / "report.md").read_text()]
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is detail                                         # back to the step, not out of it
        await pilot.press("2")                                              # reviews/
        await pilot.pause()
        folder = app.screen
        assert folder is not detail and not isinstance(folder, OutputView)
        assert "reviews" in folder.query_one("#detail").border_title
        text = detail_text(app)
        assert "1  claude.md" in text and "2  codex.md" in text
        await pilot.press("1")                                              # reviews/claude.md
        await pilot.pause()
        assert isinstance(app.screen, OutputView)
        assert app.screen.query_one("#detail").border_title.endswith("claude.md")
        assert [m.source for m in app.screen.query("#detail-body Markdown")] == [
            (RECORDS / "review-1" / "reviews" / "claude.md").read_text()]
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is folder
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is detail
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, (StepDetail, OutputView)) and app.screen is not folder


async def test_entries_past_the_ninth_are_listed_with_no_key(tmp_path):
    from autoflow_ide.detail import OutputView

    out = tmp_path / "build-1"
    out.mkdir()
    for n in range(1, 12):
        (out / f"a{n:02}.md").write_text(f"# a{n:02}\n")
    app = results_app([Step("build-1", "running", None, "S1", "build", out=str(out))])
    async with app.run_test(size=(140, 60)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert all(f"{n}  a{n:02}.md" in text for n in range(1, 12))     # all 11 listed
        assert "1–9" in str(app.screen.query_one("#detail-hint").render())
        await pilot.press("9")
        await pilot.pause()
        assert isinstance(app.screen, OutputView) and app.screen.query_one("#detail").border_title == "a09.md"
        await pilot.press("escape", "0")                                    # no key opens the tenth or eleventh
        await pilot.pause()
        assert isinstance(app.screen, StepDetail)


async def test_names_in_a_work_folder_are_shown_as_written_and_not_read_as_markup(tmp_path):
    # A module run names its files: a name holding [ is shown as it is, and no tag in it styles or acts (#83).
    from textual.content import Content
    from autoflow_ide.detail import FolderView, OutputView

    out = tmp_path / "build-1"
    (out / "[@click=app.quit]q").mkdir(parents=True)
    (out / "[@click=app.quit]q" / "[bold]b.md").write_text("# b\n")
    app = results_app([Step("build-1", "running", None, "S1", "build", out=str(out))])
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        assert "1  [@click=app.quit]q/" in detail_text(app)
        await pilot.press("1")
        await pilot.pause()
        assert isinstance(app.screen, FolderView)
        title = Content.from_markup(app.screen.query_one("#detail").border_title)
        assert (title.plain, title.spans) == ("[@click=app.quit]q/", [])
        assert str(app.screen.query_one("#detail-fields").render()).endswith("/build-1/[@click=app.quit]q")
        assert "1  [bold]b.md" in detail_text(app)
        await pilot.click("#detail-fields")                                 # no link to click on
        await pilot.pause()
        assert app.is_running and isinstance(app.screen, FolderView)
        await pilot.press("1")
        await pilot.pause()
        assert isinstance(app.screen, OutputView)
        title = Content.from_markup(app.screen.query_one("#detail").border_title)
        assert (title.plain, title.spans) == ("[@click=app.quit]q/[bold]b.md", [])
        assert str(app.screen.query_one("#detail-fields").render()).endswith("/[@click=app.quit]q/[bold]b.md")
        await pilot.click("#detail-fields")
        await pilot.pause()
        assert app.is_running and isinstance(app.screen, OutputView)


async def test_a_folder_that_cannot_be_listed_is_named_with_the_reason_not_shown_empty(tmp_path):
    # As the change names an end record it cannot read, a folder it cannot list says why (#83).
    from autoflow_ide.detail import FolderView

    out = tmp_path / "build-1"
    (out / "locked").mkdir(parents=True)
    (out / "locked" / "a.md").write_text("a\n")
    shut = tmp_path / "build-2"
    shut.mkdir()
    (out / "locked").chmod(0)
    shut.chmod(0)
    try:
        try:
            os.listdir(shut)
        except PermissionError:
            pass
        else:
            # chmod 0 does not stop root, so the folders stay listable and there is nothing to name (#83)
            pytest.skip("chmod 0 does not make a folder unlistable here (running as root)")
        app = results_app([Step("build-1", "running", None, "S1", "build", out=str(out)),
                           Step("build-2", "running", None, "S1", "build", out=str(shut))])
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(app, pilot)
            await pilot.press("2", "enter", "1")
            await pilot.pause()
            assert isinstance(app.screen, FolderView)
            text = detail_text(app)
            assert "Could not read" in text and "locked: Permission denied" in text and "(empty folder)" not in text
            await pilot.press("escape", "escape", "down", "enter")
            await pilot.pause()
            text = detail_text(app)
            assert "Could not read" in text and "build-2: Permission denied" in text and "(empty folder)" not in text
    finally:
        (out / "locked").chmod(0o755)
        shut.chmod(0o755)


# Steps past the pane's height scroll into view (#69)

async def test_the_run_table_scrolls_to_a_step_below_the_pane():
    run = Run("autoflow-sandbox-3-k", [Step(f"step-{n}", "pass", "pass", "S1") for n in range(1, 24)],
              [Session("S1", "working")], issue="3", branch="b", reference="full", checkout="/w", records="/r",
              coordination="%1")
    app = every_app(runs=[run])
    async with app.run_test(size=(124, 33)) as pilot:
        await settle(app, pilot)
        issues, steps = app.query_one("#issues", DataTable), app.query_one("#run", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#3"))
        await settle(app, pilot)
        pane = app.query_one("#run-pane").content_region
        assert steps.region.bottom <= pane.bottom                     # inside the pane, under the info lines
        await pilot.press("2", *["down"] * 22)
        await settle(app, pilot)
        assert steps.cursor_row == 22 and steps.scroll_y > 0           # the table scrolled
        top = steps.scroll_y
        assert top <= 22 < top + steps.size.height - 1                 # step 23 is in view (below the header row)


# Reading each workspace's issues with its own gh-token (#74).

import os
import stat


async def test_each_workspace_is_read_with_its_own_token(tmp_path, monkeypatch):
    log = tmp_path / "gh-calls"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/sh\necho "$4 ${{GH_TOKEN-unset}}" >> "{log}"\necho "[]"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(gh.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_TOKEN", "the-shells")
    folders = {name: tmp_path / name for name in ("mine", "theirs", "loose", "none")}
    for name, folder in folders.items():
        folder.mkdir()
    for name, mode in (("mine", 0o600), ("theirs", 0o600), ("loose", 0o644)):
        (folders[name] / "gh-token").write_text(f"tok-{name}\n")
        (folders[name] / "gh-token").chmod(mode)
    workspaces = [WorkspaceEntry(name, f"{name}-owner/{name}", None, None, folder=folder)
                  for name, folder in folders.items()]

    async def read_workspaces(coordinator):
        return workspaces

    async def no_runs(coordinator):
        return []

    app = IdeApp(coordinator="/c", run_loader=no_runs, workspaces_reader=read_workspaces,
                 state_file=tmp_path / "state.json")
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        calls = sorted(log.read_text().splitlines())
        # Each with its own; one without a gh-token as before, with the shell's; one that cannot be used is not read.
        assert calls == ["mine-owner/mine tok-mine", "none-owner/none the-shells", "theirs-owner/theirs tok-theirs"]
        issues = app.query_one("#issues", DataTable)
        assert str(issues.get_cell("loose-owner/loose", "title")).startswith("loose: gh-token:")
        assert "chmod 600" in status(app) and "tok-loose" not in status(app)


# The waiting mark: an issue whose coordination session window shows a prompt, or whose run has a work session
# in waiting-input (#79).

from rich.cells import cell_len

from autoflow_ide.app import FOLDED_MARK, UNFOLDED_MARK, WAIT_MARK
from autoflow_ide.windows import WindowError

SCREENS = Path(__file__).parent / "fixtures" / "screens"
CHOICE = (SCREENS / "choice-prompt.txt").read_text()
PERMISSION = (SCREENS / "permission-prompt.txt").read_text()
WORKING = (SCREENS / "working.txt").read_text()
TURN_END = (SCREENS / "turn-end.txt").read_text()


def marks(app):
    issues = app.query_one("#issues", DataTable)
    return {key: str(issues.get_cell(key, "run")) for key in keys(issues)}


def sandbox_run(*states, number=3, key="k"):
    return Run(f"autoflow-sandbox-{number}-{key}", [Step("analysis", "running")],
               [Session(f"S{n}", state) for n, state in enumerate(states, 1)], issue=str(number))


def waiting_app(windows, runs=(), **kw):
    """Issues 4 and 3 of autoflow-sandbox; the window check runs only when a test calls check()."""
    return windows_app(windows, [Issue(4, "four"), Issue(3, "three")], runs, lookup_states({}, []), check=100, **kw)


async def check(app, pilot):
    app.check_windows()
    await settle(app, pilot)


def test_the_wait_mark_is_one_cell_and_unlike_the_other_marks():
    assert cell_len(WAIT_MARK) == 1
    assert WAIT_MARK not in (RUN_MARK, UNFOLDED_MARK, FOLDED_MARK, "")


async def test_a_window_showing_the_workflow_choice_marks_its_issue_within_one_check():
    windows = FakeWindows([3, 4], {3: [CHOICE], 4: [PERMISSION]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert marks(app) == {"3": RUN_MARK, "4": ""}
        await check(app, pilot)
        assert windows.reads == {3: 1, 4: 1}
        # In place of ● for the issue with a run; for the one without, before its run is prepared.
        assert marks(app) == {"3": WAIT_MARK, "4": WAIT_MARK}
        assert keys(app.query_one("#issues", DataTable)) == ["3", "4"]     # the order is unchanged


async def test_an_issue_started_with_s_is_marked_before_its_run_is_prepared():
    async def starter(coordinator, repository, number, instructions=""):
        return "%4"

    windows = FakeWindows([], {4: [CHOICE]})
    app = waiting_app(windows, [sandbox_run("working")], starter=starter)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down", "s")
        await settle(app, pilot)
        await pilot.press("enter")                 # the instructions box, empty (#85)
        await settle(app, pilot)
        assert ("Munsik-Park/autoflow-sandbox", 4) in app.started
        windows.open[4] = Window("@4", "autoflow-sandbox#4", ["%4"])   # the window `s` opened
        await check(app, pilot)
        assert marks(app)["4"] == WAIT_MARK


async def test_the_earlier_mark_returns_on_the_check_after_the_answer():
    windows = FakeWindows([3, 4], {3: [CHOICE, WORKING], 4: [CHOICE, TURN_END]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        assert marks(app) == {"3": WAIT_MARK, "4": WAIT_MARK}
        await check(app, pilot)
        assert marks(app) == {"3": RUN_MARK, "4": ""}


async def test_a_window_waits_when_any_of_its_panes_shows_a_prompt():
    windows = FakeWindows([3, 4], {3: [[CHOICE, TURN_END], [TURN_END, WORKING]], 4: [[TURN_END, CHOICE]]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        assert marks(app) == {"3": WAIT_MARK, "4": WAIT_MARK}
        await check(app, pilot)
        assert marks(app) == {"3": RUN_MARK, "4": WAIT_MARK}


async def test_a_screen_that_cannot_be_read_keeps_its_mark_and_a_window_that_is_gone_drops_it():
    windows = FakeWindows([3, 4], {3: [CHOICE, WindowError("can't find pane: %3"), WORKING], 4: [CHOICE]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        assert marks(app) == {"3": WAIT_MARK, "4": WAIT_MARK}
        del windows.open[4]                       # its window was closed by hand
        await check(app, pilot)
        assert marks(app) == {"3": WAIT_MARK, "4": ""}
        assert "autoflow-sandbox#3: can't find pane: %3" in status(app)
        await check(app, pilot)
        assert marks(app) == {"3": RUN_MARK, "4": ""}


async def test_a_failed_listing_of_the_windows_keeps_the_marks():
    windows = FakeWindows([3], {3: [CHOICE]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await check(app, pilot)

        async def find(workspace):
            raise WindowError("no server running on /tmp/tmux-501/default")

        windows.find = find
        await check(app, pilot)
        assert marks(app)["3"] == WAIT_MARK
        assert "windows: no server running" in status(app)


async def test_a_work_session_waiting_for_input_marks_its_issue():
    state = {"runs": [sandbox_run("working", "waiting-input")]}
    app = waiting_app(FakeWindows([]), lambda: state["runs"])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert marks(app) == {"3": WAIT_MARK, "4": ""}
        for states in (("working", "turn-end"), ("exited",)):
            state["runs"] = [sandbox_run(*states)]
            app.refresh_runs()
            await settle(app, pilot)
            assert marks(app) == {"3": RUN_MARK, "4": ""}
        # The issue shows its first run; a second run of it with a session waiting marks it too.
        state["runs"] = [sandbox_run("working"), sandbox_run("waiting-input", key="j")]
        app.refresh_runs()
        await settle(app, pilot)
        assert app.current.run.name == "autoflow-sandbox-3-k"
        # The two runs are two sessions, each a row under the issue (#85); the issues' own rows:
        assert {k: m for k, m in marks(app).items() if "/" not in k} == {"3": WAIT_MARK, "4": ""}


async def test_a_recorded_status_with_a_session_waiting_for_input_marks_its_issue():
    runs = parse_status((Path(__file__).parent / "fixtures" / "status-waiting.json").read_text())
    app = windows_app(FakeWindows([]), [Issue(5, "five"), Issue(4, "four")], runs, lookup_states({}, []), check=100)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert marks(app) == {"5": WAIT_MARK, "4": ""}


async def test_either_source_holds_the_mark():
    state = {"runs": [sandbox_run("waiting-input")]}
    windows = FakeWindows([3], {3: [CHOICE, CHOICE, WORKING]})
    app = waiting_app(windows, lambda: state["runs"])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        state["runs"] = [sandbox_run("working")]          # the work session was answered; the window still waits
        app.refresh_runs()
        await settle(app, pilot)
        assert marks(app)["3"] == WAIT_MARK
        state["runs"] = [sandbox_run("waiting-input")]    # and now the other way round
        app.refresh_runs()
        await check(app, pilot)
        await check(app, pilot)
        assert marks(app)["3"] == WAIT_MARK
        state["runs"] = [sandbox_run("working")]
        app.refresh_runs()
        await settle(app, pilot)
        assert marks(app)["3"] == RUN_MARK


async def test_the_check_that_marks_an_issue_does_not_move_the_cursor():
    windows = FakeWindows([3], {3: [CHOICE]})
    app = waiting_app(windows, [sandbox_run("working")])
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        await settle(app, pilot)
        await check(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert marks(app)["3"] == WAIT_MARK
        assert app.current.number == 4 and app.cursor_key(issues) == "4"


async def test_every_workspace_marks_the_issue_of_its_own_repository():
    class ChoiceWindows(EveryWindows):
        async def screens(self, window):
            return [CHOICE if window.name == "autoflow-sandbox#4" else TURN_END]

    windows = ChoiceWindows({("autoflow-sandbox", 4): Window("@1", "autoflow-sandbox#4", ["%1"]),
                             ("autoflow-coordinator", 67): Window("@2", "autoflow-coordinator#67", ["%2"])})
    waiting_run = Run("autoflow-coordinator-24-k", [Step("review", "running")], [Session("S1", "waiting-input")],
                      issue="24")
    app = every_app(runs=[waiting_run], windows=windows, window_check_sec=100)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        shown = marks(app)
        assert shown["Munsik-Park/autoflow-sandbox#4"] == WAIT_MARK
        assert shown["Munsik-Park/autoflow-coordinator#24"] == WAIT_MARK
        assert shown["Munsik-Park/autoflow-coordinator#67"] == ""
        assert shown["Munsik-Park/autoflow-sandbox#3"] == ""


async def test_given_issues_show_a_waiting_session():
    issues = [Issue(1, "first", Run("run-1", [Step("analysis", "running")], [Session("S1", "waiting-input")])),
              Issue(2, "second", Run("run-2", [Step("analysis", "running")], [Session("S1", "working")]))]
    app = IdeApp(issues=issues, repository="owner/repo")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert marks(app) == {"1": WAIT_MARK, "2": RUN_MARK}


# An issue found by its number, read again at the end of each read of the issues (#80)

SANDBOX = "Munsik-Park/autoflow-sandbox"


class Source:
    """GitHub as a found issue's lookup sees it: what each number is now, or the error a read of it gets."""

    def __init__(self, **answers):
        self.answers = dict(answers)   # number as "n12" -> Issue, or IssueSourceError
        self.asked = []

    def set(self, number, answer):
        self.answers[f"n{number}"] = answer

    async def __call__(self, repository, number):
        self.asked.append(number)
        answer = self.answers[f"n{number}"]
        if isinstance(answer, IssueSourceError):
            raise answer
        return replace(answer)   # a new object on each read, as gh gives

    def count(self, number):
        return self.asked.count(number)


async def find(pilot, app, number):
    await pilot.press("number_sign", *str(number), "enter")
    await settle(app, pilot)


async def wait(pilot, seconds=0.3):
    """Pause without waiting on a held read, as settle would."""
    for _ in range(int(seconds / 0.05)):
        await pilot.pause(0.05)


async def test_a_found_issue_that_closes_shows_closed_after_the_interval_with_no_key():
    source = Source(n12=Issue(12, "an old one"))
    app = every_app(lookup=source, issue_refresh_sec=0.05)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 12)
        held = app.current
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  an old")   # cut to the pane
        assert "  |  #12  |  " in status(app)
        source.set(12, Issue(12, "an old one", state="closed"))
        await pilot.pause(0.3)                                       # no key: the interval reads it again
        await settle(app, pilot)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert "  |  #12 (closed)  |  " in status(app)
        assert keys(issues)[issues.cursor_row] == f"{SANDBOX}#12" and app.current is held


async def test_a_found_issue_is_read_again_once_on_r_a_step_change_and_a_run_ending():
    run = RECORDED_RUNS[0]
    answer = [[run]]

    async def runs(coordinator):
        return answer[0]

    source = Source(n12=Issue(12, "twelve"))
    app = coordinated_app(run_loader=runs, lookup=source, run_refresh_sec=10)   # the runs read when the test asks
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        await find(pilot, app, 12)
        assert source.count(12) == 1
        await pilot.press("r")
        await settle(app, pilot)
        assert source.count(12) == 2
        answer[0] = [with_last_step(run, state="pass", verdict="pass")]            # a step changes
        app.refresh_runs()
        await settle(app, pilot)
        assert source.count(12) == 3
        answer[0] = []                                                              # the run ends
        app.refresh_runs()
        await settle(app, pilot)
        assert source.count(12) == 4
        app.refresh_runs()                                                          # nothing changed: not read
        await settle(app, pilot)
        assert source.count(12) == 4


async def test_a_found_row_says_what_the_number_is_now():
    source = Source(n12=Issue(12, "a fix", pull=True), n13=Issue(13, "done", state="closed"))
    app = coordinated_app(lookup=source)
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        await find(pilot, app, 12)
        assert str(issues.get_cell("12", "title")).startswith("(PR) ") and "  |  #12 (PR)  |  " in status(app)
        source.set(12, Issue(12, "a fix, merged", state="closed", pull=True))
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell("12", "title")).startswith("(closed PR) ")         # cut to the pane
        assert app.current.title == "a fix, merged" and "  |  #12 (closed PR)  |  " in status(app)
        await find(pilot, app, 13)
        assert str(issues.get_cell("13", "title")).startswith("(closed) ") and "  |  #13 (closed)  |  " in status(app)
        source.set(13, Issue(13, "done"))                                            # reopened
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell("13", "title")) == "done" and "  |  #13  |  " in status(app)
        assert issues.row_count == 3 and app.current.number == 13


async def test_found_issues_of_two_workspaces_are_read_again_each_with_its_run_and_a_listed_one_is_not():
    runs = []

    async def run_loader(coordinator):
        return list(runs)

    source = Source(n12=Issue(12, "twelve"), n30=Issue(30, "thirty"))
    app = every_app(run_loader=run_loader, lookup=source, run_refresh_sec=10)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-coordinator#67"))
        await find(pilot, app, 30)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 4)                                    # listed: shown from the list, not looked up
        await find(pilot, app, 12)
        runs.append(Run("autoflow-sandbox-12-k", [Step("analysis", "pass", "pass")], [Session("S1", "working")],
                        issue="12"))                                 # a run starts for the found issue
        app.refresh_runs()
        await settle(app, pilot)
        held, rows, asked = app.current, issues.row_count, (source.count(12), source.count(30))
        assert str(issues.get_cell(f"{SANDBOX}#12", "run")) == RUN_MARK and held.run is not None
        source.set(12, Issue(12, "twelve", state="closed"))
        source.set(30, Issue(30, "renamed"))
        await pilot.press("r")
        await settle(app, pilot)
        assert (source.count(12), source.count(30)) == (asked[0] + 1, asked[1] + 1) and 4 not in source.asked
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert str(issues.get_cell("Munsik-Park/autoflow-coordinator#30", "title")) == "30  renamed"
        assert str(issues.get_cell(f"{SANDBOX}#12", "run")) == RUN_MARK
        assert app.current is held and held.run is not None and issues.row_count == rows
        assert app.query_one("#run", DataTable).row_count == 1 and "autoflow-sandbox-12-k" in status(app)


async def test_a_found_issue_whose_lookup_fails_keeps_its_state_and_the_status_line_says_why_until_it_is_read():
    source = Source(n12=Issue(12, "an old one"))
    app = every_app(lookup=source, notice_sec=0.3)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 12)
        rows = keys(issues)
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  an old") and keys(issues) == rows
        assert "  |  #12  |  " in status(app) and "issue autoflow-sandbox#12: gh: HTTP 502" in status(app)
        await pilot.pause(0.5)                                       # not a notice: it stays, and not first
        assert "issue autoflow-sandbox#12: gh: HTTP 502" in status(app)
        assert status(app).startswith(f"{SANDBOX}  |  #12  |  ")
        source.set(12, Issue(12, "an old one", state="closed"))
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert "  |  #12 (closed)  |  " in status(app) and "HTTP 502" not in status(app)
        source.set(12, IssueSourceError("gh: HTTP 502"))             # a closed one stays closed while it fails
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert "  |  #12 (closed)  |  " in status(app) and "issue autoflow-sandbox#12: gh: HTTP 502" in status(app)


async def test_a_found_failure_and_a_run_row_lookup_are_said_each_on_its_own():
    source = Source(n3=IssueSourceError("gh: Not Found (HTTP 404)"), n12=Issue(12, "twelve"))
    app, _ = merged_app([[Issue(4, "four")]], lookup=source, run_refresh_sec=0.05)
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert "issue #3: gh: Not Found (HTTP 404)" in status(app)
        await find(pilot, app, 12)
        source.set(12, Issue(12, "twelve", state="closed"))          # the found read succeeds, the run row's fails
        await pilot.press("r")
        await settle(app, pilot)
        assert "issue #3: gh: Not Found (HTTP 404)" in status(app) and "issue #12" not in status(app)
        source.set(3, CLOSED_3)                                      # the reverse
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert "issue #3:" not in status(app) and "issue #12: gh: HTTP 502" in status(app)


async def test_a_failed_read_of_the_list_still_reads_the_found_issue():
    lists = []

    async def loader(repository):
        lists.append(repository)
        if len(lists) > 1:
            raise IssueSourceError("gh: HTTP 503")
        return [Issue(4, "four"), Issue(3, "three")]

    source = Source(n12=Issue(12, "twelve"))
    app = IdeApp(repository=SANDBOX, loader=loader, coordinator="/c", run_loader=lambda c: asyncio.sleep(0, []),
                 lookup=source)
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        await find(pilot, app, 12)
        source.set(12, Issue(12, "twelve", state="closed"))
        await pilot.press("r")
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert str(issues.get_cell("12", "title")) == "(closed) twelve" and "  |  #12 (closed)  |  " in status(app)
        assert "issues: gh: HTTP 503" in status(app) and "issue #12" not in status(app)
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert "issues: gh: HTTP 503" in status(app) and "issue #12: gh: HTTP 502" in status(app)


async def test_a_failed_read_of_the_workspaces_or_of_a_workspace_list_still_reads_the_found_issue():
    reads = []

    async def workspaces(coordinator):
        reads.append(coordinator)
        if len(reads) > 1:
            raise StartError("config.py base: exit 1")
        return EVERY

    source = Source(n12=Issue(12, "twelve"), n7=Issue(7, "seven"))
    app = every_app(lookup=source, workspaces_reader=workspaces)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 12)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/gone"))   # its list cannot be read
        await find(pilot, app, 7)
        source.set(12, Issue(12, "twelve", state="closed"))
        source.set(7, Issue(7, "seven", state="closed"))
        await pilot.press("r")                                       # the workspaces cannot be read
        await settle(app, pilot)
        assert "workspaces: config.py base: exit 1" in status(app)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert str(issues.get_cell("Munsik-Park/gone#7", "title")).startswith("7  (closed) ")
        assert "  |  #7 (closed)  |  " in status(app)


async def test_a_found_issue_the_list_shows_is_drawn_from_the_list_and_not_read():
    listed = [[Issue(4, "four"), Issue(3, "three")]]

    async def loader(repository, limit):
        if repository == "Munsik-Park/gone":
            raise IssueSourceError("Could not resolve to a Repository")
        return listed[0] if repository == SANDBOX else [Issue(67, "instructions")]

    source = Source(n12=Issue(12, "twelve"))
    app = every_app(loader=loader, lookup=source)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 12)
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert "issue autoflow-sandbox#12: gh: HTTP 502" in status(app)
        asked = source.count(12)
        listed[0] = [Issue(12, "twelve, listed"), Issue(4, "four"), Issue(3, "three")]   # among the 5 recent now
        for _ in range(2):
            await pilot.press("r")
            await settle(app, pilot)
            assert str(issues.get_cell(f"{SANDBOX}#12", "title")) == "12  twelve, listed"
            assert "autoflow-sandbox#12:" not in status(app)         # its failure goes with its held copy
        assert source.count(12) == asked                             # drawn from the list: not read


def entering_and_leaving_the_list(source):
    """Every workspace whose sandbox list the test changes: (app, the sandbox list as a one-item list)."""
    listed = [[Issue(4, "four"), Issue(3, "three")]]

    async def loader(repository, limit):
        if repository == "Munsik-Park/gone":
            raise IssueSourceError("Could not resolve to a Repository")
        return listed[0] if repository == SANDBOX else [Issue(67, "instructions")]

    return every_app(loader=loader, lookup=source), listed


async def found_then_listed(pilot, app, listed):
    await settle(app, pilot)
    issues = app.query_one("#issues", DataTable)
    issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
    await find(pilot, app, 12)
    listed[0] = [Issue(12, "twelve, listed"), Issue(4, "four"), Issue(3, "three")]   # among the 5 recent now
    await pilot.press("r")
    await settle(app, pilot)
    assert str(issues.get_cell(f"{SANDBOX}#12", "title")) == "12  twelve, listed"
    return issues


async def test_a_found_issue_that_leaves_the_list_closed_shows_closed_after_one_read():
    source = Source(n12=Issue(12, "twelve"))
    app, listed = entering_and_leaving_the_list(source)
    async with app.run_test(size=(121, 37)) as pilot:
        issues = await found_then_listed(pilot, app, listed)
        asked = source.count(12)
        listed[0] = [Issue(4, "four"), Issue(3, "three")]            # a merge closes it: it leaves the list
        source.set(12, Issue(12, "twelve, listed", state="closed"))
        await pilot.press("r")                                       # one read
        await settle(app, pilot)
        assert source.count(12) == asked + 1
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")).startswith("12  (closed) ")
        assert keys(issues)[issues.cursor_row] == f"{SANDBOX}#12" and "  |  #12 (closed)  |  " in status(app)


async def test_a_found_issue_that_leaves_the_list_and_fails_keeps_its_state_and_says_why():
    source = Source(n12=Issue(12, "twelve"))
    app, listed = entering_and_leaving_the_list(source)
    async with app.run_test(size=(121, 37)) as pilot:
        issues = await found_then_listed(pilot, app, listed)
        listed[0] = [Issue(4, "four"), Issue(3, "three")]
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert str(issues.get_cell(f"{SANDBOX}#12", "title")) == "12  twelve"
        assert "  |  #12  |  " in status(app) and "issue autoflow-sandbox#12: gh: HTTP 502" in status(app)


@pytest.mark.parametrize("late", [Issue(12, "stale", state="closed"), IssueSourceError("gh: HTTP 502")],
                         ids=["late success", "late failure"])
@pytest.mark.parametrize("then", ["escape and find it again", "find another"])
async def test_a_read_out_when_esc_or_a_new_find_changes_the_found_issue_is_dropped(late, then):
    release = asyncio.Event()
    reads = []

    async def lookup(repository, number):
        reads.append(number)
        if len(reads) == 2:                                          # the read at the end of `r`, held
            await release.wait()
            if isinstance(late, IssueSourceError):
                raise late
            return replace(late)
        return Issue(number, "fresh")

    app = every_app(lookup=lookup)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
        await find(pilot, app, 12)
        await pilot.press("r")
        await wait(pilot)
        assert reads == [12, 12]
        if then == "escape and find it again":
            await pilot.press("escape")                              # cleared while it is read
            await pilot.pause()
            assert f"{SANDBOX}#12" not in keys(issues)
            issues.move_cursor(row=keys(issues).index(f"{SANDBOX}#4"))
            number = 12
        else:
            number = 13
        await pilot.press("number_sign", *str(number), "enter")      # a new find, while the read is out
        await wait(pilot)
        assert reads == [12, 12, number] and app.current.title == "fresh"
        release.set()
        await settle(app, pilot)
        assert reads == [12, 12, number]
        assert str(issues.get_cell(f"{SANDBOX}#{number}", "title")) == f"{number}  fresh"
        assert not app.current.closed and "HTTP 502" not in status(app)
        assert number == 12 or f"{SANDBOX}#12" not in keys(issues)
        await pilot.press("escape")
        await settle(app, pilot)
        await pilot.press("r")
        await settle(app, pilot)
        assert f"{SANDBOX}#{number}" not in keys(issues) and reads == [12, 12, number]


async def test_esc_and_a_new_find_clear_a_found_failure():
    source = Source(n12=Issue(12, "twelve"), n13=Issue(13, "thirteen"))
    app = coordinated_app(lookup=source)
    async with app.run_test(size=(160, 30)) as pilot:
        await settle(app, pilot)
        await find(pilot, app, 12)
        source.set(12, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert "issue #12: gh: HTTP 502" in status(app)
        await find(pilot, app, 13)                                   # replaces #12: its failure goes
        assert "issue #12" not in status(app)
        source.set(13, IssueSourceError("gh: HTTP 502"))
        await pilot.press("r")
        await settle(app, pilot)
        assert "issue #13: gh: HTTP 502" in status(app)
        await pilot.press("escape")                                  # clears what was found, and its failure
        await settle(app, pilot)
        assert "issue #13" not in status(app) and "13" not in keys(app.query_one("#issues", DataTable))


# Several coordination sessions of one issue (#85). The runs are tests/fixtures/status-sessions.json (X2) and its
# reordered recording (X3), the windows those of tests/fixtures/tmux-list-panes-sessions.txt (X1), as windows.py
# finds them: every window of an issue, each with its own panes.

from textual.widgets import Footer

SESSIONS = parse_status((FIXTURES / "status-sessions.json").read_text())
REORDERED = parse_status((FIXTURES / "status-sessions-reordered.json").read_text())
RUN_A, RUN_B, RUN_C = sorted(SESSIONS, key=lambda r: r.name)     # A and B in pane %1 of @1, C in pane %2 of @2
WINDOWS_6 = [Window("@1", "autoflow-sandbox#6", ["%1"]), Window("@2", "autoflow-sandbox#6", ["%2", "%3"])]
WINDOWS_8 = [Window("@3", "autoflow-sandbox#8", ["%4"])]
SANDBOX_WORKSPACE = WorkspaceEntry("autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/s", "main")
WIDE = (200, 40)


class SessionWindows:
    """Windows by issue, a list each, as windows.py finds every window of a name (#85). `screens` gives each
    window's screens by window id in turn, then the last one: a string is its first pane's screen, the others
    showing "> ". A close of a window in `fail_close` fails."""

    def __init__(self, windows, screens=None, fail_close=()):
        self.open = {n: list(ws) for n, ws in windows.items()}
        self.given = screens or {}
        self.fail_close = set(fail_close)
        self.reads, self.finds, self.closed, self.closed_at = {}, 0, [], {}

    async def find(self, workspace):
        self.finds += 1
        return {n: list(ws) for n, ws in self.open.items() if ws}

    async def screens(self, window):
        self.reads[window.id] = self.reads.get(window.id, 0) + 1
        shown = self.given.get(window.id, ["> "])
        screen = shown[min(self.reads[window.id], len(shown)) - 1]
        return [screen] + ["> "] * (len(window.panes) - 1)

    async def close(self, window):
        if window.id in self.fail_close:
            raise WindowError(f"can't find window: {window.id}")
        for ws in self.open.values():
            if window in ws:
                ws.remove(window)
        self.closed.append(window.id)
        self.closed_at[window.id] = time.monotonic()


def sessions_app(windows=None, runs=None, open_issues=None, lookup=None, idle=100, check=100, **kw):
    """Issues 6, 8 and 4 of autoflow-sandbox; `runs` (X2 by default) and `open_issues` are lists or functions
    giving them at each read. The window check runs only when a test calls check(), unless `check` is short."""
    async def workspace(coordinator, repository):
        return SANDBOX_WORKSPACE

    async def places():
        return {}

    return windows_app(windows if windows is not None else SessionWindows({6: WINDOWS_6, 8: WINDOWS_8}),
                       open_issues if open_issues is not None else [Issue(6, "six"), Issue(8, "eight"),
                                                                    Issue(4, "four")],
                       runs if runs is not None else SESSIONS, lookup or lookup_states({}, []),
                       idle=idle, check=check, workspace_reader=workspace, placer=places, **kw)


def issues_table(app):
    return app.query_one("#issues", DataTable)


def cursor_key(app):
    table = issues_table(app)
    return keys(table)[table.cursor_row] if table.row_count else None


def children(app, key):
    """The keys of the rows under the row `key` of an issue, up to the next issue's or workspace's row."""
    bounds = {app.row_key(i) for i in app.issues} | {g.repository for g in app.groups}
    rows = keys(issues_table(app))
    under = []
    for row in rows[rows.index(key) + 1:]:
        if row in bounds:
            break
        under.append(row)
    return under


def row_text(app, key):
    return " ".join(str(c) for c in issues_table(app).get_row(key))


def info(app):
    return str(app.query_one("#info", Static).render())


def info_labels(app):
    return [line[:10].strip() for line in info(app).splitlines()]


def shown_run(app):
    """Which of X2's runs the info names, or None."""
    named = [r.name for r in (RUN_A, RUN_B, RUN_C) if r.name in info(app)]
    assert len(named) <= 1, named
    return named[0] if named else None


async def highlight(app, pilot, key):
    issues_table(app).move_cursor(row=keys(issues_table(app)).index(key))
    await settle(app, pilot)


async def highlight_now(app, pilot, key):
    """As highlight, without waiting on a held worker (#85)."""
    issues_table(app).move_cursor(row=keys(issues_table(app)).index(key))
    await wait(pilot, 0.1)


async def runs_by_row(app, pilot, key):
    """The run each row under issue row `key` shows when highlighted, in row order."""
    shown = []
    for row in children(app, key):
        await highlight(app, pilot, row)
        shown.append(shown_run(app))
    return shown


def session_names(app):
    return keys(app.query_one("#sessions", DataTable))


async def test_each_session_of_an_issue_is_a_row_under_it_and_two_runs_of_one_pane_are_two_rows():
    # Every window and every active run of the issue; the two runs of pane %1 are not folded into one row (#85).
    app = sessions_app()
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        rows = keys(issues_table(app))
        assert rows[0] == "6" and len(children(app, "6")) == 3
        assert rows[4:] == ["8", "4"]
        assert await runs_by_row(app, pilot, "6") == [RUN_A.name, RUN_B.name, RUN_C.name]
        # One session (issue 8's window) or none (issue 4): no child row and today's mark.
        assert children(app, "8") == [] and children(app, "4") == []
        assert (marks(app)["6"], marks(app)["8"], marks(app)["4"]) == (RUN_MARK, "", "")


async def test_a_prompt_in_the_second_window_alone_marks_the_issue_and_that_windows_session():
    # The issue's mark is that of any of its sessions, and each window is judged on its own (#79, #85).
    windows = SessionWindows({6: WINDOWS_6, 8: WINDOWS_8}, {"@2": [CHOICE]})
    app = sessions_app(windows)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        assert marks(app)["6"] == WAIT_MARK
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        assert marks(app)[by_run[RUN_C.name]] == WAIT_MARK                  # C's pane is in @2
        assert marks(app)[by_run[RUN_A.name]] == marks(app)[by_run[RUN_B.name]] == RUN_MARK


def recording_starter(starts, pane="%9", error=None):
    async def starter(coordinator, repository, number, instructions):
        starts.append((repository, number, instructions))
        if error:
            raise error
        return pane
    return starter


async def test_S_opens_another_session_for_an_issue_with_runs_and_adds_its_row_at_once():
    # As s does, also when the issue has sessions and runs; the row is there before any window check (#85).
    starts = []
    windows = SessionWindows({6: WINDOWS_6, 8: WINDOWS_8})
    app = sessions_app(windows, starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        await highlight(app, pilot, "6")
        await pilot.press("S")
        await settle(app, pilot)
        assert app.query_one("#find", Input).display                       # the instructions box (#85)
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 6, "")]
        new = [k for k in children(app, "6") if "%9" in row_text(app, k)]
        assert len(children(app, "6")) == 4 and len(new) == 1
        assert cursor_key(app) == new[0]


async def test_S_refuses_what_s_refuses():
    # A closed issue, a pull request, a starter's StartError (an unusable gh-token), no coordinator (#85).
    starts = []
    issues = [Issue(5, "five", state="closed"), Issue(7, "seven", pull=True), Issue(4, "four")]
    error = StartError("autoflow-sandbox/gh-token: readable by you only: chmod 600")
    app = sessions_app(SessionWindows({}), runs=[], open_issues=issues, starter=recording_starter(starts, error=error))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        await highlight(app, pilot, "5")
        await pilot.press("S")
        await settle(app, pilot)
        assert "#5 is closed" in status(app) and not box.display
        await highlight(app, pilot, "7")
        await pilot.press("S")
        await settle(app, pilot)
        assert "#7 is a pull request" in status(app) and not box.display
        await highlight(app, pilot, "4")
        await pilot.press("S")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 4, "")]
        assert "start #4: autoflow-sandbox/gh-token: readable by you only: chmod 600" in status(app)
        assert children(app, "4") == []

    app = IdeApp(issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test() as pilot:
        await pilot.press("S")
        await settle(app, pilot)
        assert "start: needs --coordinator" in status(app)
        assert len(starts) == 1


@pytest.mark.parametrize("key", ["s", "S"])
@pytest.mark.parametrize("typed, passed", [
    ("  focus on  tests  ", "focus on  tests"),     # outer blanks removed, inner ones kept (#85)
    ("   ", ""),                                    # blanks only: none, as today
    ("", ""),
])
async def test_s_and_S_ask_for_instructions_and_pass_them_as_typed(key, typed, passed):
    # The box opens; Enter passes its text to the starter (#85).
    starts = []
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display and box.value == "" and app.focused is box
        box.value = typed
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 4, passed)]
        assert not box.display


@pytest.mark.parametrize("key", ["s", "S"])
async def test_esc_in_the_instructions_box_opens_nothing(key):
    starts = []
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        assert app.query_one("#find", Input).display
        await pilot.press("f", "o", "c", "u", "s", "escape")
        await settle(app, pilot)
        assert starts == [] and not app.query_one("#find", Input).display


async def test_highlighting_a_session_shows_its_own_run_in_every_pane():
    # Each row its own label, and its own run's lines, steps and sessions (#85).
    app = sessions_app(SessionWindows({}))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        rows = children(app, "6")
        assert len(rows) == 3 and len({row_text(app, k) for k in rows}) == 3
        for row in rows:
            await highlight(app, pilot, row)
            assert shown_run(app) is not None, row_text(app, row)
            run = next(r for r in SESSIONS if r.name == shown_run(app))
            for label, value in (("run", run.name), ("branch", run.branch), ("worktree", run.checkout),
                                 ("records", run.records)):
                assert f"{label.ljust(10)}{value}" in info(app)
            assert run_pane_names(app) == [s.shown for s in run.steps]
            assert session_names(app) == [s.name for s in run.sessions]
        assert sorted(await runs_by_row(app, pilot, "6")) == [RUN_A.name, RUN_B.name, RUN_C.name]


async def test_a_session_keeps_its_run_and_step_when_status_py_lists_the_runs_in_another_order():
    # X3 lists B first; each row still shows its own run, and the run pane's cursor stays on its step (#85).
    given = [SESSIONS]
    app = sessions_app(SessionWindows({}), runs=lambda: given[0])
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        before = dict(zip(children(app, "6"), await runs_by_row(app, pilot, "6")))
        assert RUN_B.name in before.values()
        row_b = next(k for k, name in before.items() if name == RUN_B.name)
        await highlight(app, pilot, row_b)
        app.query_one("#run", DataTable).move_cursor(row=1)
        given[0] = REORDERED
        app.refresh_runs()
        await settle(app, pilot)
        assert cursor_key(app) == row_b and shown_run(app) == RUN_B.name
        assert app.query_one("#run", DataTable).cursor_row == 1
        assert dict(zip(children(app, "6"), await runs_by_row(app, pilot, "6"))) == before


async def test_moving_to_another_session_starts_its_run_and_sessions_panes_at_the_top():
    # A different run does not keep the previous run's cursor row (#85).
    app = sessions_app(SessionWindows({}))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        await highlight(app, pilot, by_run[RUN_B.name])
        app.query_one("#run", DataTable).move_cursor(row=2)
        app.query_one("#sessions", DataTable).move_cursor(row=1)
        await pilot.pause()
        await highlight(app, pilot, by_run[RUN_A.name])
        assert shown_run(app) == RUN_A.name
        assert app.query_one("#run", DataTable).cursor_row == 0
        assert app.query_one("#sessions", DataTable).cursor_row == 0


async def test_enter_on_a_session_row_goes_to_its_coordination_pane():
    # The pane opened for it before its run; its run's coordination surface after, the same pane for the
    # two runs of one pane; a pane that is gone is said on the status line (#85).
    starts, focused = [], []
    fail = []

    async def focuser(surface):
        if surface in fail:
            raise FocusError(f"can't find pane: {surface}")
        focused.append(surface)

    app = sessions_app(SessionWindows({}), starter=recording_starter(starts), focuser=focuser)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        for name, pane in ((RUN_A.name, "%1"), (RUN_B.name, "%1"), (RUN_C.name, "%2")):
            await highlight(app, pilot, by_run[name])
            await pilot.press("enter")
            await settle(app, pilot)
            assert focused[-1] == pane
        await highlight(app, pilot, "6")
        await pilot.press("S")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert cursor_key(app) in children(app, "6") and "%9" in row_text(app, cursor_key(app))
        await pilot.press("enter")
        await settle(app, pilot)
        assert focused == ["%1", "%1", "%2", "%9"]
        fail.append("%9")
        await pilot.press("enter")
        await settle(app, pilot)
        assert "can't find pane: %9" in status(app)


async def test_the_windows_of_an_issue_are_closed_each_on_its_own_wait():
    # Issue 6 is closed and has no run; @2's screen changes later than @1's, so it is closed later (#28, #85).
    windows = SessionWindows({6: WINDOWS_6}, {"@2": [f"{n}" for n in range(8)]})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], lookup=lookup_states({6: "closed"}, []),
                       idle=0.3, check=0.05)
    async with app.run_test(size=WIDE) as pilot:
        await wait(pilot, 0.5)
        assert windows.closed == ["@1"]
        await wait(pilot, 0.6)
        assert windows.closed == ["@1", "@2"]
        assert windows.closed_at["@2"] - windows.closed_at["@1"] >= 0.2


async def test_a_failed_close_of_one_window_leaves_the_other_closed():
    windows = SessionWindows({6: WINDOWS_6}, fail_close={"@1"})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], lookup=lookup_states({6: "closed"}, []),
                       idle=0.2, check=0.05)
    async with app.run_test(size=WIDE) as pilot:
        await wait(pilot, 0.6)
        assert windows.closed == ["@2"]
        assert "can't find window: @1" in status(app)


async def test_the_sessions_of_an_issue_are_found_again_after_a_restart_keyed_by_their_panes():
    # A new app reads the runs first, then the windows; each run's row keeps its key, and the windows
    # with no run (issue 4's two) are shown under their issue after the first window read (#85).
    windows = SessionWindows({6: WINDOWS_6, 4: [Window("@5", "autoflow-sandbox#4", ["%6"]),
                                                Window("@6", "autoflow-sandbox#4", ["%7"])]})
    app = sessions_app(windows)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        before = dict(zip(children(app, "6"), await runs_by_row(app, pilot, "6")))
        assert sorted(before.values()) == [RUN_A.name, RUN_B.name, RUN_C.name]
        await check(app, pilot)
        assert dict(zip(children(app, "6"), await runs_by_row(app, pilot, "6"))) == before
        under = children(app, "4")
        assert len(under) == 2
        assert "%6" in row_text(app, under[0]) and "%7" in row_text(app, under[1])


async def test_the_row_of_an_issue_with_several_sessions_shows_no_single_run():
    # Empty run and sessions panes, the repository and workspace with no run lines, and the count; an issue
    # with one session shows its run on its own row, as today (#85).
    app = sessions_app(SessionWindows({}), runs=SESSIONS + RECORDED_RUNS,
                       open_issues=[Issue(6, "six"), Issue(3, "three"), Issue(4, "four")])
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await highlight(app, pilot, "6")
        assert app.query_one("#run", DataTable).row_count == 0
        assert app.query_one("#sessions", DataTable).row_count == 0
        labels = info_labels(app)
        assert "repo" in labels and "workspace" in labels and "sessions  3" in info(app)
        assert not {"run", "branch", "worktree", "records"} & set(labels)
        assert "3 sessions" in status(app)
        await highlight(app, pilot, "3")
        assert children(app, "3") == []
        assert "run       autoflow-sandbox-3-sample" in info(app)
        assert app.query_one("#run", DataTable).row_count == len(RECORDED_RUNS[0].steps)


async def test_s_on_a_session_row_goes_to_its_pane_and_on_the_parent_row_says_so():
    # On a session row s goes to its pane; on the row of an issue with several sessions it says so (#85).
    starts, focused = [], []

    async def focuser(surface):
        focused.append(surface)

    app = sessions_app(SessionWindows({}), starter=recording_starter(starts), focuser=focuser)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        await highlight(app, pilot, by_run[RUN_C.name])
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%2"] and starts == [] and not app.query_one("#find", Input).display
        await highlight(app, pilot, "6")
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%2"] and starts == [] and not app.query_one("#find", Input).display
        assert "#6 has 3 sessions" in status(app)


async def test_s_goes_back_to_a_window_found_after_a_restart():
    # Issue 8's one window was opened before this app started (nothing in its own opens) (#85).
    starts, focused = [], []

    async def focuser(surface):
        focused.append(surface)

    app = sessions_app(starter=recording_starter(starts), focuser=focuser)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        await highlight(app, pilot, "8")
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%4"] and starts == [] and not app.query_one("#find", Input).display


async def test_s_reads_the_windows_again_before_opening_the_box():
    # Addition 3: a window the periodic check has not found yet is gone back to, and no box opens.
    starts, focused = [], []

    async def focuser(surface):
        focused.append(surface)

    windows = SessionWindows({6: WINDOWS_6})
    app = sessions_app(windows, starter=recording_starter(starts), focuser=focuser)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        windows.open[4] = [Window("@5", "autoflow-sandbox#4", ["%6"])]
        await highlight(app, pilot, "4")
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%6"] and starts == [] and not app.query_one("#find", Input).display


class HeldStarter:
    """A starter whose calls wait until the test releases them; the first returns pane %9, the next %10."""

    def __init__(self):
        self.calls, self.release = [], asyncio.Event()

    async def __call__(self, coordinator, repository, number, instructions):
        self.calls.append((repository, number, instructions))
        pane = f"%{8 + len(self.calls)}"
        await self.release.wait()
        return pane


async def test_s_during_an_open_of_the_issue_opens_no_box_and_starts_nothing():
    # The guard is set when the box is submitted, before the starter returns (#85).
    starter = HeldStarter()
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], starter=starter)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        await pilot.press("enter")
        await wait(pilot, 0.1)                       # the starter is held: settle would wait on it
        assert len(starter.calls) == 1
        await pilot.press("s")
        await wait(pilot, 0.1)
        assert not app.query_one("#find", Input).display
        assert "#4: opening the coordination session" in status(app)
        starter.release.set()
        await settle(app, pilot)
        assert len(starter.calls) == 1


class HeldFindWindows(SessionWindows):
    """SessionWindows whose reads, once `hold` is set, wait until the test releases them (#85)."""

    def __init__(self, windows):
        super().__init__(windows)
        self.hold, self.release = False, asyncio.Event()

    async def find(self, workspace):
        if self.hold:
            await self.release.wait()
        return await super().find(workspace)


async def test_s_whose_window_read_ends_during_an_S_open_opens_no_box_and_starts_nothing():
    # `s` reads the windows again before it decides; an S submitted during that read is opening a session whose
    # pane is not known yet, so when the read returns `s` opens no box and no second session (#85).
    starter, windows = HeldStarter(), HeldFindWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=starter)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        windows.hold = True
        await pilot.press("s")                       # its worker waits on the window read
        await wait(pilot, 0.1)
        await pilot.press("S")
        await wait(pilot, 0.1)
        await pilot.press("enter")                   # the S open: its starter is held
        await wait(pilot, 0.1)
        assert len(starter.calls) == 1 and app.opening
        windows.release.set()                        # the read of `s` returns while the open is in progress
        await wait(pilot, 0.2)
        assert not app.query_one("#find", Input).display
        assert "#4: opening the coordination session" in status(app)
        await pilot.press("enter")
        await wait(pilot, 0.1)
        assert len(starter.calls) == 1
        starter.release.set()
        await settle(app, pilot)
        assert len(starter.calls) == 1
        assert list(app.started.values()) == [["%9"]]


async def test_a_second_S_does_not_cancel_an_open_in_progress():
    # Both opens complete and both panes are sessions under the issue (#85).
    starter = HeldStarter()
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], starter=starter)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        for _ in range(2):
            await pilot.press("S")
            await wait(pilot, 0.1)                   # a held start: settle would wait on it
            await pilot.press("enter")
            await wait(pilot, 0.1)
        assert len(starter.calls) == 2
        starter.release.set()
        await settle(app, pilot)
        under = children(app, "4")
        assert len(under) == 2
        assert "%9" in row_text(app, under[0]) and "%10" in row_text(app, under[1])


def every_sessions_app(given, loader_calls=None, **kw):
    """Every workspace of EVERY, autoflow-sandbox listing issues 6 and 4; the runs are given[0] at each read."""
    async def loader(repository, limit):
        if loader_calls is not None:
            loader_calls.append(repository)
        if repository == "Munsik-Park/autoflow-sandbox":
            return [Issue(6, "six"), Issue(4, "four")]
        return []

    async def runs(coordinator):
        return list(given[0])

    async def places():
        return {}

    return every_app(loader=loader, run_loader=runs, windows=SessionWindows({}), placer=places, **kw)


async def test_a_second_run_of_an_issue_unfolds_its_workspace_and_moves_to_its_session():
    # Runs are compared by name, not by the issues that have one (#85).
    given = [[RUN_A]]
    app = every_sessions_app(given)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await highlight(app, pilot, "Munsik-Park/autoflow-sandbox")
        await pilot.press("enter")                                          # fold it; the cursor is on its row
        await settle(app, pilot)
        assert "Munsik-Park/autoflow-sandbox#6" not in keys(issues_table(app))
        given[0] = [RUN_A, RUN_C]
        app.refresh_runs()
        await settle(app, pilot)
        assert "Munsik-Park/autoflow-sandbox#6" in keys(issues_table(app))
        assert cursor_key(app) in children(app, "Munsik-Park/autoflow-sandbox#6")
        assert shown_run(app) == RUN_C.name


async def test_a_run_ending_while_another_of_its_issue_stays_reads_the_issues_again():
    # Runs are compared by name, not by the issues that have one (#85).
    given = [[RUN_A, RUN_C]]
    calls = []

    async def loader(repository):
        calls.append(repository)
        return [Issue(6, "six")]

    async def runs(coordinator):
        return list(given[0])

    app = IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs,
                 windows=SessionWindows({}), window_check_sec=100)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        read = len(calls)
        given[0] = [RUN_C]
        app.refresh_runs()
        await settle(app, pilot)
        assert len(calls) == read + 1


async def test_the_cursor_stays_on_a_new_session_when_its_window_is_found_and_its_run_prepared():
    # The key is the pane, from the open to the prepared run. Issue 6 has C's session already, so the
    # new one is the second and has a row of its own (#85).
    starts = []
    given = [[RUN_C]]
    windows = SessionWindows({})
    run = Run("autoflow-sandbox-6-2026-11-20-230000-dddddddd", [Step("analysis", "running")], issue="6",
              coordination="%9")
    app = sessions_app(windows, runs=lambda: given[0], open_issues=[Issue(6, "six"), Issue(4, "four")],
                       starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("S")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        row = cursor_key(app)
        assert row in children(app, "6") and "%9" in row_text(app, row)
        windows.open[6] = [Window("@9", "autoflow-sandbox#6", ["%9"])]
        await check(app, pilot)
        assert cursor_key(app) == row
        given[0] = [RUN_C, run]
        app.refresh_runs()
        await settle(app, pilot)
        assert cursor_key(app) == row
        assert run.name in info(app) and run_pane_names(app) == ["analysis"]


async def test_a_second_run_in_the_shown_sessions_pane_keeps_the_cursor_on_the_run_it_showed():
    # <pane> becomes <pane>/<run>; the IDE moves the cursor, and it is not taken for a key press (#85).
    given = [[RUN_A, RUN_C]]
    app = sessions_app(SessionWindows({}), runs=lambda: given[0])
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        await highlight(app, pilot, by_run[RUN_A.name])
        given[0] = [RUN_A, RUN_B, RUN_C]
        app.refresh_runs()
        await settle(app, pilot)
        assert cursor_key(app) in children(app, "6") and shown_run(app) == RUN_A.name
        assert run_pane_names(app) == [s.shown for s in RUN_A.steps]
        app.refresh_runs()
        await settle(app, pilot)
        assert shown_run(app) == RUN_A.name


async def test_the_cursor_goes_to_the_issue_when_its_session_is_gone():
    # The session's window and run are gone; the issue has one session left, shown on its own row (#85).
    given = [[RUN_A, RUN_C]]
    windows = SessionWindows({6: WINDOWS_6})
    app = sessions_app(windows, runs=lambda: given[0])
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await check(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        await highlight(app, pilot, by_run[RUN_C.name])
        windows.open[6] = [WINDOWS_6[0]]
        given[0] = [RUN_A]
        await check(app, pilot)
        app.refresh_runs()
        await settle(app, pilot)
        assert children(app, "6") == [] and cursor_key(app) == "6"
        assert shown_run(app) == RUN_A.name


async def test_an_issue_that_gains_a_second_session_moves_the_cursor_to_the_session_it_showed():
    # Addition 2: the cursor on the issue's own row, showing its one run, goes to that run's row.
    given = [[RUN_A]]
    app = sessions_app(SessionWindows({}), runs=lambda: given[0])
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await highlight(app, pilot, "6")
        assert shown_run(app) == RUN_A.name
        given[0] = [RUN_A, RUN_C]
        app.refresh_runs()
        await settle(app, pilot)
        assert cursor_key(app) in children(app, "6") and shown_run(app) == RUN_A.name
        app.refresh_runs()
        await settle(app, pilot)
        assert shown_run(app) == RUN_A.name


@pytest.mark.parametrize("every", [False, True])
async def test_every_footer_key_fits_at_124_columns_and_S_is_next_to_s(every):
    # One repository and every workspace; nothing checks app.py's Footer line otherwise (#85).
    app = every_app() if every else coordinated_app()
    async with app.run_test(size=(124, 36)) as pilot:
        await settle(app, pilot)
        drawn = list(app.query_one(Footer).children)
        palette = [k for k in drawn if getattr(k, "action", "") == "command_palette"]
        bindings = [k for k in drawn if k not in palette]
        assert len(palette) == 1
        spans = [(k.region.x, k.region.right) for k in bindings]
        assert all(right <= palette[0].region.x for _, right in spans)
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
        names = [k.key for k in bindings]
        assert names[names.index("s") + 1] == "S"
        # b turns browser control on and off, and is not shown, so every key still fits (#86).
        assert "b" in app.active_bindings and not app.active_bindings["b"].binding.show
        assert "b" not in names


async def test_session_rows_follow_folding_and_a():
    given = [SESSIONS]
    app = every_sessions_app(given)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        issue = "Munsik-Park/autoflow-sandbox#6"
        assert len(children(app, issue)) == 3
        await highlight(app, pilot, "Munsik-Park/autoflow-sandbox")
        await pilot.press("enter")                                          # fold
        await settle(app, pilot)
        assert issue not in keys(issues_table(app))
        assert children(app, "Munsik-Park/autoflow-sandbox") == []
        await pilot.press("a")                                              # only the issues with a run
        await settle(app, pilot)
        assert len(children(app, issue)) == 3


async def test_the_step_detail_and_its_cause_are_those_of_the_highlighted_sessions_run():
    # The step detail of #83 per session (#85): the two runs of pane %1 each open their own steps.
    app = sessions_app(SessionWindows({}))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        for name, index, cause, other in ((RUN_B.name, 1, 0, RUN_A.name), (RUN_A.name, 2, 1, RUN_B.name)):
            await highlight(app, pilot, by_run[name])
            await pilot.press("2")
            app.query_one("#run", DataTable).move_cursor(row=index)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, StepDetail)
            assert app.screen.query_one("#detail").border_title == f"Step {index + 1}  ↺ review-2 ← review-1"
            text = detail_text(app)
            assert f"{name}/review-2" in text and other not in text
            await pilot.press("c")
            await pilot.pause()
            assert not isinstance(app.screen, StepDetail)
            assert app.query_one("#run", DataTable).cursor_row == cause
            await pilot.press("1")


# The instructions box: what reaches it and what it passes (#85)

async def test_the_instructions_box_passes_no_control_characters():
    # A paste keeps the control characters of its first line; they are dropped, then the blanks (#85).
    starts = []
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("S")
        await settle(app, pilot)
        app.query_one("#find", Input).value = "\x15 touch x \x1b[2J#"
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 4, "touch x [2J#")]


async def test_hash_while_an_instructions_box_is_open_leaves_that_box_and_its_enter_to_its_issue():
    # The box is bound to the purpose and the issue its opener set: `#` with the focus off the box opens no find
    # and says why; the box keeps its title and text, and its Enter starts the session it was opened for (#85).
    starts, asked = [], []

    async def lookup(repository, number):
        if number == 4:
            return Issue(4, "four")                                 # the state read at Enter, not a find (#85)
        asked.append(number)
        return Issue(number, "an old one", state="closed")

    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")], lookup=lookup,
                       starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("S")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        box.value = "tests first"
        await pilot.press("tab")                                    # focus leaves the box, which stays shown
        await pilot.press("number_sign")
        await settle(app, pilot)
        assert "find: the box is open for the instructions of #4" in status(app)
        assert box.display and box.border_title == "Instructions for #4" and box.value == "tests first"
        box.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 4, "tests first")] and asked == []


async def test_w_while_an_instructions_box_is_open_leaves_that_box_and_its_enter_to_its_issue():
    # As with `#`, for `w` (#85).
    starts, created = [], []

    async def creator(coordinator, address):
        created.append(address)
        return "%20"

    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")],
                       starter=recording_starter(starts), workspace_creator=creator)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("S")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        box.value = "tests first"
        await pilot.press("tab", "w")
        await settle(app, pilot)
        assert "add workspace: the box is open for the instructions of #4" in status(app)
        assert box.border_title == "Instructions for #4" and box.value == "tests first"
        box.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [("Munsik-Park/autoflow-sandbox", 4, "tests first")] and created == []


@pytest.mark.parametrize("opener", ["number_sign", "w"])
async def test_S_while_a_find_or_workspace_box_is_open_leaves_that_box_alone(opener):
    # The other way round: S opens no instructions box over a find's or a new workspace's (#85).
    starts, created = [], []

    async def creator(coordinator, address):
        created.append(address)
        return "%20"

    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")],
                       starter=recording_starter(starts), workspace_creator=creator)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press(opener)
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        title, placeholder = box.border_title, box.placeholder
        box.value = "4" if opener == "number_sign" else "Munsik-Park/other"
        await pilot.press("tab", "S")
        await settle(app, pilot)
        assert "S: the box is open for" in status(app)
        assert (box.border_title, box.placeholder) == (title, placeholder) and app.instructing is None
        box.focus()
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == []
        assert created == ([] if opener == "number_sign" else ["Munsik-Park/other"])


def closing_list(issues):
    """The open issues as a function of the list read: `issues[0]` at each read, which a test changes."""
    return lambda: list(issues[0])


@pytest.mark.parametrize("key", ["s", "S"])
async def test_an_issue_that_leaves_the_open_list_while_its_box_is_open_gets_no_session(key):
    # The issue closes while the instructions are typed and the next read of the list drops it: on Enter the
    # checks of s and S are made again, nothing opens, the box closes and the status line says why (#85).
    starts, listed = [], [[Issue(4, "four"), Issue(8, "eight")]]
    app = sessions_app(SessionWindows({}), runs=[], open_issues=closing_list(listed), starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display and box.border_title == "Instructions for #4"
        box.value = "go"
        listed[0] = [Issue(8, "eight")]
        app.action_refresh_issues()
        await settle(app, pilot)
        assert box.display and box.value == "go"                   # the read leaves the box as it is
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not box.display
        assert "#4 is no longer listed" in status(app)


async def test_an_issue_with_runs_that_closes_while_its_S_box_is_open_gets_no_session():
    # Issue 6 has runs, so its row stays when it leaves the open list, marked closed: Enter in its S box says it is
    # closed and opens nothing (#85).
    starts, listed = [], [[Issue(6, "six"), Issue(4, "four")]]
    app = sessions_app(SessionWindows({}), open_issues=closing_list(listed), lookup=lookup_states({6: "closed"}, []),
                       starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await highlight(app, pilot, "6")
        await pilot.press("S")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display and box.border_title == "Instructions for #6"
        listed[0] = [Issue(4, "four")]
        app.action_refresh_issues()
        await settle(app, pilot)
        await settle(app, pilot)                                    # the closed row's lookup
        assert app.find_issue(("Munsik-Park/autoflow-sandbox", 6)).closed
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not box.display and "#6 is closed" in status(app)


async def test_a_window_found_while_the_s_box_is_open_is_gone_back_to_by_s_not_doubled_by_enter():
    # `s` opens a box only for an issue with no session; one found while the box is open (a window opened from
    # elsewhere) makes Enter open nothing and say so, as `s` would (#85).
    starts = []
    windows = SessionWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        windows.open[4] = [Window("@5", "autoflow-sandbox#4", ["%6"])]
        await check(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not app.query_one("#find", Input).display
        assert "#4 has a session: s goes to it, or S for another" in status(app)


async def test_S_during_the_window_read_of_s_keeps_its_box_for_its_own_issue():
    # `s` on issue 4 waits on its window read; S on issue 8 opens the box meanwhile and text is typed. When the
    # read returns, `s` leaves that box alone, and its Enter starts issue 8's session, not 4's (#85).
    starter, windows = HeldStarter(), HeldFindWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four"), Issue(8, "eight")], starter=starter)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        windows.hold = True
        await pilot.press("s")                                     # issue 4; its worker waits on the read
        await wait(pilot, 0.1)
        await highlight_now(app, pilot, "8")
        await pilot.press("S")
        await wait(pilot, 0.1)
        box = app.query_one("#find", Input)
        box.value = "for eight"
        windows.release.set()
        await wait(pilot, 0.2)
        assert box.display and box.border_title == "Instructions for #8" and box.value == "for eight"
        assert "s: the box is open for the instructions of #8" in status(app)
        await pilot.press("enter")
        await wait(pilot, 0.1)
        starter.release.set()
        await settle(app, pilot)
        assert starter.calls == [("Munsik-Park/autoflow-sandbox", 8, "for eight")]


@pytest.mark.parametrize("opener", ["number_sign", "w"])
async def test_a_find_or_workspace_box_opened_during_the_window_read_of_s_is_left_to_its_opener(opener):
    # `#` or `w` during the read of `s`: when the read returns, the box keeps its purpose and text, and its Enter
    # finds or adds, starting no session (#85).
    starts, created, asked = [], [], []

    async def creator(coordinator, address):
        created.append(address)
        return "%20"

    async def lookup(repository, number):
        asked.append(number)
        return Issue(number, "an old one", state="closed")

    windows = HeldFindWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], lookup=lookup,
                       starter=recording_starter(starts), workspace_creator=creator)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        windows.hold = True
        await pilot.press("s")
        await wait(pilot, 0.1)
        await pilot.press(opener)
        await wait(pilot, 0.1)
        box = app.query_one("#find", Input)
        title, placeholder = box.border_title, box.placeholder
        typed = "12" if opener == "number_sign" else "Munsik-Park/other"
        box.value = typed
        windows.release.set()
        await wait(pilot, 0.2)
        assert box.display and (box.border_title, box.placeholder, box.value) == (title, placeholder, typed)
        assert app.instructing is None
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == []
        assert (asked, created) == (([12], []) if opener == "number_sign" else ([], ["Munsik-Park/other"]))


async def test_s_on_a_session_row_of_a_closed_issue_goes_to_the_session():
    # Issue 6 left the open list closed and still has its runs; `s` on a session's row goes there, as Enter does,
    # and on the issue's row it still says the issue is closed (#85).
    starts, focused = [], []

    async def focuser(surface):
        focused.append(surface)

    app = sessions_app(SessionWindows({}), open_issues=[Issue(4, "four")], lookup=lookup_states({6: "closed"}, []),
                       starter=recording_starter(starts), focuser=focuser)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        by_run = dict(zip(await runs_by_row(app, pilot, "6"), children(app, "6")))
        await highlight(app, pilot, by_run[RUN_C.name])
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%2"] and starts == []
        await highlight(app, pilot, "6")
        await pilot.press("s")
        await settle(app, pilot)
        assert focused == ["%2"] and "#6 is closed" in status(app)


# The open decided at Enter from the state read then, failing closed (#85)

class HeldLookup:
    """A lookup of the issue's state whose reads wait until the test releases them, then give `answer`: a state,
    "pull" for a pull request, or an IssueSourceError raised (#85)."""

    def __init__(self, answer):
        self.answer, self.asked, self.release = answer, [], asyncio.Event()

    async def __call__(self, repository, number):
        self.asked.append(number)
        await self.release.wait()
        if isinstance(self.answer, IssueSourceError):
            raise self.answer
        if self.answer == "pull":
            return Issue(number, f"issue {number}", state="open", pull=True)
        return Issue(number, f"issue {number}", state=self.answer)


@pytest.mark.parametrize("answer, said", [
    ("closed", "#6 is closed"),
    ("pull", "#6 is a pull request"),
    (IssueSourceError("HTTP 502"), "#6: nothing opened: its state could not be read: HTTP 502"),
    ("open", None),
])
async def test_an_issue_with_runs_whose_state_is_unknown_at_enter_opens_only_when_github_says_it_is_open(answer, said):
    # Issue 6 has runs and leaves the open list while its S box is open; its row's state is unknown while it is
    # looked up. Enter reads the state from GitHub before deciding, and opens only an issue it finds open (#85).
    starts, listed, lookup = [], [[Issue(6, "six"), Issue(4, "four")]], HeldLookup(answer)
    app = sessions_app(SessionWindows({}), open_issues=closing_list(listed), lookup=lookup,
                       starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await highlight(app, pilot, "6")
        await pilot.press("S")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display and box.border_title == "Instructions for #6"
        listed[0] = [Issue(4, "four")]
        app.action_refresh_issues()
        await wait(pilot, 0.2)
        assert app.find_issue(("Munsik-Park/autoflow-sandbox", 6)).state is None    # looked up, held
        await pilot.press("enter")
        await wait(pilot, 0.2)
        assert starts == [] and not box.display
        lookup.release.set()
        await settle(app, pilot)
        if said is None:
            assert starts == [("Munsik-Park/autoflow-sandbox", 6, "")]
        else:
            assert starts == [] and said in status(app)


async def test_a_window_opened_by_hand_while_the_s_box_is_open_is_found_at_enter_and_gets_no_second_session():
    # A `<workspace>#<issue>` window opened outside the IDE while the box is open, before any window check:
    # Enter reads the issue's windows again, opens nothing and says the issue has a session (#85).
    starts = []
    windows = SessionWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display
        windows.open[4] = [Window("@5", "autoflow-sandbox#4", ["%6"])]   # no check runs (check=100)
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not box.display
        assert "#4 has a session: s goes to it, or S for another" in status(app)


class FailingWindows(SessionWindows):
    """SessionWindows whose listing fails once `fail` is set, as tmux does with no server (#85)."""

    def __init__(self, windows):
        super().__init__(windows)
        self.fail = False

    async def find(self, workspace):
        if self.fail:
            raise WindowError("no server running on /tmp/tmux-501/default")
        return await super().find(workspace)


async def test_s_opens_nothing_when_the_windows_cannot_be_read_before_the_box_or_at_enter():
    # The issue's windows are what tells `s` it has no session; when tmux cannot list them, `s` opens neither the
    # box nor, at Enter, a session, and the status line says why (#85).
    starts = []
    windows = FailingWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        windows.fail = True
        await pilot.press("s")
        await settle(app, pilot)
        assert not box.display
        assert "#4: nothing opened: its windows could not be read: no server running" in status(app)
        windows.fail = False
        await pilot.press("s")
        await settle(app, pilot)
        assert box.display
        windows.fail = True
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not box.display
        assert "#4: nothing opened: its windows could not be read: no server running" in status(app)


RUN_4 = Run("autoflow-sandbox-4-2026-11-21-100000-dddddddd", issue="4", branch="issue-4-2026-11-21-100000-dddddddd")


@pytest.mark.parametrize("then, said", [
    ([RUN_4], "#4 already has a run: autoflow-sandbox-4-2026-11-21-100000-dddddddd"),
    (RunSourceError("status.py exited with status 1"), "#4: nothing opened: the runs could not be read"),
])
async def test_s_at_enter_reads_the_runs_again_and_opens_nothing_for_a_run_or_runs_it_cannot_read(then, said):
    # A run of issue 4 listed by status.py since the last read, or a read that fails: Enter in the `s` box opens
    # nothing and says why (#85).
    starts, runs = [], [[]]

    def read_runs():
        if isinstance(runs[0], Exception):
            raise runs[0]
        return list(runs[0])

    app = sessions_app(SessionWindows({}), runs=read_runs, open_issues=[Issue(4, "four")],
                       starter=recording_starter(starts), run_refresh_sec=100)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display
        runs[0] = then
        await pilot.press("enter")
        await settle(app, pilot)
        assert starts == [] and not box.display and said in status(app)


async def test_an_open_started_during_the_enter_reads_of_s_keeps_s_from_opening_a_second_session():
    # Enter in the `s` box marks the open as in progress before it reads the windows again: `s` meanwhile opens no
    # box, and an S open submitted meanwhile makes the `s` open nothing when its reads return (#85).
    starter, windows = HeldStarter(), HeldFindWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=starter)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        box.value = "by s"
        windows.hold = True
        await pilot.press("enter")                   # its reads wait on the window listing
        await wait(pilot, 0.1)
        assert app.opening and not box.display
        await pilot.press("s")
        await wait(pilot, 0.1)
        assert not box.display and "#4: opening the coordination session" in status(app)
        await pilot.press("S")
        await wait(pilot, 0.1)
        box.value = "by S"
        await pilot.press("enter")                   # the S open: its starter is held
        await wait(pilot, 0.1)
        assert starter.calls == [("Munsik-Park/autoflow-sandbox", 4, "by S")]
        windows.release.set()                        # the reads of the `s` Enter return during the S open
        await wait(pilot, 0.2)
        assert "#4: opening the coordination session" in status(app)
        starter.release.set()
        await settle(app, pilot)
        assert starter.calls == [("Munsik-Park/autoflow-sandbox", 4, "by S")]
        assert list(app.started.values()) == [["%9"]] and not app.opening


@pytest.mark.parametrize("key", ["s", "S"])
async def test_an_issue_the_list_shows_open_but_github_says_closed_at_enter_gets_no_session(key):
    # The issue closed on GitHub while its box was open, before the next read of the list: its row still shows it
    # open, and the state read at Enter says closed, so nothing opens and the status line says why (#85).
    starts, asked = [], []
    states = {}
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")],
                       lookup=lookup_states(states, asked), starter=recording_starter(starts))
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display
        states[4] = "closed"
        assert app.find_issue(("Munsik-Park/autoflow-sandbox", 4)).state == "open"
        await pilot.press("enter")
        await settle(app, pilot)
        assert asked == [4]
        assert starts == [] and not box.display and "#4 is closed" in status(app)


class StaleFindWindows(SessionWindows):
    """SessionWindows whose next listing, once `hold` is set, is taken at once and returned only when the test
    releases it, as a periodic check that began before a window was opened ends after it (#85)."""

    def __init__(self, windows):
        super().__init__(windows)
        self.hold, self.release = False, asyncio.Event()

    async def find(self, workspace):
        found = await super().find(workspace)
        if self.hold:
            self.hold = False
            await self.release.wait()
        return found


async def test_a_window_check_that_began_before_a_window_opened_and_ends_during_enter_does_not_hide_it():
    # A periodic window check lists no window of issue 4; a window is opened by hand; Enter in the `s` box finds
    # it; the older check then ends while Enter reads the runs and shows no window again. The decision is made
    # from what Enter read, so nothing opens (#85).
    starts = []
    windows = StaleFindWindows({})
    app = sessions_app(windows, runs=[], open_issues=[Issue(4, "four")], starter=recording_starter(starts),
                       run_refresh_sec=100)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display
        windows.hold = True
        app.check_windows()                          # its listing has no window of issue 4, and is held
        await wait(pilot, 0.1)
        windows.open[4] = [Window("@5", "autoflow-sandbox#4", ["%6"])]

        async def read_runs(coordinator):
            windows.release.set()                    # the older check ends while Enter reads the runs
            await asyncio.sleep(0.2)
            return []

        app.run_loader = read_runs
        await pilot.press("enter")
        await settle(app, pilot)
        assert ("Munsik-Park/autoflow-sandbox", 4) not in app.windows_found    # the older check ended last
        assert starts == [] and not box.display
        assert "#4 has a session: s goes to it, or S for another" in status(app)


RUN_4_PANE = Run("autoflow-sandbox-4-2026-11-21-100000-dddddddd", issue="4",
                 branch="issue-4-2026-11-21-100000-dddddddd", coordination="%7")


async def test_a_run_read_that_began_before_a_run_started_and_ends_during_enter_does_not_hide_it():
    # A periodic read of the runs lists no run of issue 4; a run of it starts; Enter in the `s` box reads it; the
    # older read then ends while Enter reads where the panes are and shows no run again. The decision is made from
    # what Enter read, so nothing opens (#85).
    starts, release = [], asyncio.Event()
    app = sessions_app(SessionWindows({}), runs=[], open_issues=[Issue(4, "four")],
                       starter=recording_starter(starts), run_refresh_sec=100)
    async with app.run_test(size=WIDE) as pilot:
        await settle(app, pilot)
        await pilot.press("s")
        await settle(app, pilot)
        box = app.query_one("#find", Input)
        assert box.display
        reads = []

        async def read_runs(coordinator):
            reads.append(coordinator)
            if len(reads) == 1:                      # the periodic read: taken before the run started, held
                await release.wait()
                return []
            return [RUN_4_PANE]

        async def places():
            release.set()                            # the older read ends while Enter reads the pane places
            await asyncio.sleep(0.2)
            return {}

        app.run_loader, app.placer = read_runs, places
        app.refresh_runs()
        await wait(pilot, 0.1)
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(reads) == 2 and app.runs == []    # the older read ended last
        assert starts == [] and not box.display
        assert "#4 already has a run: autoflow-sandbox-4-2026-11-21-100000-dddddddd" in status(app)
