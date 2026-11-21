"""Claude Code's session records, as browser control reads them (#86): where a session's transcript is, and the
end of each file, cut to whole records.

They run on records recorded from Claude Code and on the coordinator's recorded sessions.tsv and config.py output,
so they are what breaks when Claude Code changes where or how it writes a session's records, or the coordinator
changes the sessions.tsv it writes. See tests/fixtures/README.md for where each fixture comes from.
"""
import builtins
import json
import os
import shutil
from pathlib import Path

import pytest

from autoflow_ide.runs import parse_status
from autoflow_ide.transcripts import (LIMIT, TranscriptUnknown, coordination_folder, project_folder, read_tail,
                                      runs_folder, transcript_files, work_session_id, work_transcripts)

FIXTURES = Path(__file__).parent / "fixtures"
TRANSCRIPTS = FIXTURES / "transcripts"
SID = "00000000-0000-4000-8000-000000000001"
MAIN = TRANSCRIPTS / f"{SID}.jsonl"
SUBAGENT = TRANSCRIPTS / SID / "subagents" / "agent-a0000000000000001.jsonl"
RUN = "autoflow-coordinator-119-2026-11-19-180359-3cbd8c18"
KB = 1024


def recorded():
    """The recorded records, each a line with its line feed."""
    return [line + b"\n" for path in (MAIN, SUBAGENT) for line in path.read_bytes().splitlines()]


def records(size):
    """Recorded records, in turn, until they make at least `size` bytes."""
    lines, out = recorded(), b""
    n = 0
    while len(out) < size:
        out += lines[n % len(lines)]
        n += 1
    return out


def record_of(size, end=b"\n"):
    """One record of exactly `size` bytes, its line feed included unless `end` is empty."""
    head = b'{"type":"pad","text":"'
    tail = b'"}' + end
    return head + b"x" * (size - len(head) - len(tail)) + tail


def exactly(size):
    """Whole recorded records, then one record that makes them exactly `size` bytes."""
    body = records(size - 4 * KB)
    while len(body) > size - 200:
        body = body[:body.rstrip(b"\n").rfind(b"\n") + 1]
    return body + record_of(size - len(body))


