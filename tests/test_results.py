"""Reading what a module run left: the entries of its work folder, and from its end record whether the run has
ended and which entries were written after the end (#83). On the records of a run of autoflow-coordinator 0.8
(tests/fixtures/README.md), and on folders made here for the cases no recording has."""
import json
import os
import shutil
from pathlib import Path

from autoflow_ide import results
from autoflow_ide.results import OUTPUT_LIMIT, output_path, read_output

RUN = "autoflow-coordinator-119-2026-11-19-180359-3cbd8c18"
RECORDS = Path(__file__).parent / "fixtures" / "records" / RUN


def entries(work, sub=""):
    """(name, written after the end) of each entry the detail lists, in its order; `sub` a folder inside the work
    folder, as the detail opens it."""
    return [(e.name, e.after_end) for e in work.entries(sub)]


def copied(tmp_path, name="review-1"):
    """A copy of a recorded module run, its work folder and its end record beside it, at another place: the end
    record's paths are still those of the recorded records folder (#83)."""
    shutil.copytree(RECORDS / name, tmp_path / name)
    shutil.copyfile(RECORDS / f"{name}.ended.json", tmp_path / f"{name}.ended.json")
    return tmp_path / name


def test_a_review_run_of_the_0_8_coordinator():
    # Its end record lists the files under /home/claude/fx/records/…, which are this folder's (#83).
    work = results.read_work(str(RECORDS / "review-1"))
    assert (work.folder, work.ended, work.unreadable) == (True, True, [])
    assert entries(work) == [("report.md", False), ("reviews/", False)]
    assert entries(work, "reviews") == [("claude.md", False), ("codex.md", False)]
    assert [e.path for e in work.entries()] == [RECORDS / "review-1" / "report.md", RECORDS / "review-1" / "reviews"]


def test_a_file_written_after_the_end_is_marked_and_one_gone_is_not_shown(tmp_path):
    folder = copied(tmp_path)
    (folder / "late.md").write_text("written after the end record\n")
    (folder / "reviews" / "codex.md").unlink()
    work = results.read_work(str(folder))
    assert entries(work) == [("late.md", True), ("report.md", False), ("reviews/", False)]
    assert entries(work, "reviews") == [("claude.md", False)]


def test_a_link_to_a_folder_is_not_marked(tmp_path):
    # ended.py lists the files by os.walk, which does not go into a link to a folder (#83).
    folder = copied(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "a.txt").write_text("a\n")
    os.symlink(tmp_path / "elsewhere", folder / "linked")
    work = results.read_work(str(folder))
    assert entries(work) == [("linked/", False), ("report.md", False), ("reviews/", False)]


def test_a_folder_holding_a_file_written_after_the_end_is_marked(tmp_path):
    folder = copied(tmp_path)
    (folder / "reviews" / "late.md").write_text("a third reviewer, after the end\n")
    work = results.read_work(str(folder))
    assert entries(work) == [("report.md", False), ("reviews/", True)]
    assert entries(work, "reviews") == [("claude.md", False), ("codex.md", False), ("late.md", True)]


def test_entries_are_sorted_by_name_and_folders_marked(tmp_path):
    folder = tmp_path / "build-1"
    (folder / "a").mkdir(parents=True)
    (folder / "b.md").write_text("b\n")
    (folder / "C.txt").write_text("c\n")
    (folder / "a" / "x.json").write_text("{}")
    work = results.read_work(str(folder))
    assert [e.name for e in work.entries()] == ["C.txt", "a/", "b.md"]
    assert [e.name for e in work.entries("a")] == ["x.json"]


def test_a_run_that_has_not_ended_shows_its_entries_without_marks(tmp_path):
    folder = tmp_path / "build-1"
    folder.mkdir()
    (folder / "draft.md").write_text("so far\n")
    work = results.read_work(str(folder))
    assert (work.folder, work.ended, work.unreadable) == (True, False, [])
    assert entries(work) == [("draft.md", False)]


def test_an_end_record_that_cannot_be_read_is_named_and_the_entries_listed_without_marks(tmp_path):
    folder = tmp_path / "build-1"
    folder.mkdir()
    (folder / "draft.md").write_text("so far\n")
    for content in ("{not json", json.dumps({"status": "ended"}), json.dumps({"status": "ended", "outputs": {}}),
                    "[]"):
        (tmp_path / "build-1.ended.json").write_text(content)
        work = results.read_work(str(folder))
        assert [u.split(":")[0] for u in work.unreadable] == ["build-1.ended.json"], content
        assert entries(work) == [("draft.md", False)], content


def test_no_work_folder(tmp_path):
    work = results.read_work(str(tmp_path / "nothing"))
    assert (work.folder, work.ended) == (False, False)
    assert work.entries() == []


def test_an_output_outside_the_folder_is_where_it_is_written():
    assert output_path("/r/x", "/elsewhere/a.md") == Path("/elsewhere/a.md")
    assert output_path("/r/x", "reviews/claude.md") == Path("/r/x/reviews/claude.md")


def test_an_output_read_to_show_it(tmp_path):
    (tmp_path / "a.json").write_text('{"b": [1, 2]}')
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "big.md").write_text("x" * (OUTPUT_LIMIT + 10))
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "f.txt").write_text("")
    assert read_output(tmp_path / "a.json").text == json.dumps({"b": [1, 2]}, indent=2)
    assert (read_output(tmp_path / "bad.json").kind, read_output(tmp_path / "bad.json").text) == ("text", "{not json")
    big = read_output(tmp_path / "big.md")
    assert (big.kind, big.cut, len(big.text)) == ("markdown", True, OUTPUT_LIMIT)
    assert (read_output(tmp_path / "dir").kind, read_output(tmp_path / "dir").text) == ("folder", "f.txt")
    assert read_output(tmp_path / "gone.md").kind == "missing"
    report = read_output(RECORDS / "review-1" / "report.md")
    assert report.kind == "markdown" and report.text == (RECORDS / "review-1" / "report.md").read_text()
