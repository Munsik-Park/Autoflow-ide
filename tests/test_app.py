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

    return IdeApp(repository="owner/name", loader=loader, coordinator="/coordinator",
                  run_loader=run_loader or runs, focuser=focuser, **kw)


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_runs_attach_to_their_issue():
    app = coordinated_app()
    async with app.run_test() as pilot:
        await settle(app, pilot)
        issues = app.query_one("#issues", DataTable)
        assert issues.get_cell("3", "title") == "three  (run)"
        assert issues.get_cell("4", "title") == "four"
        assert app.query_one("#run", DataTable).row_count == 0
        await pilot.press("down")
        await pilot.pause()
        assert app.query_one("#run", DataTable).row_count == 3
        assert app.query_one("#sessions", DataTable).row_count == 2
        assert "issue-3-sample" in status(app) and "1 active runs" in status(app)


async def test_enter_on_a_session_focuses_its_pane():
    focused = []

    async def focuser(surface):
        focused.append(surface)

    app = coordinated_app(focuser=focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down", "3", "down", "enter")
        await settle(app, pilot)
        assert focused == ["%2"]


async def test_focus_failure_shows_on_the_status_line():
    async def focuser(surface):
        raise FocusError("can't find pane: %1")

    app = coordinated_app(focuser=focuser)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down", "3", "enter")
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


async def test_refresh_keeps_the_issue_cursor():
    app = coordinated_app(run_refresh_sec=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        await pilot.pause(0.2)
        await settle(app, pilot)
        assert app.query_one("#issues", DataTable).cursor_row == 1
        assert app.current.number == 3
