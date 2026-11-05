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
        assert str(issues.get_cell("3", "title")).startswith("(closed) word_count")
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
        assert str(issues.get_cell("3", "title")).startswith("(closed) word_count")


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
