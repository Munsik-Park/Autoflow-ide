import json
import os
import re
import stat

import pytest

from autoflow_ide.start import (Setting, StartError, TokenError, Workspace, coordinator_base, coordinator_workspaces,
                                create_workspace, repository_of, workspace_instruction, find_workspace, find_workspace_entry, instruction, list_workspaces, open_coordination,
                                parse_config, read_token, setting_source, start_run, workspace_base)

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def fake_tmux(tmp_path, monkeypatch, fail_with=None):
    """A tmux on PATH that records its arguments; new-window prints a pane id as -P -F '#{pane_id}' does."""
    log = tmp_path / "calls"
    script = f'#!/bin/sh\necho "$@" >> "{log}"\n'
    if fail_with:
        script += f'echo "{fail_with}" >&2\nexit 1\n'
    script += 'if [ "$1" = new-window ]; then echo %7; fi\n'
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


# The session id the IDE starts a coordination session with, as uuid4() gives it: canonical, lowercase (#86).
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def session_id(calls):
    """The id the IDE kept on pane %7, from its `set-option -p -t %7 @autoflow-session-id <id>` (#86)."""
    [sid] = [c.split()[-1] for c in calls if c.startswith("set-option -p -t %7 @autoflow-session-id ")]
    assert re.fullmatch(UUID, sid)
    return sid


def coordinator_clone(tmp_path, base):
    """A coordinator clone whose tools/config.py prints `base` for the key base, as the coordinator's
    config.py prints a setting's value alone (expanded) when given its key."""
    tools = tmp_path / "coordination" / "tools"
    tools.mkdir(parents=True)
    (tools / "config.py").write_text(
        "import sys\n"
        f"print({str(base)!r}) if sys.argv[1:] == ['base'] else sys.exit('unknown key ' + ' '.join(sys.argv[1:]))\n")
    return str(tmp_path / "coordination")


def workspace(base, name, github):
    (base / name).mkdir(parents=True)
    (base / name / "repo.json").write_text(json.dumps({"github": github, "clone": "~/x", "base_branch": "main"}))


def test_instruction_is_the_operators():
    # As the operator gave it to the coordination session in the sandbox runs (run record, verbatim).
    assert instruction("autoflow-sandbox", 2) == "Workspace autoflow-sandbox, issue #2."


def test_find_workspace(tmp_path):
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    workspace(tmp_path, "opensre", "Munsik-Park/opensre")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "repo.json").write_text("not json")
    assert find_workspace(tmp_path, "Munsik-Park/autoflow-sandbox") == "autoflow-sandbox"
    with pytest.raises(StartError, match="no workspace for owner/none"):
        find_workspace(tmp_path, "owner/none")
    workspace(tmp_path, "sandbox-2", "Munsik-Park/autoflow-sandbox")
    with pytest.raises(StartError, match="several workspaces .*: autoflow-sandbox, sandbox-2"):
        find_workspace(tmp_path, "Munsik-Park/autoflow-sandbox")


def test_find_workspace_entry_reads_repo_json(tmp_path):
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "repo.json").write_text(json.dumps({"github": "owner/bare"}))
    assert find_workspace_entry(tmp_path, "Munsik-Park/autoflow-sandbox") == Workspace(
        "autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/x", "main")
    assert find_workspace_entry(tmp_path, "owner/bare") == Workspace("bare", "owner/bare", None, None)


def test_list_workspaces_in_name_order_skipping_what_is_not_one(tmp_path):
    workspace(tmp_path, "opensre", "Munsik-Park/opensre")
    workspace(tmp_path, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "repo.json").write_text(json.dumps({"github": "owner/bare"}))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "repo.json").write_text("not json")
    (tmp_path / "list").mkdir()
    (tmp_path / "list" / "repo.json").write_text("[]")
    (tmp_path / "nogithub").mkdir()
    (tmp_path / "nogithub" / "repo.json").write_text(json.dumps({"clone": "~/x"}))
    (tmp_path / "records").mkdir()
    assert list_workspaces(tmp_path) == [
        Workspace("autoflow-sandbox", "Munsik-Park/autoflow-sandbox", "~/x", "main"),
        Workspace("bare", "owner/bare", None, None),
        Workspace("opensre", "Munsik-Park/opensre", "~/x", "main"),
    ]
    assert list_workspaces(tmp_path / "nowhere") == []