def written(tmp_path, data, name="t.jsonl"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def whole_json(data):
    for line in data.splitlines():
        json.loads(line)
    return True


def test_the_limit_is_the_ides_output_limit():
    assert LIMIT == 512 * KB


def test_a_boundary_at_the_start_of_a_record_keeps_that_record(tmp_path):
    # The byte at size - 512 KB starts a record: that record is the first sent (#86).
    before = records(3 * KB)
    window = exactly(LIMIT)
    tail = read_tail(written(tmp_path, before + window))
    assert tail.data == window and whole_json(tail.data)
    assert tail.notes == [f"{len(before)} bytes before this were left out"]


def test_a_boundary_inside_a_record_starts_at_the_next_record(tmp_path):
    before = records(3 * KB)
    cut = record_of(3000)
    after = exactly(LIMIT - 1000)          # the window starts 1000 bytes before the end of `cut` (#86)
    tail = read_tail(written(tmp_path, before + cut + after))
    assert tail.data == after and whole_json(tail.data)
    assert tail.notes == [f"{len(before) + len(cut)} bytes before this were left out"]


def test_a_record_being_written_is_not_sent_and_its_bytes_are_told(tmp_path):
    before = records(3 * KB)
    partial = record_of(500, end=b"")[:-1]          # no line feed yet (#86)
    window = exactly(LIMIT - len(partial))
    tail = read_tail(written(tmp_path, before + window + partial))
    assert tail.data == window and whole_json(tail.data)
    assert tail.notes == [f"{len(before)} bytes before this were left out",
                          f"{len(partial)} bytes of a record being written not shown"]


def test_a_last_record_over_the_limit_sends_nothing_and_says_so(tmp_path):
    data = records(3 * KB) + record_of(LIMIT + 100)
    tail = read_tail(written(tmp_path, data))
    assert tail.data == b""
    assert tail.notes == [f"the last record is over 512 KB: no record shown ({len(data)} bytes in the file)"]


def test_a_short_record_then_one_being_written_that_fills_the_window(tmp_path):
    # Not one record over 512 KB: what is there is a record being written, and what was left out before it (#86).
    before = records(3 * KB)
    short = record_of(200)
    partial = record_of(LIMIT - 100 + 1, end=b"")[:-1]      # LIMIT - 100 bytes, unterminated (#86)
    data = before + short + partial
    tail = read_tail(written(tmp_path, data))
    assert tail.data == b""
    assert tail.notes == [f"{len(before) + len(short)} bytes before this were left out",
                          f"{len(partial)} bytes of a record being written not shown"]
    assert not any("over 512 KB" in note for note in tail.notes)


def test_a_file_under_the_limit_is_sent_whole(tmp_path):
    data = records(20 * KB)
    tail = read_tail(written(tmp_path, data))
    assert tail.data == data and tail.notes == [] and whole_json(tail.data)
    assert read_tail(written(tmp_path, exactly(LIMIT), "exact.jsonl")).notes == []


def test_a_file_under_the_limit_leaves_out_only_a_record_being_written(tmp_path):
    data = records(20 * KB)
    tail = read_tail(written(tmp_path, data + b'{"type":"user"'))
    assert tail.data == data
    assert tail.notes == ["14 bytes of a record being written not shown"]


def test_the_recorded_files_are_sent_whole():
    for path in (MAIN, SUBAGENT):
        tail = read_tail(path)
        assert tail.data == path.read_bytes() and tail.notes == [] and whole_json(tail.data)


def test_each_file_is_cut_on_its_own(tmp_path):
    big = records(3 * KB) + exactly(LIMIT)
    small = records(5 * KB)
    main, sub = written(tmp_path, big, "main.jsonl"), written(tmp_path, small, "sub.jsonl")
    assert read_tail(sub).data == small and read_tail(sub).notes == []
    assert read_tail(main).notes == [f"{len(big) - LIMIT} bytes before this were left out"]
    assert read_tail(sub).data == small


# Where a session's transcript is (#86)

def test_the_project_folder_is_the_directory_with_each_other_character_as_a_dash(tmp_path):
    # As the coordinator's tools/session.py and launch.sh find it. (#86)
    assert project_folder("/home/claude/fx/base/autoflow-coordinator/x_y.z", tmp_path) == (
        tmp_path / ".claude" / "projects" / "-home-claude-fx-base-autoflow-coordinator-x-y-z")


def test_a_work_sessions_transcript_from_its_runs_sessions_tsv(tmp_path):
    assert runs_folder((FIXTURES / "config-default.txt").read_text()) == Path("/home/claude/fxhome/.autoflow/coordination/runs")
    assert work_session_id(FIXTURES / "runs" / RUN, "S2") == "00000000-0000-4000-8000-000000000003"
    assert work_session_id(FIXTURES / "runs" / RUN, "X1") == "00000000-0000-4000-8000-000000000004"
    assert work_session_id(FIXTURES / "runs" / RUN, "S9") is None
    [run] = parse_status((FIXTURES / "status-reviews.json").read_text())
    folder = project_folder(run.checkout, tmp_path)
    assert folder.name == "-home-claude-fx-base-autoflow-coordinator-autoflow-coordinator-wt-119-2026-11-19-180359-3cbd8c18"
    (folder / "00000000-0000-4000-8000-000000000003" / "subagents").mkdir(parents=True)
    main = folder / "00000000-0000-4000-8000-000000000003.jsonl"
    shutil.copy(MAIN, main)
    sub = folder / "00000000-0000-4000-8000-000000000003" / "subagents" / "agent-a0000000000000001.jsonl"
    shutil.copy(SUBAGENT, sub)
    assert work_transcripts(FIXTURES / "runs", run, "S2", tmp_path) == [main, sub]


def test_the_main_file_then_its_subagent_files(tmp_path):
    folder = tmp_path / "projects" / "-x"
    shutil.copytree(TRANSCRIPTS, folder)
    (folder / SID / "subagents" / "agent-b.jsonl").write_bytes(b"")
    assert transcript_files(folder, SID) == [folder / f"{SID}.jsonl", folder / SID / "subagents" / "agent-a0000000000000001.jsonl",
                                             folder / SID / "subagents" / "agent-b.jsonl"]
    # Before Claude Code writes it: the main file, which the page says is not there yet. (#86)
    assert transcript_files(folder, "00000000-0000-4000-8000-0000000000ff") == [
        folder / "00000000-0000-4000-8000-0000000000ff.jsonl"]


def test_the_coordination_sessions_folder_is_the_resolved_clones(tmp_path):
    real = tmp_path / "real" / "coordination"
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert coordination_folder(str(tmp_path / "link" / "coordination"), tmp_path / "home") == project_folder(
        str(real.resolve()), tmp_path / "home")


@pytest.mark.parametrize("value", ["../../x", "ABC", "00000000-0000-4000-8000-0000000000AA",
                                   "00000000000040008000000000000001", "{00000000-0000-4000-8000-000000000001}"])
def test_an_id_that_is_not_a_lowercase_uuid_opens_no_file(tmp_path, monkeypatch, value):
    # From the pane's option or a sessions.tsv row, the id is joined into no path unless it is a UUID as uuid4() and (#86)
    # `uuidgen | tr A-Z a-z` give it (#86).
    run = tmp_path / "runs" / "r"
    run.mkdir(parents=True)
    (run / "sessions.tsv").write_text(f"S1\t%2\t{value}\n")
    seen = []
    for module, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"), (os, "listdir")):
        real = getattr(module, name)

        def watch(*a, _real=real, **k):
            if a and str(tmp_path / "home") in str(a[0]):
                seen.append(a[0])
            return _real(*a, **k)
        monkeypatch.setattr(module, name, watch)
    assert work_session_id(run, "S1") == value
    for given in (value, work_session_id(run, "S1")):
        with pytest.raises(TranscriptUnknown) as e:
            transcript_files(tmp_path / "home" / ".claude" / "projects" / "-x", given)
        assert str(e.value) == f"transcript not known: the session id {value} is not a UUID"
    assert seen == []
