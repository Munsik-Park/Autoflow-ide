// The IDE's page (#86). Every string the server sends goes into the page as text only: textContent and
// createTextNode, never markup; no URL is taken from it. The view is xterm.js with no input: keys typed in it reach
// no pane, and input goes only through the input form.
"use strict";
(() => {
  const base = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
  const $ = (id) => document.getElementById(id);
  const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "Up", "Down", "Left", "Right", "Enter", "Escape"];
  const LABELS = { Up: "↑", Down: "↓", Left: "←", Right: "→", Escape: "Esc" };
  let pageId = null;
  let state = null;
  let chosenRow = null;
  let target = null;          // {row} for a coordination session, {run, session} for a work session (#86)
  let latestStart = null;     // the issue of this page's latest start, whose box may open (#86)
  let box = null;
  let term = null;
  let stopped = false;

  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }

  function say(text) {
    const line = el("div", text);
    $("messages").appendChild(line);
    $("messages").scrollTop = $("messages").scrollHeight;
  }

  async function act(action, body) {
    if (!pageId || stopped) return;
    try {
      const response = await fetch(base + "action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ page: pageId, action: action }, body || {})),
      });
      if (!response.ok) say(action + ": refused by the server (" + response.status + ")");
    } catch (e) {
      say(action + ": the server cannot be reached");
    }
  }

  function fillRows(container, list, attr, chosen, onClick) {
    container.replaceChildren();
    for (const [key, cells] of list) {
      const row = el("div", undefined, "row");
      row.dataset[attr] = key;
      if (key === chosen) row.classList.add("chosen");
      for (const cell of cells) row.appendChild(el("span", cell));
      if (onClick) row.addEventListener("click", () => onClick(key));
      container.appendChild(row);
    }
  }

  function render() {
    if (!state) return;
    fillRows($("issues"), state.issues.map((r) => [r.key, r.cells]), "row", chosenRow, chooseRow);
    const chosen = state.chosen || { info: [], steps: [], sessions: [] };
    const info = $("info");
    info.replaceChildren();
    for (const [label, value] of chosen.info) info.appendChild(el("div", label.padEnd(10) + value, "line"));
    fillRows($("steps"), chosen.steps.map((cells, n) => [String(n + 1), cells]), "step", null, null);
    const work = target && target.run ? target.session : null;
    fillRows($("sessions"), chosen.sessions.map((cells) => [cells[0], cells]), "session", work, chooseWork);
  }

  function chooseRow(key) {
    chosenRow = key;
    target = { row: key };
    act("select", { row: key });
    showInput();
    render();
  }

  function chooseWork(name) {
    const run = state && state.chosen ? state.chosen.run : null;
    if (!run) return;
    target = { run: run, session: name };
    showInput();
    render();
  }

  function showInput() {
    $("input").hidden = !target;
    if (!target) return;
    const work = Boolean(target.run);
    $("input-target").textContent = work ? target.session + " of " + target.run : "the coordination session of " + target.row;
    $("message-box").hidden = work;
    $("input-note").textContent = work
      ? "only the keys that answer a prompt its screen shows; the coordination session is not told of these keys"
      : "a message goes only to an empty input area; the keys only to a prompt";
  }

  function issueOf(row) {
    const found = state ? state.issues.find((r) => r.key === row) : null;
    return found ? found.number : null;
  }

  function startAction(name) {
    if (chosenRow === null) {
      say("choose an issue's row first");
      return;
    }
    latestStart = issueOf(chosenRow);
    act(name, { row: chosenRow });
  }

  function openBox(message) {
    if (latestStart !== null && message.number !== latestStart) {
      say("#" + message.number + ": the instructions box is not opened, a later start was asked");
      return;
    }
    box = message;
    $("instructions-title").textContent = "Instructions for #" + message.number + " (" + message.opener + ")";
    $("instructions").hidden = false;
    $("instructions-text").value = "";
    $("instructions-text").focus();
  }

  function closeBox() {
    box = null;
    $("instructions").hidden = true;
  }

  function draw(frame) {
    if (!term) {
      term = new Terminal({ disableStdin: true, cols: frame.width, rows: frame.height, scrollback: 0, cursorBlink: false });
      term.open($("view"));
    } else if (term.cols !== frame.width || term.rows !== frame.height) {
      term.resize(frame.width, frame.height);
    }
    term.reset();
    // The alternate buffer: the view is the screen alone, with no history, and xterm.js draws the cursor only once
    // the buffer is switched or the terminal is focused (#86).
    let out = "\x1b[?1047h\x1b[?25l";
    frame.rows.forEach((row, n) => {
      out += "\x1b[" + (n + 1) + ";1H\x1b[0m" + row + "\x1b[0m";
    });
    out += "\x1b[" + (frame.cursor[1] + 1) + ";" + (frame.cursor[0] + 1) + "H";
    out += frame.cursor_visible ? "\x1b[?25h" : "\x1b[?25l";
    term.write(out);
    $("view-name").textContent = frame.pane;
  }

  function closeView() {
    act("close-view");
    if (term) {
      term.dispose();
      term = null;
    }
    $("view").replaceChildren();
    $("view-name").textContent = "";
  }

  async function openRecords() {
    if (!target) {
      say("choose a session first");
      return;
    }
    const query = target.run
      ? "run=" + encodeURIComponent(target.run) + "&session=" + encodeURIComponent(target.session)
      : "row=" + encodeURIComponent(target.row);
    let records;
    try {
      const response = await fetch(base + "records?" + query);
      records = await response.json();
    } catch (e) {
      say("records: the server cannot be reached");
      return;
    }
    $("records").hidden = false;
    $("records-name").textContent = target.run ? target.session : target.row;
    const about = $("records-about");
    about.replaceChildren();
    $("screen-text").textContent = records.screen || "";
    const files = $("transcripts");
    files.replaceChildren();
    if (records.error) {
      about.appendChild(el("div", records.error));
      say(records.error);
      return;
    }
    for (const line of records.about || []) about.appendChild(el("div", line));
    for (const file of records.files || []) {
      const part = el("div", undefined, "transcript");
      part.appendChild(el("div", file.name + ", last written " + new Date(file.modified * 1000).toISOString()));
      const notes = el("div", file.notes.join("; "), "notes");
      part.appendChild(notes);
      part.appendChild(el("pre", file.data.split("\n").filter((line) => line).map(shownRecord).join("\n\n")));
      files.appendChild(part);
    }
  }

  // A record as indented "key: value" lines, its strings as written, so what a session read or printed reads as
  // it was; a line that is not JSON is shown as it is (#86).
  function shownRecord(line) {
    let record;
    try {
      record = JSON.parse(line);
    } catch (e) {
      return line;
    }
    return tree(record, "").join("\n");
  }

  function tree(value, indent) {
    if (value === null || typeof value !== "object") return [indent + String(value)];
    const lines = [];
    for (const [key, item] of Object.entries(value)) {
      const name = Array.isArray(value) ? "-" : key + ":";
      if (item !== null && typeof item === "object") {
        lines.push(indent + name);
        lines.push(...tree(item, indent + "  "));
      } else {
        lines.push(indent + name + " " + String(item));
      }
    }
    return lines;
  }

  function serverStopped() {
    stopped = true;
    $("server-state").textContent = "server stopped";
  }

  function connect() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + base + "ws");
    ws.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (message.type === "hello") {
        pageId = message.page;
        $("server-state").textContent = "connected";
      } else if (message.type === "state") {
        state = message.state;
        render();
      } else if (message.type === "message") {
        say(message.text);
      } else if (message.type === "instructions") {
        openBox(message);
      } else if (message.type === "frame") {
        draw(message);
      } else if (message.type === "stopped") {
        serverStopped();
      }
    });
    ws.addEventListener("close", serverStopped);
  }

  for (const key of KEYS) {
    const button = el("button", LABELS[key] || key);
    button.type = "button";
    button.dataset.key = key;
    button.addEventListener("click", () => {
      if (target) act("keys", Object.assign({ key: key }, target));
    });
    $("keys").appendChild(button);
  }
  $("start").addEventListener("click", () => startAction("start"));
  $("new-session").addEventListener("click", () => startAction("new-session"));
  $("go").addEventListener("click", () => {
    if (target) act("go", target);
    else say("choose a session first");
  });
  $("records-open").addEventListener("click", openRecords);
  $("view-close").addEventListener("click", closeView);
  $("message-send").addEventListener("click", () => {
    if (target && !target.run) act("message", { row: target.row, text: $("message-text").value });
  });
  $("instructions-text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && box) {
      act("instructions", { repository: box.repository, number: box.number, opener: box.opener,
                            text: $("instructions-text").value });
      closeBox();
    } else if (event.key === "Escape") {
      closeBox();
    }
  });
  connect();
})();
