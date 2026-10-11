"use strict";
// Alfred's web demo: the banner around Alfred's window. The example buttons send their command to
// Alfred as typed commands are sent (POST /api/command), "Replay the setup" opens the real setup on a
// new simulated PC, and the simulated PC's panel folds away on small screens. Ctrl+Alt+Backspace stops
// Alfred, as the Windows app's Stop hotkey does.

(() => {
  const Demo = window.AlfredDemo = window.AlfredDemo || {};
  const $ = (id) => document.getElementById(id);

  function say(text) {
    const box = $("demo-msg");
    if (!box) return;
    box.hidden = !text;
    box.textContent = text || "";
  }

  async function send(command) {
    say("");
    let data = {};
    let response;
    try {
      response = await fetch("/api/command", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Alfred": "web-demo" },
        body: JSON.stringify({ text: command }),
      });
      data = await response.json();
    } catch {
      response = null;
    }
    if (response && response.ok) return;
    const input = $("input");
    if (input && !input.value.trim()) input.value = command; // so it can be sent again in a moment
    say((data && data.error) || "Alfred didn't take that. Try again in a moment.");
  }

  for (const chip of document.querySelectorAll(".demo-chip[data-command]")) {
    chip.title = `Sends: ${chip.dataset.command}`;
    chip.addEventListener("click", () => send(chip.dataset.command));
  }

  // The setup's key fields say, right where a key would go, that this is the demo: no real key is
  // needed, and what is pasted stays in this tab (the stand-in keeps only a hint, like "sk-ant-...4f2c").
  for (const id of ["anthropic-key", "openai-key", "bot-token", "spectrum-secret"]) {
    const field = $(id);
    const label = field && field.closest("label");
    if (!label) continue;
    const note = document.createElement("p");
    note.className = "demo-keynote";
    note.textContent = "Web demo: you don't need a real key. Any text that looks like one works, and it never leaves this tab.";
    label.after(note);
  }

  const replay = $("demo-replay");
  if (replay) {
    replay.addEventListener("click", () => {
      if (Demo.server) Demo.server.replaySetup();
      location.href = "onboarding/index.html";
    });
  }

  // While Alfred's approval is open it is the thing to answer: the banner and the simulated PC step
  // aside (no Tab, no clicks) until it closes. STOP stays in reach.
  const sheet = $("approval");
  if (sheet) {
    const modal = () => {
      for (const node of document.querySelectorAll(".demo-bar, .demo-pc")) node.inert = !sheet.hidden;
    };
    new MutationObserver(modal).observe(sheet, { attributes: true, attributeFilter: ["hidden"] });
  }

  const panel = $("demo-pc");
  const toggle = $("demo-pc-toggle");
  if (panel && toggle) {
    toggle.addEventListener("click", () => {
      const hidden = panel.toggleAttribute("data-collapsed");
      toggle.setAttribute("aria-expanded", String(!hidden));
      toggle.textContent = hidden ? "Show" : "Hide";
    });
  }

  // Back from the setup with the browser's Back button: the page may come back from its cache with the
  // state it had when it left, so load it again to pick up what the setup saved.
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && Demo.page === "main") location.reload();
  });

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.altKey && e.key === "Backspace" && Demo.server) {
      e.preventDefault();
      Demo.server.stop("hotkey");
    }
  });
})();
