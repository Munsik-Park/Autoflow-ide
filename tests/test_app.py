from textual.widgets import DataTable, Static

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
    await app.workers.wait_for_complete()
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


def starting_app(starter, focuser=None):
    async def loader(repository):
        return [Issue(4, "four"), Issue(3, "three")]

    async def runs(coordinator):
        return RECORDED_RUNS      # issue 3 has a run

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs,
                  starter=starter, focuser=focuser)


async def test_s_starts_a_run_for_the_issue_and_goes_back_to_it_the_second_time():
    starts, focused = [], []

    async def starter(coordinator, repository, number):
        starts.append((coordinator, repository, number))
        return "%7"

    async def focuser(surface):
        focused.append(surface)

    app = starting_app(starter, focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down", "s")
        await settle(app, pilot)
        assert starts == [("/c", "Munsik-Park/autoflow-sandbox", 4)]
        assert "#4: coordination session in %7" in status(app)
        await pilot.press("s")
        await settle(app, pilot)
        assert starts == [("/c", "Munsik-Park/autoflow-sandbox", 4)] and focused == ["%7"]


async def test_s_on_an_issue_with_a_run_or_a_failure_says_why():
    async def starter(coordinator, repository, number):
        raise StartError("no workspace for Munsik-Park/autoflow-sandbox in /base")

    app = starting_app(starter)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("s")                     # #3 has a run
        await settle(app, pilot)
        assert "#3 already has a run: autoflow-sandbox-3-sample" in status(app)
        await pilot.press("down", "s")
        await settle(app, pilot)
        assert "start #4: no workspace for Munsik-Park/autoflow-sandbox in /base" in status(app)


async def test_s_without_a_coordinator():
    async def starter(coordinator, repository, number):
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
RECORDS = FIXTURES / "records" / "autoflow-sandbox-6-results"


def test_describe_a_running_step():
    rows = dict(describe(Step("build-1", "running", session="S2", module="build", session_state="working",
                              idle_sec=42.4, out="/r/build-1")))
    assert rows["Session"] == "S2 (working, idle 42s)" and rows["Verdict"] == "-" and rows["Result"] == "-"
    assert rows["Folder"] == "/r/build-1"


def run_pane_names(app):
    table = app.query_one("#run", DataTable)
    return [str(table.get_row_at(n)[1]) for n in range(table.row_count)]


def results_app(steps):
    async def loader(repository):
        return [Issue(6, "six")]

    async def runs(coordinator):
        return [Run("autoflow-sandbox-6-results", steps, issue="6")]

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=runs)


def detail_text(app):
    """The detail's text: its fields and section titles, the evaluation, and the source of each Markdown part."""
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
    steps = [Step("review-1", "fail", "goal missed", "S3", "review", out=str(RECORDS / "review-1"), result="accepted"),
             Step("build-2", "pass", "pass", "S2", "build", "return", "review-1", out=str(RECORDS / "build-2"),
                  result="accepted")]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "down", "enter")                       # build-2
        await pilot.pause()
        assert isinstance(app.screen, StepDetail)
        assert app.screen.query_one("#detail").border_title == "Step 2  ↩ build-2 ← review-1"
        text = detail_text(app)
        assert "Result   accepted" in text and "Outputs" in text and "• commits.json" in text
        assert "Lacked" in text and "A Windows environment" in text
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, StepDetail)
        assert app.query_one("#run", DataTable).cursor_row == 0           # review-1
        await pilot.press("enter")
        await pilot.pause()
        text = detail_text(app)
        assert "Review findings (review-findings.md)" in text and "### F1 — Medium — holds" in text
        assert "Outputs" not in text                                      # the findings are the result
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, StepDetail)


async def test_the_detail_shows_an_evaluation_a_refusal_and_a_failure():
    steps = [Step("plan-1", "pass", "pass", "S1", "plan", out=str(RECORDS / "plan-1"), result="accepted"),
             Step("review-2", "awaiting", "awaiting verdict", "S3", "review", "rerun", "review-1",
                  out=str(RECORDS / "review-2"), result="refused"),
             Step("review-3", "fail", "goal not met", "S3", "review", "rerun", "review-1",
                  out=str(RECORDS / "review-3"), result="failed")]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "Scores" in text and "Decision grounds  9" in text and "Recommendations (7)" in text
        assert "Low · Decision grounds · feature-design.md > Decisions > D4 (Holds under)" in text
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "Result   refused" in text and "Defects" in text and "param 'pr' does not match" in text
        await pilot.press("escape", "down", "enter")
        await pilot.pause()
        text = detail_text(app)
        assert "Result   failed" in text and "Why it did not reach its goal (failure.md)" in text
        assert app.screen.query_one("#detail-body Markdown").source.startswith("# ")


async def test_the_detail_of_a_step_with_no_result():
    steps = [Step("CI check (gh)", "pass", "pass", "X1"),
             Step("review-4", "running", None, None, "review", "rerun", "review-1", out=str(RECORDS / "review-4")),
             Step("review-5", "running", None, None, "review", out="/no/such/review-5")]
    app = results_app(steps)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await pilot.press("2")
        for row, expected in enumerate(("No module run", "No result yet", "not there yet")):
            await pilot.press(*(["escape", "down"] if row else []), "enter")
            await pilot.pause()
            assert expected in detail_text(app)