async def test_coordinator_workspaces_are_those_of_its_base(tmp_path):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    workspace(base, "opensre", "Munsik-Park/opensre")
    clone = coordinator_clone(tmp_path, base)
    assert [w.name for w in await coordinator_workspaces(clone)] == ["autoflow-sandbox", "opensre"]


async def test_workspace_base_from_config_py(tmp_path):
    clone = coordinator_clone(tmp_path, tmp_path / "base")
    assert await workspace_base(clone) == tmp_path / "base"
    with pytest.raises(StartError, match="no .*config.py"):
        await workspace_base(str(tmp_path / "nowhere"))


async def test_opens_a_window_in_the_clone_and_starts_claude_with_the_instruction(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch)
    pane = await open_coordination("/coord", "autoflow-sandbox#4", "Workspace autoflow-sandbox, issue #4.")
    assert pane == "%7"
    calls = log.read_text().splitlines()
    sid = session_id(calls)
    assert calls == [
        "new-window -c /coord -n autoflow-sandbox#4 -P -F #{pane_id}",
        f"set-option -p -t %7 @autoflow-session-id {sid}",          # kept on the pane, before claude starts (#86)
        f"send-keys -t %7 -l -- claude --session-id {sid} 'Workspace autoflow-sandbox, issue #4.'",
        "send-keys -t %7 Enter",
    ]


async def test_not_inside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    with pytest.raises(StartError, match="not inside tmux"):
        await open_coordination("/coord", "w", "text")


async def test_tmux_failure_carries_its_message(tmp_path, monkeypatch):
    fake_tmux(tmp_path, monkeypatch, fail_with="no server running on /tmp/tmux-501/default")
    with pytest.raises(StartError, match="no server running"):
        await open_coordination("/coord", "w", "text")


