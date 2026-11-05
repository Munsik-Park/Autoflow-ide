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
