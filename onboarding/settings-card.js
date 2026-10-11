"use strict";
// The Setup card on Alfred's Settings page (index.html): open any step of the setup again, and turn
// "Start Alfred when I sign in" on or off (it is a value in Windows' startup list, so this removes it
// too). Part of the setup (jarvis/onboarding), kept apart from app.js. Text is set with textContent only.

(() => {
  const card = document.getElementById("setup-card");
  if (!card) return;
  const $ = (id) => document.getElementById(id);
  const STATUS = { done: "done", skipped: "skipped" };

  // The page key app.js got from /auth lives in sessionStorage, readable only by this origin
  // (127.0.0.1 at Alfred's port). Every call sends it; there is no cookie.
  const key = (() => {
    try { return sessionStorage.getItem("alfred-key") || ""; } catch { return ""; }
  })();

  async function api(method, path, body) {
    const options = { method, headers: { "X-Alfred": key } };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body || {});
    }
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) throw new Error(data.error || `Alfred answered ${response.status}.`);
    return data;
  }

  async function load() {
    let state;
    let autostart;
    try {
      [state, autostart] = await Promise.all([api("GET", "/api/onboarding/state"), api("GET", "/api/onboarding/autostart")]);
    } catch {
      return; // no setup on this server: the card stays hidden
    }
    const steps = $("setup-steps");
    steps.textContent = "";
    state.steps.forEach((step, i) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "link";
      button.textContent = `${i + 1}. ${step.title}${STATUS[step.status] ? ` (${STATUS[step.status]})` : ""}`;
      button.onclick = () => { location.href = `onboarding/index.html#${step.name}`; };
      steps.append(button);
    });
    const box = $("setup-autostart");
    box.checked = autostart.enabled;
    box.disabled = !autostart.supported;
    $("setup-autostart-help").textContent = !autostart.supported ? "Only on Windows."
      : autostart.simulated ? "The stand-in engine (--fake-core) only pretends: Windows’ startup apps don’t change."
        : `Alfred adds itself to Windows’ startup apps (${autostart.where}). Turn this off to remove it.`;
    card.hidden = false;
  }

  $("setup-autostart").onchange = async (event) => {
    const box = event.target;
    try {
      const result = await api("PUT", "/api/onboarding/autostart", { on: box.checked });
      box.checked = result.enabled;
      $("setup-msg").textContent = result.enabled ? "Alfred starts when you sign in." : "Removed from Windows’ startup apps.";
    } catch (e) {
      box.checked = !box.checked;
      $("setup-msg").textContent = e.message;
    }
  };
  for (const tab of document.querySelectorAll('.tab[data-view="settings"]')) tab.addEventListener("click", load);
  load();
})();