async def test_the_detail_scrolls():
    steps = [Step("review-1", "fail", "goal missed", "S3", "review", out=str(RECORDS / "review-1"), result="accepted")]
    app = results_app(steps)
    async with app.run_test(size=(100, 30)) as pilot:
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
    """Windows by issue; `screens` gives each issue's screens in turn, then the last one."""

    def __init__(self, numbers, screens=None):
        self.open = {n: Window(f"@{n}", f"autoflow-sandbox#{n}", [f"%{n}"]) for n in numbers}
        self.screens = screens or {}
        self.reads = {}
        self.closed = []

    async def find(self, workspace):
        assert workspace == "autoflow-sandbox"
        return dict(self.open)

    async def screen(self, window):
        number = int(window.name.split("#")[1])
        self.reads[number] = self.reads.get(number, 0) + 1
        shown = self.screens.get(number, ["> "])
        return shown[min(self.reads[number], len(shown)) - 1]

    async def close(self, window):
        number = int(window.name.split("#")[1])
        del self.open[number]
        self.closed.append(window.name)


def windows_app(windows, open_issues, runs, lookup, idle=0.2):
    async def loader(repository):
        return open_issues

    async def run_loader(coordinator):
        return runs

    async def workspace_finder(coordinator, repository):
        return "autoflow-sandbox"

    return IdeApp(repository="Munsik-Park/autoflow-sandbox", loader=loader, coordinator="/c", run_loader=run_loader,
                  lookup=lookup, windows=windows, workspace_finder=workspace_finder,
                  window_check_sec=0.05, window_idle_sec=idle)


def lookup_states(states, asked):
    async def lookup(repository, number):
        asked.append(number)
        return Issue(number, f"issue {number}", state=states[number])
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
        assert asked == [8]
        assert "closed autoflow-sandbox#8: #8 is closed and has no run" in status(app)


async def test_keeps_the_window_of_a_closed_issue_while_it_has_a_run():
    asked = []
    windows = FakeWindows([3])
    app = windows_app(windows, [Issue(4, "four")], RECORDED_RUNS, lookup_states({3: "closed"}, asked))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert windows.closed == []


async def test_keeps_the_window_of_an_open_issue():
    asked = []
    windows = FakeWindows([4, 9])
    # 4 is in the open list; 9 is not (more open issues than listed), and GitHub says it is open.
    app = windows_app(windows, [Issue(4, "four")], [], lookup_states({9: "open"}, asked))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.pause(0.5)
        await settle(app, pilot)
        assert windows.closed == []
        assert 4 not in asked
        assert 1 <= asked.count(9) <= 2            # asked again only after window_idle_sec


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


async def test_the_detail_shows_the_row_cells_above_the_output_folder():
    steps = [Step("F1 fix (outside AutoFlow)", "pass", "pass", "X1", verdict_text="pass, [head] 253901c pushed",
                  notes="x1-2-instruction.txt"),
             Step("review-1", "fail", "goal missed", "S3", "review", out=str(RECORDS / "review-1"), result="accepted",
                  verdict_text="goal missed: F1 holds (Medium)", notes="F1 fixed by X1, then re-review")]
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
        assert text.index("F1 fixed by X1") < text.index("Review findings (review-findings.md)")


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
              **kw):
    """Every workspace of EVERY; `recent` are the workspaces chosen recently (all of them: all unfolded)."""
    if state_file is None:
        state_file = Path(tempfile.mkdtemp()) / "state.json"
        state_file.write_text(json.dumps({"recent": list(recent)}))
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

    return IdeApp(coordinator="/c", loader=loader, run_loader=run_loader or given_runs, workspaces_reader=workspaces,
                  state_file=state_file, **kw)


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


async def test_s_starts_the_issue_of_its_own_repository():
    started = []

    async def starter(coordinator, repository, number):
        started.append((repository, number))
        return "%9"

    app = every_app(starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("s")
        await settle(app, pilot)
        assert started == [("Munsik-Park/autoflow-sandbox", 4)]
        assert "autoflow-sandbox#4: coordination session in %9" in status(app)


class EveryWindows(FakeWindows):
    def __init__(self, windows):
        super().__init__([])
        self.open = windows

    async def find(self, workspace):
        return {n: w for (ws, n), w in self.open.items() if ws == workspace}

    async def screen(self, window):
        return "still"

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

    async def starter(coordinator, repository, number):
        return "%9"

    app = every_app(state_file=state, starter=starter)
    async with app.run_test(size=(121, 37)) as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        issues.move_cursor(row=keys(issues).index("Munsik-Park/autoflow-sandbox#4"))
        await pilot.press("s")
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
        assert app.query_one("#issues-pane").border_title == "Issues · running"
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
        assert app.query_one("#issues-pane").border_title == "Issues"
        assert "Munsik-Park/gone" in keys(issues) and "Munsik-Park/autoflow-coordinator#67" in keys(issues)


async def test_a_with_one_repository():
    app = coordinated_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert [str(issues.get_row_at(i)[1]) for i in range(issues.row_count)] == ["3"]