async def test_start_run(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await start_run(clone, "Munsik-Park/autoflow-sandbox", 4) == "%7"
    calls = log.read_text().splitlines()
    assert calls[0] == f"new-window -c {clone} -n autoflow-sandbox#4 -P -F #{{pane_id}}"
    assert calls[2].endswith(f"claude --session-id {session_id(calls)} 'Workspace autoflow-sandbox, issue #4.'")


async def test_create_workspace_opens_a_coordination_session_named_apart_from_issue_windows(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await create_workspace(clone, "Munsik-Park/opensre") == "%7"
    calls = log.read_text().splitlines()
    sid = session_id(calls)
    assert calls == [
        f"new-window -c {clone} -n workspace:Munsik-Park/opensre -P -F #{{pane_id}}",
        f"set-option -p -t %7 @autoflow-session-id {sid}",
        f"send-keys -t %7 -l -- claude --session-id {sid} 'Create a workspace for Munsik-Park/opensre.'",
        "send-keys -t %7 Enter",
    ]


async def test_create_workspace_opens_nothing_when_one_names_the_repository(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "sandbox", "Munsik-Park/autoflow-sandbox")
    log = fake_tmux(tmp_path, monkeypatch)
    clone = coordinator_clone(tmp_path, base)
    with pytest.raises(StartError, match="Munsik-Park/autoflow-sandbox already has a workspace: sandbox"):
        await create_workspace(clone, "Munsik-Park/autoflow-sandbox")
    with pytest.raises(StartError, match="https://gitlab.com/a/b is not a GitHub repository"):
        await create_workspace(clone, "https://gitlab.com/a/b")
    assert not log.exists()


async def test_create_workspace_from_a_clone_address(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await create_workspace(clone, "git@github.com:connev-llm/llmroute.git") == "%7"
    calls = log.read_text().splitlines()
    assert calls[0] == f"new-window -c {clone} -n workspace:connev-llm/llmroute -P -F #{{pane_id}}"
    assert calls[2] == (f"send-keys -t %7 -l -- claude --session-id {session_id(calls)} 'Create a workspace for "
                        "connev-llm/llmroute, cloned from git@github.com:connev-llm/llmroute.git.'")
    with pytest.raises(StartError, match="Munsik-Park/autoflow-sandbox already has a workspace"):
        await create_workspace(clone, "https://github.com/Munsik-Park/autoflow-sandbox")


def test_a_repository_given_as_owner_name_web_address_or_clone_address():
    for address in ("connev-llm/llmroute", "connev-llm/llmroute.git", "https://github.com/connev-llm/llmroute",
                    "https://github.com/connev-llm/llmroute/", "github.com/connev-llm/llmroute",
                    "https://github.com/connev-llm/llmroute.git", "git@github.com:connev-llm/llmroute.git",
                    "ssh://git@github.com/connev-llm/llmroute.git", " git@github.com:connev-llm/llmroute.git "):
        assert repository_of(address) == "connev-llm/llmroute", address
    assert repository_of("owner/name.with_dots-1") == "owner/name.with_dots-1"
    for bad in ("", "llmroute", "owner/", "/name", "-owner/name", "owner/name/extra", "owner/..", "own er/name",
                "https://gitlab.com/connev-llm/llmroute", "git@gitlab.com:connev-llm/llmroute.git",
                "https://github.com/connev-llm/llmroute/issues"):
        assert repository_of(bad) is None, bad


def test_the_session_gets_the_address_as_given_so_it_clones_that_way():
    assert workspace_instruction("connev-llm/llmroute") == "Create a workspace for connev-llm/llmroute."
    assert workspace_instruction("git@github.com:connev-llm/llmroute.git") == (
        "Create a workspace for connev-llm/llmroute, cloned from git@github.com:connev-llm/llmroute.git.")


def test_config_settings_and_where_each_was_set():
    """config.py without a key (coordinator 0.8.0-rc2, #83): each setting with the file that set it, or the
    default."""
    home = "/home/claude/fxhome"
    assert parse_config((FIXTURES / "config-default.txt").read_text()) == {
        "root": Setting(f"{home}/.autoflow/coordination", None),
        "base": Setting(f"{home}/work/coordinator", None),
        "modules": Setting(f"{home}/work/autoflow-module", None),
        "home": Setting(f"{home}/.autoflow", None)}
    assert parse_config((FIXTURES / "config-global.txt").read_text())["base"] == Setting(
        f"{home}/work/coordinator", f"{home}/.autoflow/coordination.json")
    assert parse_config((FIXTURES / "config-local.txt").read_text())["base"] == Setting(
        "/home/claude/fx/base", "/home/claude/autoflow-coordinator/coordination.local.json")


def test_where_a_setting_was_set_by_the_coordinator_settings_file_names():
    sources = [parse_config((FIXTURES / f"config-{kind}.txt").read_text())["base"].source
               for kind in ("default", "global", "local")]
    assert [setting_source(s) for s in sources] == ["built-in default", "global settings", "local settings"]
    assert setting_source("/elsewhere/settings.json") == "/elsewhere/settings.json"


async def test_coordinator_base_from_config_py(tmp_path):
    tools = tmp_path / "clone" / "tools"
    tools.mkdir(parents=True)
    recorded = (FIXTURES / "config-global.txt").read_text()
    (tools / "config.py").write_text(f"import sys\nsys.stdout.write({recorded!r}) if not sys.argv[1:] else sys.exit(1)\n")
    assert await coordinator_base(str(tmp_path / "clone")) == Setting(
        "/home/claude/fxhome/work/coordinator", "/home/claude/fxhome/.autoflow/coordination.json")
    (tools / "config.py").write_text("print('runs\\t/x')\n")
    with pytest.raises(StartError, match="printed no base"):
        await coordinator_base(str(tmp_path / "clone"))
    with pytest.raises(StartError, match="no .*config.py"):
        await coordinator_base(str(tmp_path / "nowhere"))


# A workspace's gh-token (#74), as the coordinator reads it (autoflow-coordinator#71, tools/prepare.py read_token).

def token(folder, content, mode=0o600):
    path = folder / "gh-token"
    path.write_text(content, newline="")
    path.chmod(mode)
    return path


def test_read_token(tmp_path):
    assert read_token(tmp_path) is None  # no gh-token: as before the workspace had one
    token(tmp_path, "github_pat_abc\n")
    assert read_token(tmp_path) == "github_pat_abc"
    token(tmp_path, "github_pat_abc")
    assert read_token(tmp_path) == "github_pat_abc"


@pytest.mark.parametrize("content, mode, message", [
    ("github_pat_abc\n", 0o644, "readable by you only: chmod 600"),
    ("", 0o600, "is empty"),
    ("\n", 0o600, "is empty"),
    ("github_pat_abc\r\n", 0o600, "not one token alone on one line"),
    ("github_pat_abc\n\n", 0o600, "not one token alone on one line"),
    ("github pat\n", 0o600, "not one token alone on one line"),
])
def test_a_gh_token_the_coordinator_would_refuse(tmp_path, content, mode, message):
    token(tmp_path, content, mode)
    with pytest.raises(TokenError, match=message) as e:
        read_token(tmp_path)
    assert "github_pat_abc" not in str(e.value)


def test_a_gh_token_that_is_not_a_file(tmp_path):
    (tmp_path / "gh-token").mkdir()
    with pytest.raises(TokenError, match="not a regular file"):
        read_token(tmp_path)


async def test_opens_claude_with_the_token_read_from_its_file(tmp_path, monkeypatch):
    # The typed line reads the file, as the coordinator's tools/launch.sh does: the token is neither typed nor
    # in tmux's arguments.
    log = fake_tmux(tmp_path, monkeypatch)
    await open_coordination("/coord", "w#1", "Workspace w, issue #1.", tmp_path / "my base" / "gh-token")
    calls = log.read_text().splitlines()
    assert calls[2] == (f"send-keys -t %7 -l -- GH_TOKEN=$(cat '{tmp_path}/my base/gh-token') claude --session-id "
                        f"{session_id(calls)} 'Workspace w, issue #1.'")


async def test_start_run_with_the_workspaces_token(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    token(base / "autoflow-sandbox", "github_pat_abc\n")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await start_run(clone, "Munsik-Park/autoflow-sandbox", 4) == "%7"
    calls = log.read_text()
    assert (f"GH_TOKEN=$(cat {base}/autoflow-sandbox/gh-token) claude --session-id {session_id(calls.splitlines())} "
            "'Workspace autoflow-sandbox, issue #4.'") in calls
    assert "github_pat_abc" not in calls


async def test_start_run_opens_nothing_with_a_gh_token_it_cannot_use(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    token(base / "autoflow-sandbox", "github_pat_abc\n", 0o644)
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    with pytest.raises(StartError, match="chmod 600") as e:
        await start_run(clone, "Munsik-Park/autoflow-sandbox", 4)
    assert "github_pat_abc" not in str(e.value)
    assert not log.exists()


# Instructions given at start (#85): typed as one word of the first prompt, as the operator gave them.

import shlex


def fake_tmux_args(tmp_path, monkeypatch):
    """A tmux on PATH that records each argument byte for byte (NUL after each, RS after each call), so a text
    holding a backslash is compared as typed (#85); `echo "$@"` under /bin/sh rewrites backslash sequences."""
    log = tmp_path / "args"
    script = (f'#!/bin/sh\nprintf \'%s\\0\' "$@" >> "{log}"\nprintf \'\\036\' >> "{log}"\n'
              'if [ "$1" = new-window ]; then echo %7; fi\n')
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    return log


def typed_line(log):
    """The line typed by `send-keys -t %7 -l -- <line>`, as tmux received it."""
    calls = [c.split(b"\0")[:-1] for c in log.read_bytes().split(b"\x1e")[:-1]]
    [line] = [c[5] for c in calls if c[:5] == [b"send-keys", b"-t", b"%7", b"-l", b"--"]]
    return line.decode()


INSTRUCTIONS = [
    "focus on the tests first",
    "don't touch the README",                   # '
    'say "why" in the report',                  # "
    "keep $HOME and ${PATH} as they are",       # $
    "run `date` after it",                      # backticks
    r"a path C:\work\new and a \n",             # \
    "no rush!",                                 # !
    "focus on  tests   here",                   # a run of inner blanks
]


@pytest.mark.parametrize("text", INSTRUCTIONS)
async def test_start_run_types_the_instructions_as_one_word_of_the_first_prompt(tmp_path, monkeypatch, text):
    # A POSIX shell splits the typed line into exactly `claude` and the prompt: the instructions are neither
    # split nor expanded (#85).
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux_args(tmp_path, monkeypatch)
    assert await start_run(clone, "Munsik-Park/autoflow-sandbox", 4, text) == "%7"
    words = shlex.split(typed_line(log))
    assert words[:2] == ["claude", "--session-id"] and re.fullmatch(UUID, words[2])     # #86 (#86)
    assert words[3:] == ["Workspace autoflow-sandbox, issue #4. " + text]


@pytest.mark.parametrize("text", INSTRUCTIONS)
async def test_start_run_types_the_instructions_after_the_token(tmp_path, monkeypatch, text):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    token(base / "autoflow-sandbox", "github_pat_abc\n")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux_args(tmp_path, monkeypatch)
    await start_run(clone, "Munsik-Park/autoflow-sandbox", 4, text)
    words = shlex.split(typed_line(log))
    assert words[0].startswith("GH_TOKEN=$(cat")
    assert words[-4:-2] == ["claude", "--session-id"] and re.fullmatch(UUID, words[-2])     # #86 (#86)
    assert words[-1] == "Workspace autoflow-sandbox, issue #4. " + text
    assert "github_pat_abc" not in log.read_text()


async def test_start_run_with_no_instructions_types_todays_line(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux_args(tmp_path, monkeypatch)
    await start_run(clone, "Munsik-Park/autoflow-sandbox", 4, "")
    assert re.fullmatch(f"claude --session-id {UUID} 'Workspace autoflow-sandbox, issue #4.'", typed_line(log))


# Control characters in the instructions, as a paste can carry them (#85): the shell's line editor would act on
# them whatever the quoting (Ctrl-U erases the typed `claude '…`), so they are not typed.

@pytest.mark.parametrize("text, kept", [
    ("\x15touch x #", "touch x #"),                         # Ctrl-U, then a command
    ("stop\x03 here", "stop here"),                         # Ctrl-C
    ("one\rtwo", "one two"),                                # CR, a space
    ("one\ntwo", "one two"),                                # LF, a space
    ("\x1b[2Jclear \x1b]0;title\x07it", "[2Jclear ]0;titleit"),   # ESC sequences and BEL
    ("del\x7fete", "delete"),                               # DEL
    ("c1\x85\x9bcontrols", "c1 controls"),                  # C1 controls: NEL a line break, CSI dropped
    ("tab\there", "tab here"),
    ("한글 지시 그대로", "한글 지시 그대로"),                      # printable non-ASCII is kept
    # Every other whitespace character is a plain space, so the words stay apart (#85).
    ("fix\u00a0the\u202fparser", "fix the parser"),         # no-break spaces
    ("thin\u2009and\u2003em", "thin and em"),               # thin and em spaces
    ("전각\u3000공백", "전각 공백"),                             # ideographic space
    ("one\u2028two\u2029three", "one two three"),          # line and paragraph separators
    ("v\x0bf\x0cr\r\nend", "v f r  end"),                  # VT, FF, CR LF
])
async def test_start_run_drops_control_characters_from_the_instructions(tmp_path, monkeypatch, text, kept):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux_args(tmp_path, monkeypatch)
    await start_run(clone, "Munsik-Park/autoflow-sandbox", 4, text)
    line = typed_line(log)
    assert all(c.isprintable() for c in line)
    words = shlex.split(line)
    assert words[:2] == ["claude", "--session-id"] and re.fullmatch(UUID, words[2])     # #86 (#86)
    assert words[3:] == ["Workspace autoflow-sandbox, issue #4. " + kept]


# Browser control (#86): a start from the browser leaves the operator's tmux window where it is, and every
# coordination session is started with a session id kept on its pane, so its transcript can be found. (#86)

async def test_a_detached_open_leaves_the_operators_window_and_the_terminals_open_is_as_before(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch)
    assert await open_coordination("/coord", "autoflow-sandbox#4", "Workspace autoflow-sandbox, issue #4.",
                                   detached=True) == "%7"
    assert log.read_text().splitlines()[0] == "new-window -d -c /coord -n autoflow-sandbox#4 -P -F #{pane_id}"
    log.unlink()
    await open_coordination("/coord", "autoflow-sandbox#4", "Workspace autoflow-sandbox, issue #4.")
    assert log.read_text().splitlines()[0] == "new-window -c /coord -n autoflow-sandbox#4 -P -F #{pane_id}"


async def test_start_run_passes_detached_to_the_window(tmp_path, monkeypatch):
    base = tmp_path / "base"
    workspace(base, "autoflow-sandbox", "Munsik-Park/autoflow-sandbox")
    clone = coordinator_clone(tmp_path, base)
    log = fake_tmux(tmp_path, monkeypatch)
    assert await start_run(clone, "Munsik-Park/autoflow-sandbox", 4, "", detached=True) == "%7"
    assert log.read_text().splitlines()[0] == f"new-window -d -c {clone} -n autoflow-sandbox#4 -P -F #{{pane_id}}"


async def test_each_open_has_a_session_id_of_its_own(tmp_path, monkeypatch):
    log = fake_tmux(tmp_path, monkeypatch)
    await open_coordination("/coord", "w#1", "one")
    first = session_id(log.read_text().splitlines())
    log.unlink()
    await open_coordination("/coord", "w#1", "two")
    assert session_id(log.read_text().splitlines()) != first


async def test_a_failed_set_option_closes_the_window_and_types_nothing(tmp_path, monkeypatch):
    # The id would be known to no one, so no session is started without it (#86).
    log = tmp_path / "calls"
    script = (f'#!/bin/sh\necho "$@" >> "{log}"\n'
              'if [ "$1" = new-window ]; then echo %7; fi\n'
              'if [ "$1" = set-option ]; then echo "invalid option: @autoflow-session-id" >&2; exit 1; fi\n')
    tmux = tmp_path / "tmux"
    tmux.write_text(script)
    tmux.chmod(tmux.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TMUX", "/tmp/tmux-test,1,0")
    with pytest.raises(StartError, match="invalid option: @autoflow-session-id"):
        await open_coordination("/coord", "w#1", "text")
    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["new-window", "set-option", "kill-window"]
    assert calls[2] == "kill-window -t %7"
    assert not any(c.startswith("send-keys") for c in calls)


async def test_the_clone_given_through_a_symlink_opens_in_its_resolved_path(tmp_path, monkeypatch):
    # Claude Code names its project folder after the physical working directory (#86).
    real = tmp_path / "real" / "coordination"
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    log = fake_tmux(tmp_path, monkeypatch)
    await open_coordination(str(tmp_path / "link" / "coordination"), "w#1", "text")
    assert log.read_text().splitlines()[0] == f"new-window -c {real.resolve()} -n w#1 -P -F #{{pane_id}}"
