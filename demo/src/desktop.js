"use strict";
// Alfred's web demo: the simulated PC. A small Windows-like desktop (and the paired laptop) drawn in
// HTML and CSS, and what scenarios.js needs to show Alfred at work there: open and focus windows,
// type into Notepad, play music, change the volume, write an email, fill in a form with a visible
// mouse pointer, and copy a file to the laptop. Nothing here touches the viewer's computer. On a page
// without the panel (the setup) the same desktop is kept off screen.

(() => {
  const Demo = window.AlfredDemo = window.AlfredDemo || {};
  const SPEED = Number((window.AlfredDemoTest || {}).speed) || 1;
  const W = 800; // the desktop's own pixels; it is scaled to fit its panel
  const LW = 640; // the laptop's
  const SHOT = 1.6; // Alfred's screenshots are 1280 px wide: 1.6 of these pixels
  const reduced = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const svg = (markup, cls) => {
    const span = el("span", cls || "dk-icon");
    span.innerHTML = markup; // constant icons only; text always goes in with textContent
    span.setAttribute("aria-hidden", "true");
    return span;
  };
  const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms / SPEED); });
  const norm = (text) => String(text || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

  const ICONS = {
    start: '<svg viewBox="0 0 20 20"><rect x="2" y="2" width="7.5" height="7.5" fill="#1a8cff"/><rect x="10.5" y="2" width="7.5" height="7.5" fill="#1a8cff"/><rect x="2" y="10.5" width="7.5" height="7.5" fill="#1a8cff"/><rect x="10.5" y="10.5" width="7.5" height="7.5" fill="#1a8cff"/></svg>',
    explorer: '<svg viewBox="0 0 20 20"><path d="M2 5.5h6l1.6 1.6H18v8.4H2z" fill="#f2b62a"/><path d="M2 8h16v7.5H2z" fill="#ffd25c"/></svg>',
    edge: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="#1d9bd8"/><path d="M4.5 12.5c1-4.5 9.5-6 11 .2-1.7-1.5-4.6-1.6-5.6.1-.8 1.6 1 3.3 3.3 3.3-3.8 1.6-8.1-.1-8.7-3.6z" fill="#3dd16b"/></svg>',
    notepad: '<svg viewBox="0 0 20 20"><rect x="4" y="2.5" width="12" height="15" rx="1.5" fill="#fff" stroke="#3b7dd8"/><path d="M6.5 7h7M6.5 10h7M6.5 13h5" stroke="#3b7dd8" stroke-width="1.2"/></svg>',
    player: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="#ff6a3d"/><path d="M8 6.5v7l5.5-3.5z" fill="#fff"/></svg>',
    outlook: '<svg viewBox="0 0 20 20"><rect x="6" y="4" width="12" height="12" rx="1.5" fill="#28a8ea"/><path d="M6 7l6 4 6-4" stroke="#fff" stroke-width="1.2" fill="none"/><rect x="2" y="6" width="9" height="9" rx="1.5" fill="#0a64ad"/><circle cx="6.5" cy="10.5" r="2.2" stroke="#fff" stroke-width="1.3" fill="none"/></svg>',
    taskmgr: '<svg viewBox="0 0 20 20"><rect x="2.5" y="3.5" width="15" height="13" rx="1.5" fill="#2e7d32"/><path d="M4.5 13l3-4 2.5 2.5 3-5 2.5 3" stroke="#b9f6ca" stroke-width="1.4" fill="none"/></svg>',
    alfred: '<svg viewBox="0 0 20 20"><defs><radialGradient id="dk-alfred-orb" cx="50%" cy="35%" r="70%"><stop offset="0" stop-color="#6fd3f7"/><stop offset=".55" stop-color="#1b7fd9"/><stop offset="1" stop-color="#0b3d8f"/></radialGradient></defs><circle cx="10" cy="10" r="9" fill="url(#dk-alfred-orb)"/><path d="M4.4 7.5L8.9 10l-4.5 2.5zM15.6 7.5L11.1 10l4.5 2.5z" fill="#fff"/><rect x="8.5" y="8.5" width="3" height="3" rx=".8" fill="#fff"/><rect x="9.4" y="9.4" width="1.2" height="1.2" rx=".3" fill="#1b7fd9"/><circle cx="15.7" cy="7.3" r="1.1" fill="#8fd14f" stroke="#fff" stroke-width=".5"/></svg>',
    pdf: '<svg viewBox="0 0 20 20"><path d="M5 2h7l4 4v12H5z" fill="#fff" stroke="#c62828"/><rect x="3.5" y="10" width="9" height="5" rx="1" fill="#c62828"/></svg>',
    xlsx: '<svg viewBox="0 0 20 20"><path d="M5 2h7l4 4v12H5z" fill="#fff" stroke="#1e7b45"/><rect x="3.5" y="10" width="9" height="5" rx="1" fill="#1e7b45"/></svg>',
    txt: '<svg viewBox="0 0 20 20"><path d="M5 2h7l4 4v12H5z" fill="#fff" stroke="#607d8b"/><path d="M7 9h6M7 11.5h6M7 14h4" stroke="#607d8b"/></svg>',
    jpg: '<svg viewBox="0 0 20 20"><rect x="3" y="4" width="14" height="12" rx="1.5" fill="#4aa3df"/><path d="M3 14l4-4 3 3 2.5-2.5L17 15v1H3z" fill="#2e7d32"/></svg>',
    zip: '<svg viewBox="0 0 20 20"><path d="M5 2h7l4 4v12H5z" fill="#ffe082" stroke="#b28704"/><path d="M9 4v8" stroke="#6d4c41" stroke-dasharray="1.5 1.5"/></svg>',
    speaker: '<svg viewBox="0 0 20 20"><path d="M3 8h3l4-3.5v11L6 12H3z" fill="currentColor"/><path d="M13 7.5a3.5 3.5 0 0 1 0 5M15 5.5a6.3 6.3 0 0 1 0 9" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>',
    wifi: '<svg viewBox="0 0 20 20"><path d="M3 8a10 10 0 0 1 14 0M5.5 10.5a6.4 6.4 0 0 1 9 0M8 13a2.8 2.8 0 0 1 4 0" stroke="currentColor" stroke-width="1.5" fill="none"/><circle cx="10" cy="15.5" r="1.2" fill="currentColor"/></svg>',
    prev: '<svg viewBox="0 0 20 20"><path d="M5 5h2v10H5zM16 5v10l-8-5z" fill="currentColor"/></svg>',
    play: '<svg viewBox="0 0 20 20"><path d="M7 4.5v11l9-5.5z" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 20 20"><path d="M6 4.5h3v11H6zM11 4.5h3v11h-3z" fill="currentColor"/></svg>',
    next: '<svg viewBox="0 0 20 20"><path d="M13 5h2v10h-2zM4 5v10l8-5z" fill="currentColor"/></svg>',
    pointer: '<svg viewBox="0 0 20 24"><path d="M2 1.5v18l4.6-4.3 3 6.8 3-1.3-3-6.6h6.3z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    clip: '<svg viewBox="0 0 20 20"><path d="M13.5 6.5l-6 6a1.8 1.8 0 0 0 2.5 2.5l6.5-6.5a3.2 3.2 0 0 0-4.5-4.5L5.5 10.5a4.6 4.6 0 0 0 6.5 6.5l4-4" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>',
  };

  // The apps on the simulated PC: where their window sits, and the names Alfred knows them by. Only their
  // own names: "open chrome" must not open Edge.
  const APPS = {
    notepad: { name: "Notepad", title: "Untitled - Notepad", process: "Notepad.exe", box: [84, 18, 340, 222],
      aliases: ["notepad", "note pad", "notes", "text editor"] },
    player: { name: "Media Player", title: "Media Player", process: "Microsoft.Media.Player.exe", box: [470, 244, 300, 196],
      aliases: ["media player", "music", "music player", "player", "groove", "windows media player"] },
    explorer: { name: "File Explorer", title: "Documents - File Explorer", process: "explorer.exe", box: [44, 178, 392, 252],
      aliases: ["file explorer", "explorer", "files", "documents", "folder", "this pc"] },
    edge: { name: "Microsoft Edge", title: "Sign up - Contoso Events - Microsoft Edge", process: "msedge.exe", box: [292, 34, 474, 346],
      aliases: ["microsoft edge", "edge", "browser", "web browser", "internet", "sign up form", "form"] },
    outlook: { name: "Outlook", title: "Inbox - Alex Morgan - Outlook", process: "olk.exe", box: [84, 14, 632, 428],
      aliases: ["outlook", "mail", "email", "e mail", "inbox", "new outlook"] },
    taskmgr: { name: "Task Manager", title: "Task Manager", process: "Taskmgr.exe", box: [318, 30, 440, 330],
      aliases: ["task manager", "taskmgr", "task mgr"] },
  };
  const PINNED = ["explorer", "edge", "notepad", "player", "outlook", "taskmgr"];
  const TRACKS = [
    { title: "Blue Skies", artist: "The Aero Band", length: 214 },
    { title: "Glass Garden", artist: "Bubble Theory", length: 187 },
    { title: "Morning Water", artist: "Lumen Drive", length: 243 },
  ];
  const FILES = [
    { name: "Budget 2026.xlsx", kind: "xlsx", date: "10/2/2026 4:15 PM", size: "48 KB" },
    { name: "Notes.txt", kind: "txt", date: "10/8/2026 9:02 AM", size: "2 KB" },
    { name: "Quarterly report.pdf", kind: "pdf", date: "10/10/2026 5:47 PM", size: "2.4 MB" },
    { name: "Team photo.jpg", kind: "jpg", date: "9/27/2026 1:30 PM", size: "3.1 MB" },
  ];
  const MAIL = [
    { from: "Sam Rivera", subject: "Can you send the quarterly report?", preview: "Hi Alex, when you get a chance…", time: "9:12 AM",
      body: "Hi Alex,\n\nCan you send me the quarterly report when you get a chance? I'd like to read it before Monday.\n\nThanks,\nSam" },
    { from: "Jordan Lee", subject: "Team lunch on Friday", preview: "Pizza or tacos? Vote by Thursday…", time: "Yesterday" },
    { from: "Contoso Events", subject: "Your sign-up link", preview: "Finish signing up for the meetup…", time: "Wed" },
  ];
  // Task Manager's processes: [name, CPU %, memory MB]; the numbers move a little each time.
  const PROCESSES = [
    ["Microsoft Edge", 9.8, 1204], ["Microsoft Teams", 6.1, 812], ["Alfred", 2.3, 214], ["Media Player", 1.4, 96],
    ["Windows Explorer", 0.9, 141], ["Notepad", 0.1, 32], ["Antimalware Service Executable", 0.6, 178],
  ];

  class Desktop {
    constructor() {
      this.gen = 0; // halt() moves it on, which ends every animation
      this.z = 10;
      this.windows = {}; // key -> { node, open, z }
      this.volume = 62;
      this.track = 0;
      this.playing = false;
      this.position = 41;
      this.notepad = "Shopping list\n- Milk\n- Eggs\n- Bread\n- Apples\n- Coffee";
      this.notepadSaved = false;
      this.load = { cpu: 23, memory: 61 };
      this.active = "notepad";
      this.root = el("div", "dk");
      this.laptopRoot = el("div", "dk dk--laptop");
      this.mounted = false;
      this.build();
      this.buildLaptop();
      for (const key of ["edge", "explorer", "player", "notepad"]) this.show(key, false);
      this.renderNotepad();
      this.mount();
      this.tick();
    }

    get instant() { return !this.mounted || reduced.matches; }

    // --- mounting and scale -----------------------------------------------------------------------------
    mount() {
      const screen = document.getElementById("demo-desktop");
      const laptop = document.getElementById("demo-laptop");
      if (!screen || !laptop) return; // the setup page: the desktop stays off screen
      screen.append(this.root);
      laptop.append(this.laptopRoot);
      this.panel = document.getElementById("demo-pc");
      this.mounted = true;
      const fit = () => {
        this.root.style.transform = `scale(${screen.clientWidth / W})`;
        this.laptopRoot.style.transform = `scale(${laptop.clientWidth / LW})`;
      };
      fit();
      if (window.ResizeObserver) new ResizeObserver(fit).observe(screen.parentElement);
      if (window.ResizeObserver) new ResizeObserver(fit).observe(laptop.parentElement);
      window.addEventListener("resize", fit);
    }

    tick() {
      const now = new Date();
      const time = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      const date = now.toLocaleDateString("en-US");
      for (const clock of [this.clock, this.laptopClock]) {
        clock.time.textContent = time;
        clock.date.textContent = date;
      }
      if (this.playing) this.position = (this.position + 10) % TRACKS[this.track].length;
      this.renderPlayer();
      setTimeout(() => this.tick(), 10000);
    }

    // --- drawing ---------------------------------------------------------------------------------------------
    build() {
      const root = this.root;
      root.append(el("div", "dk-wall"));
      const icons = el("div", "dk-desk-icons");
      for (const [icon, label] of [["recycle", "Recycle Bin"], ["pdf", "Quarterly report.pdf"]]) {
        const item = el("div", "dk-desk-icon");
        item.append(icon === "recycle" ? svg('<svg viewBox="0 0 20 20"><path d="M5 6h10l-1 11H6z" fill="#cfe3f5" stroke="#5b87a8"/><path d="M4 6h12M8 6V4h4v2" stroke="#5b87a8"/></svg>') : svg(ICONS.pdf),
          el("span", "dk-desk-label", label));
        icons.append(item);
      }
      root.append(icons);
      this.layer = el("div", "dk-windows");
      root.append(this.layer);
      for (const key of Object.keys(APPS)) this.windows[key] = { node: this.makeWindow(key), open: false, z: 0 };

      // the taskbar
      const bar = el("div", "dk-taskbar");
      const apps = el("div", "dk-task-apps");
      apps.append(svg(ICONS.start, "dk-task-btn dk-task-start"));
      this.taskButtons = {};
      for (const key of PINNED) {
        const button = svg(ICONS[key], "dk-task-btn");
        button.dataset.app = key;
        this.taskButtons[key] = button;
        apps.append(button);
      }
      const alfred = svg(ICONS.alfred, "dk-task-btn is-open is-alfred");
      alfred.title = "Alfred";
      apps.append(alfred);
      bar.append(apps);
      const tray = el("div", "dk-tray");
      this.speaker = svg(ICONS.speaker, "dk-tray-icon");
      tray.append(svg(ICONS.wifi, "dk-tray-icon"), this.speaker);
      this.clock = { time: el("span", "dk-time"), date: el("span", "dk-date") };
      const clock = el("div", "dk-clock");
      clock.append(this.clock.time, this.clock.date);
      tray.append(clock);
      bar.append(tray);
      root.append(bar);

      // the volume flyout, notifications, screenshot flash, highlight box and pointer
      this.flyout = el("div", "dk-flyout");
      this.flyoutFill = el("span", "dk-flyout-fill");
      this.flyoutText = el("span", "dk-flyout-text");
      const track = el("span", "dk-flyout-track");
      track.append(this.flyoutFill);
      this.flyout.append(svg(ICONS.speaker, "dk-flyout-icon"), track, this.flyoutText);
      this.toasts = el("div", "dk-toasts");
      this.flashNode = el("div", "dk-flash");
      this.box = el("div", "dk-box");
      this.boxLabel = el("span", "dk-box-label");
      this.box.append(this.boxLabel);
      this.pointer = svg(ICONS.pointer, "dk-pointer");
      this.ripple = el("div", "dk-ripple");
      root.append(this.flyout, this.toasts, this.box, this.ripple, this.pointer, this.flashNode);
      this.pointerAt = { x: 560, y: 300 };
    }

    makeWindow(key) {
      const app = APPS[key];
      const node = el("section", "dk-win");
      node.dataset.app = key;
      const [x, y, w, h] = app.box;
      Object.assign(node.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
      const bar = el("header", "dk-titlebar");
      const title = el("span", "dk-title", app.title);
      const caps = el("span", "dk-caps");
      caps.append(el("span", null, "–"), el("span", null, "□"), el("span", null, "✕"));
      bar.append(svg(ICONS[key], "dk-win-icon"), title, caps);
      const body = el("div", `dk-body dk-body--${key}`);
      node.append(bar, body);
      node.title_ = title;
      this[`draw_${key}`](body);
      return node;
    }

    draw_notepad(body) {
      const menu = el("div", "np-menu");
      for (const item of ["File", "Edit", "View"]) menu.append(el("span", null, item));
      this.npText = el("div", "np-text");
      this.npCaret = el("span", "np-caret");
      const status = el("div", "np-status", "Ln 1, Col 1   100%   Windows (CRLF)   UTF-8");
      body.append(menu, this.npText, status);
      this.renderNotepad();
    }

    draw_player(body) {
      const art = el("div", "mp-art");
      art.append(svg('<svg viewBox="0 0 20 20"><path d="M8 4v9.2a2.6 2.6 0 1 0 1.5 2.3V7h5V4z" fill="#fff"/></svg>', "mp-note"));
      const info = el("div", "mp-info");
      this.mpTitle = el("strong", "mp-title");
      this.mpArtist = el("span", "mp-artist");
      this.mpBars = el("span", "mp-eq");
      for (let i = 0; i < 5; i += 1) this.mpBars.append(el("i"));
      info.append(this.mpTitle, this.mpArtist, this.mpBars);
      const time = el("div", "mp-time");
      this.mpBar = el("span", "mp-fill");
      const track = el("span", "mp-track");
      track.append(this.mpBar);
      this.mpPos = el("span", "mp-pos");
      this.mpLen = el("span", "mp-len");
      time.append(this.mpPos, track, this.mpLen);
      const controls = el("div", "mp-controls");
      this.mpPlay = svg(ICONS.play, "mp-btn mp-btn--play");
      controls.append(svg(ICONS.prev, "mp-btn"), this.mpPlay, svg(ICONS.next, "mp-btn"));
      const top = el("div", "mp-top");
      top.append(art, info);
      body.append(top, time, controls);
    }

    draw_explorer(body) {
      const address = el("div", "fx-address", "This PC  ›  Documents");
      const main = el("div", "fx-main");
      const side = el("ul", "fx-side");
      for (const item of ["Home", "Desktop", "Documents", "Downloads", "Pictures"]) {
        const li = el("li", item === "Documents" ? "is-current" : null, item);
        side.append(li);
      }
      const list = el("div", "fx-list");
      const head = el("div", "fx-row fx-head");
      head.append(el("span", null, "Name"), el("span", null, "Date modified"), el("span", null, "Size"));
      list.append(head);
      this.fxRows = {};
      for (const file of FILES) {
        const row = el("div", "fx-row");
        const name = el("span", "fx-name");
        name.append(svg(ICONS[file.kind], "fx-icon"), el("span", null, file.name));
        row.append(name, el("span", null, file.date), el("span", null, file.size));
        this.fxRows[file.name] = row;
        list.append(row);
      }
      main.append(side, list);
      body.append(address, main, el("div", "fx-status", `${FILES.length} items`));
    }

    draw_edge(body) {
      const tabs = el("div", "ed-tabs");
      tabs.append(el("span", "ed-tab", "Sign up - Contoso Events"));
      const address = el("div", "ed-address");
      address.append(el("span", "ed-lock", "●"), el("span", null, "events.contoso.com/meetup/sign-up"));
      const page = el("div", "ed-page");
      this.edForm = el("div", "ed-form");
      this.edForm.append(el("strong", "ed-h", "Aero Design Meetup"), el("span", "ed-sub", "Saturday, 7 pm · Raleigh, NC"));
      this.edFields = {};
      for (const [field, label] of [["name", "Full name"], ["email", "Email"]]) {
        const wrap = el("label", "ed-field");
        const input = el("span", "ed-input");
        wrap.append(el("span", "ed-label", label), input);
        this.edFields[field] = input;
        this.edForm.append(wrap);
      }
      const session = el("label", "ed-field");
      session.append(el("span", "ed-label", "Session"), el("span", "ed-input ed-select", "In person"));
      this.edSubmit = el("span", "ed-submit", "Submit");
      this.edForm.append(session, this.edSubmit);
      this.edDone = el("div", "ed-done");
      this.edDone.append(el("strong", null, "Thanks, Alex!"), el("span", null, "You're signed up for the Aero Design Meetup. See you Saturday."));
      this.edDone.hidden = true;
      page.append(this.edForm, this.edDone);
      body.append(tabs, address, page);
    }

    draw_outlook(body) {
      const rail = el("ul", "ol-folders");
      for (const [name, count] of [["Inbox", "2"], ["Drafts", ""], ["Sent Items", ""], ["Archive", ""]]) {
        const li = el("li", name === "Inbox" ? "is-current" : null);
        li.append(el("span", null, name), el("span", "ol-count", count));
        rail.append(li);
      }
      const list = el("div", "ol-list");
      this.olNew = el("span", "ol-new", "New mail");
      list.append(this.olNew);
      MAIL.forEach((mail, i) => {
        const item = el("div", `ol-item${i === 0 ? " is-current" : ""}`);
        item.append(el("strong", null, mail.from), el("span", "ol-subject", mail.subject), el("span", "ol-preview", mail.preview),
          el("span", "ol-time", mail.time));
        list.append(item);
      });
      const pane = el("div", "ol-pane");
      this.olRead = el("div", "ol-read");
      this.olRead.append(el("strong", "ol-read-subject", MAIL[0].subject), el("span", "ol-read-from", `${MAIL[0].from} · ${MAIL[0].time}`),
        el("p", "ol-read-body", MAIL[0].body));
      this.olCompose = el("div", "ol-compose");
      this.olSend = el("span", "ol-send", "Send");
      const head = el("div", "ol-compose-head");
      head.append(this.olSend, el("span", "ol-discard", "Discard"));
      this.olFields = {};
      for (const [field, label] of [["to", "To"], ["subject", "Subject"]]) {
        const row = el("div", "ol-field");
        const value = el("span", "ol-value");
        row.append(el("span", "ol-label", label), value);
        this.olFields[field] = value;
        this.olCompose.append(row);
      }
      this.olAttach = el("div", "ol-attach");
      this.olFields.body = el("div", "ol-body");
      this.olCompose.prepend(head);
      this.olCompose.append(this.olAttach, this.olFields.body);
      this.olCompose.hidden = true;
      pane.append(this.olRead, this.olCompose);
      body.append(rail, list, pane);
    }

    draw_taskmgr(body) {
      const head = el("div", "tm-head");
      this.tmCpu = el("span", "tm-stat");
      this.tmMem = el("span", "tm-stat");
      head.append(el("strong", null, "Processes"), this.tmCpu, this.tmMem);
      this.tmRows = el("div", "tm-rows");
      body.append(head, this.tmRows);
      this.renderTasks();
    }

    buildLaptop() {
      const root = this.laptopRoot;
      root.append(el("div", "dk-wall dk-wall--laptop"));
      this.lpWin = el("section", "dk-win is-active");
      Object.assign(this.lpWin.style, { left: "36px", top: "28px", width: "420px", height: "250px" });
      const bar = el("header", "dk-titlebar");
      bar.append(svg(ICONS.explorer, "dk-win-icon"), el("span", "dk-title", "Downloads - File Explorer"));
      const body = el("div", "dk-body dk-body--explorer");
      body.append(el("div", "fx-address", "This PC  ›  Downloads"));
      this.lpList = el("div", "fx-list fx-list--wide");
      const head = el("div", "fx-row fx-head");
      head.append(el("span", null, "Name"), el("span", null, "Date modified"), el("span", null, "Size"));
      this.lpList.append(head);
      for (const file of [{ name: "Boarding pass.pdf", kind: "pdf", date: "10/6/2026 8:40 PM", size: "212 KB" },
        { name: "Holiday photos.zip", kind: "zip", date: "9/30/2026 6:05 PM", size: "84 MB" }]) {
        this.lpList.append(this.fileRow(file));
      }
      body.append(this.lpList);
      this.lpWin.append(bar, body);
      root.append(this.lpWin);

      // Alfred's approval on the laptop: the same words as the Alfred window there
      this.lpAsk = el("div", "lp-ask");
      const sheet = el("div", "lp-sheet");
      const brand = el("div", "lp-brand");
      brand.append(svg(ICONS.alfred, "lp-logo"), el("span", null, "Alfred on Laptop"));
      this.lpReason = el("p", "lp-reason");
      this.lpBar = el("div", "lp-bar");
      const countdown = el("div", "lp-countdown");
      countdown.append(this.lpBar);
      this.lpDeny = el("button", "lp-btn", "Deny");
      this.lpAllow = el("button", "lp-btn lp-btn--allow", "Allow");
      this.lpDeny.type = this.lpAllow.type = "button";
      const actions = el("div", "lp-actions");
      actions.append(this.lpDeny, this.lpAllow);
      sheet.append(brand, el("strong", "lp-h", "Alfred wants to"), this.lpReason,
        el("p", "lp-from", "Another of your PCs wants to do this on this PC."), countdown, actions);
      this.lpAsk.append(sheet);
      this.lpAsk.hidden = true;
      this.lpToast = el("div", "dk-toasts");
      this.lpPointer = svg(ICONS.pointer, "dk-pointer");
      root.append(this.lpAsk, this.lpToast, this.lpPointer);

      const bar2 = el("div", "dk-taskbar");
      const apps = el("div", "dk-task-apps");
      apps.append(svg(ICONS.start, "dk-task-btn"), svg(ICONS.explorer, "dk-task-btn is-open is-active"), svg(ICONS.edge, "dk-task-btn"),
        svg(ICONS.alfred, "dk-task-btn is-open"));
      const tray = el("div", "dk-tray");
      this.laptopClock = { time: el("span", "dk-time"), date: el("span", "dk-date") };
      const clock = el("div", "dk-clock");
      clock.append(this.laptopClock.time, this.laptopClock.date);
      tray.append(svg(ICONS.wifi, "dk-tray-icon"), clock);
      bar2.append(apps, tray);
      root.append(bar2);
    }

    fileRow(file) {
      const row = el("div", "fx-row");
      const name = el("span", "fx-name");
      name.append(svg(ICONS[file.kind], "fx-icon"), el("span", null, file.name));
      row.append(name, el("span", null, file.date), el("span", null, file.size));
      return row;
    }

    // --- state shown ---------------------------------------------------------------------------------------------
    renderNotepad(shown) {
      const text = shown === undefined ? this.notepad : shown;
      this.npText.textContent = text;
      this.npText.append(this.npCaret);
      const title = this.notepad ? `${this.notepadSaved ? "" : "*"}Untitled - Notepad` : "Untitled - Notepad";
      this.setTitle("notepad", title);
    }

    renderPlayer() {
      const track = TRACKS[this.track];
      this.mpTitle.textContent = track.title;
      this.mpArtist.textContent = track.artist;
      const time = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      this.mpPos.textContent = time(this.position);
      this.mpLen.textContent = time(track.length);
      this.mpBar.style.width = `${(100 * this.position) / track.length}%`;
      this.mpPlay.innerHTML = this.playing ? ICONS.pause : ICONS.play;
      this.windows.player.node.classList.toggle("is-playing", this.playing);
      this.setTitle("player", this.playing ? `${track.title} - Media Player` : "Media Player");
    }

    renderTasks() {
      const jitter = () => (Math.random() - 0.5) * 2;
      this.rows = PROCESSES.map(([name, cpu, mem]) => [name, Math.max(0, cpu + jitter()), mem + Math.round(jitter() * 12)]);
      this.load = { cpu: Math.round(20 + Math.random() * 9), memory: Math.round(58 + Math.random() * 6) };
      this.tmCpu.textContent = `CPU ${this.load.cpu}%`;
      this.tmMem.textContent = `Memory ${this.load.memory}%`;
      this.tmRows.textContent = "";
      const head = el("div", "tm-row tm-row--head");
      head.append(el("span", null, "Name"), el("span", null, "CPU"), el("span", null, "Memory"));
      this.tmRows.append(head);
      for (const [name, cpu, mem] of this.rows) {
        const row = el("div", "tm-row");
        row.append(el("span", null, name), el("span", null, `${cpu.toFixed(1)}%`), el("span", null, `${mem.toLocaleString("en-US")} MB`));
        this.tmRows.append(row);
      }
    }

    setTitle(key, title) {
      const node = this.windows[key] && this.windows[key].node;
      if (node) node.title_.textContent = title;
    }

    // --- windows -------------------------------------------------------------------------------------------------
    // An app by the name it has in the Start menu (open_app), or an open window by its title too
    // (close_window, focus_window).
    findApp(query) {
      const q = norm(query).replace(/^(the|my) /, "").replace(/ (app|application)$/, "");
      if (!q) return null;
      for (const [key, app] of Object.entries(APPS)) if (app.aliases.includes(q)) return key;
      for (const [key, app] of Object.entries(APPS)) if (app.aliases.some((alias) => q.includes(alias))) return key;
      return null;
    }

    find(query) {
      const q = norm(query).replace(/^(the|my) /, "").replace(/ (app|window|application)$/, "");
      if (!q) return null;
      for (const [key, app] of Object.entries(APPS)) if (app.aliases.includes(q)) return key;
      for (const [key] of Object.entries(APPS)) if (this.windows[key].open && norm(this.title(key)).includes(q)) return key;
      return this.findApp(q);
    }
    name(key) { return APPS[key].name; }
    apps() { return Object.values(APPS).map((app) => app.name); }
    title(key) { return this.windows[key].node.title_.textContent; }
    isOpen(key) { return this.windows[key].open; }

    show(key, animate = true) {
      const win = this.windows[key];
      if (!win.open) {
        win.open = true;
        this.layer.append(win.node);
        if (animate && !this.instant) {
          win.node.classList.add("is-opening");
          setTimeout(() => win.node.classList.remove("is-opening"), 260);
        }
      }
      this.front(key);
    }

    front(key) {
      const win = this.windows[key];
      this.z += 1;
      win.z = this.z;
      win.node.style.zIndex = String(this.z);
      this.active = key;
      for (const [other, w] of Object.entries(this.windows)) w.node.classList.toggle("is-active", other === key);
      for (const [other, button] of Object.entries(this.taskButtons)) {
        button.classList.toggle("is-open", this.windows[other].open);
        button.classList.toggle("is-active", other === key);
      }
    }

    async open(key) {
      this.show(key, true);
      await wait(this.instant ? 60 : 300);
    }

    async focus(key) {
      if (!this.windows[key].open) return this.open(key);
      this.front(key);
      await wait(this.instant ? 40 : 160);
    }

    close(key) {
      const win = this.windows[key];
      if (!win.open) return false;
      win.open = false;
      win.node.remove();
      const left = Object.entries(this.windows).filter(([, w]) => w.open).sort((a, b) => b[1].z - a[1].z);
      if (left.length) this.front(left[0][0]);
      else this.active = null;
      for (const [other, button] of Object.entries(this.taskButtons)) button.classList.toggle("is-open", this.windows[other].open);
      return true;
    }

    // The open windows, front to back (as EnumWindows lists them), with Alfred's own window first.
    list() {
      const open = Object.entries(this.windows).filter(([, w]) => w.open).sort((a, b) => b[1].z - a[1].z);
      return [{ key: "alfred", title: "Alfred", process: "Alfred.exe", active: true },
        ...open.map(([key]) => ({ key, title: this.title(key), process: APPS[key].process, active: false }))];
    }

    // --- Notepad ---------------------------------------------------------------------------------------------------
    // Notepad keeps what was typed: after a Stop half way, only the letters that made it (what Alfred
    // reports when it looks at the screen).
    async typeNotepad(text, perChar = 22) {
      const gen = this.gen;
      this.notepadSaved = false;
      if (this.instant) {
        this.notepad = text;
        this.renderNotepad();
        return;
      }
      this.npText.classList.add("is-typing");
      const step = Math.max(1, Math.ceil(text.length / 90)); // long text: a few letters at a time
      for (let i = 0; i <= text.length && gen === this.gen; i += step) {
        this.notepad = text.slice(0, i);
        this.renderNotepad();
        await wait(perChar);
      }
      if (gen === this.gen) {
        this.notepad = text;
        this.renderNotepad();
      }
      this.npText.classList.remove("is-typing");
    }

    // --- music and volume ------------------------------------------------------------------------------------------
    nowPlaying() { return { ...TRACKS[this.track], playing: this.playing }; }

    async media(action, times = 1) {
      if (action === "play_pause") this.playing = !this.playing;
      else if (action === "next" || action === "previous") {
        this.track = (this.track + (action === "next" ? 1 : TRACKS.length - 1)) % TRACKS.length;
        this.position = 0;
        this.playing = true;
      } else if (action === "mute") {
        this.muted = !this.muted;
        await this.showVolume();
      } else if (action === "volume_up" || action === "volume_down") {
        this.volume = Math.max(0, Math.min(100, this.volume + (action === "volume_up" ? 2 : -2) * times));
        this.muted = false;
        await this.showVolume();
      }
      this.renderPlayer();
      await wait(this.instant ? 40 : 220);
    }

    async setVolume(percent) {
      this.volume = percent;
      this.muted = false;
      await this.showVolume();
    }

    async showVolume() {
      const gen = this.gen;
      this.flyoutFill.style.width = `${this.muted ? 0 : this.volume}%`;
      this.flyoutText.textContent = this.muted ? "Muted" : String(this.volume);
      this.flyout.classList.add("is-shown");
      clearTimeout(this.flyoutTimer);
      this.flyoutTimer = setTimeout(() => this.flyout.classList.remove("is-shown"), 2200 / SPEED);
      await wait(this.instant ? 60 : 420);
      if (gen !== this.gen) this.flyout.classList.remove("is-shown");
    }

    // --- screenshots, notifications ------------------------------------------------------------------------------------
    async flash() {
      if (!this.mounted) return;
      this.flashNode.classList.remove("is-flashing");
      void this.flashNode.offsetWidth;
      this.flashNode.classList.add("is-flashing");
      await wait(this.instant ? 80 : 380);
    }

    toast(title, text, where = "desktop") {
      const box = where === "laptop" ? this.lpToast : this.toasts;
      const toast = el("div", "dk-toast");
      const head = el("div", "dk-toast-head");
      head.append(svg(ICONS.alfred, "dk-toast-icon"), el("strong", null, title));
      toast.append(head, el("span", null, text));
      box.append(toast);
      setTimeout(() => toast.classList.add("is-leaving"), 5200 / SPEED);
      setTimeout(() => toast.remove(), 5600 / SPEED);
    }

    // --- File Explorer and Task Manager --------------------------------------------------------------------------------------
    async selectFile(name) {
      for (const [file, row] of Object.entries(this.fxRows)) row.classList.toggle("is-selected", file === name);
      await wait(this.instant ? 40 : 240);
    }

    explorerText() {
      return `Documents\nName | Date modified | Size\n${FILES.map((f) => `${f.name} | ${f.date} | ${f.size}`).join("\n")}`;
    }

    taskManagerText() {
      const rows = [...this.rows].sort((a, b) => b[1] - a[1]);
      return `Processes  CPU ${this.load.cpu}%  Memory ${this.load.memory}%\n${rows.map(([name, cpu, mem]) =>
        `${name} | ${cpu.toFixed(1)}% | ${mem.toLocaleString("en-US")} MB`).join("\n")}`;
    }

    busiest() {
      return [...this.rows].sort((a, b) => b[1] - a[1]).slice(0, 2);
    }

    // --- Outlook --------------------------------------------------------------------------------------------------------------
    async compose() {
      this.olRead.hidden = true;
      this.olCompose.hidden = false;
      for (const field of Object.values(this.olFields)) field.textContent = "";
      this.olAttach.textContent = "";
      this.olSend.classList.remove("is-sending", "is-target");
      this.olSending = false;
      this.setTitle("outlook", "Untitled - Message - Outlook");
      await wait(this.instant ? 40 : 260);
    }

    async mailField(field, text) {
      const gen = this.gen;
      const node = this.olFields[field];
      node.classList.add("is-typing");
      if (this.instant) node.textContent = text;
      else {
        const step = Math.max(1, Math.ceil(text.length / 40));
        for (let i = 0; i <= text.length && gen === this.gen; i += step) {
          node.textContent = text.slice(0, i);
          await wait(16);
        }
        if (gen === this.gen) node.textContent = text; // after a Stop, only what was typed
      }
      node.classList.remove("is-typing");
      if (field === "subject" && gen === this.gen) this.setTitle("outlook", `${text} - Message - Outlook`);
    }

    async attach(name, size) {
      const chip = el("span", "ol-chip");
      chip.append(svg(ICONS.pdf, "ol-chip-icon"), el("span", null, `${name}  ${size}`));
      this.olAttach.append(chip);
      await wait(this.instant ? 40 : 260);
    }

    pointAtSend(on) { this.olSend.classList.toggle("is-target", on); }

    async sendMail() {
      const gen = this.gen;
      this.olSend.classList.remove("is-target");
      this.olSend.classList.add("is-sending");
      await wait(this.instant ? 80 : 700);
      if (gen !== this.gen) return;
      this.olCompose.hidden = true;
      this.olRead.hidden = false;
      this.setTitle("outlook", APPS.outlook.title);
      this.toast("Outlook", "Message sent to Sam Rivera.");
    }

    // --- the pointer (computer use) ---------------------------------------------------------------------------------------------
    // Where a form field or button is, in desktop pixels and in screenshot pixels (what Alfred clicks).
    point(target) {
      const node = { name: this.edFields.name, email: this.edFields.email, submit: this.edSubmit }[target];
      const fallback = { name: [520, 194], email: [520, 238], submit: [366, 318] }[target];
      let x = fallback[0];
      let y = fallback[1];
      if (this.mounted && node.offsetParent) {
        const box = node.getBoundingClientRect();
        const desk = this.root.getBoundingClientRect();
        const scale = desk.width / W;
        if (scale > 0 && box.width > 0) {
          x = Math.round((box.left + box.width / 2 - desk.left) / scale);
          y = Math.round((box.top + box.height / 2 - desk.top) / scale);
        }
      }
      return { x, y, shot: [Math.round(x * SHOT), Math.round(y * SHOT)], node };
    }

    async moveTo(x, y) {
      const gen = this.gen;
      this.pointer.classList.add("is-shown");
      const from = this.pointerAt;
      this.pointerAt = { x, y };
      if (this.instant) {
        this.pointer.style.transform = `translate(${x}px, ${y}px)`;
        return;
      }
      const steps = 14;
      for (let i = 1; i <= steps && gen === this.gen; i += 1) {
        const t = i / steps;
        const ease = t * t * (3 - 2 * t);
        this.pointer.style.transform = `translate(${from.x + (x - from.x) * ease}px, ${from.y + (y - from.y) * ease}px)`;
        await wait(24);
      }
    }

    async clickAt(x, y) {
      this.ripple.style.transform = `translate(${x}px, ${y}px)`;
      this.ripple.classList.remove("is-clicking");
      void this.ripple.offsetWidth;
      this.ripple.classList.add("is-clicking");
      await wait(this.instant ? 40 : 200);
    }

    target(x, y, label) {
      if (x === null) {
        this.box.classList.remove("is-shown");
        return;
      }
      this.box.style.transform = `translate(${x - 70}px, ${y - 16}px)`;
      this.boxLabel.textContent = label;
      this.box.classList.add("is-shown");
    }

    async formType(field, text) {
      const gen = this.gen;
      const node = this.edFields[field];
      for (const other of Object.values(this.edFields)) other.classList.toggle("is-focused", other === node);
      if (this.instant) node.textContent = text;
      else {
        for (let i = 0; i <= text.length && gen === this.gen; i += 1) {
          node.textContent = text.slice(0, i);
          await wait(28);
        }
      }
    }

    formFocus(field) {
      for (const [name, node] of Object.entries(this.edFields)) node.classList.toggle("is-focused", name === field);
    }

    formFilled() { return Boolean(this.edFields.name.textContent && this.edFields.email.textContent); }

    async submitForm() {
      const gen = this.gen;
      this.edSubmit.classList.add("is-pressed");
      await wait(this.instant ? 60 : 450);
      if (gen !== this.gen) return;
      this.edForm.hidden = true;
      this.edDone.hidden = false;
      this.setTitle("edge", "Thanks for signing up - Contoso Events - Microsoft Edge");
    }

    resetForm() {
      for (const node of Object.values(this.edFields)) {
        node.textContent = "";
        node.classList.remove("is-focused");
      }
      this.edSubmit.classList.remove("is-pressed");
      this.edForm.hidden = false;
      this.edDone.hidden = true;
      this.setTitle("edge", APPS.edge.title);
    }

    hidePointer() {
      this.pointer.classList.remove("is-shown");
      this.target(null);
    }

    // --- the laptop -------------------------------------------------------------------------------------------------------------------
    // On narrow screens only one PC shows: the laptop comes forward while something happens there. With
    // `after` (ms), it changes then, unless something else is shown first.
    showMachine(name, after = 0) {
      clearTimeout(this.machineTimer);
      if (after) this.machineTimer = setTimeout(() => this.showMachine(name), after / SPEED);
      else if (this.panel) this.panel.dataset.focus = name;
    }

    // Alfred on the laptop asks its user. The viewer may answer; otherwise the laptop's user allows it.
    ask(reason) {
      const gen = this.gen;
      this.lpReason.textContent = reason;
      this.lpAsk.hidden = false;
      // On a short screen the laptop may be below the panel's fold: bring it into view.
      const machine = this.laptopRoot.closest(".demo-machine");
      if (this.mounted && machine) machine.scrollIntoView({ block: "nearest", behavior: this.instant ? "auto" : "smooth" });
      this.lpAllow.disabled = this.lpDeny.disabled = false;
      this.lpBar.style.transition = "none";
      this.lpBar.style.width = "100%";
      void this.lpBar.offsetWidth;
      this.lpBar.style.transition = `width ${60 / SPEED}s linear`;
      this.lpBar.style.width = "0%";
      return new Promise((resolve) => {
        let done = false;
        const finish = (yes) => {
          if (done) return;
          done = true;
          this.lpAllow.disabled = this.lpDeny.disabled = true;
          setTimeout(() => { this.lpAsk.hidden = true; this.lpPointer.classList.remove("is-shown"); }, (yes ? 350 : 250) / SPEED);
          resolve(yes);
        };
        this.lpAllow.onclick = () => finish(true);
        this.lpDeny.onclick = () => finish(false);
        this.lpCancel = () => finish(false);
        (async () => { // the laptop's user reads it, then presses Allow
          await wait(1200);
          if (done || gen !== this.gen) return;
          const button = this.lpAllow;
          let x = 470;
          let y = 286;
          if (this.mounted && button.offsetParent) {
            const box = button.getBoundingClientRect();
            const screen = this.laptopRoot.getBoundingClientRect();
            const scale = screen.width / LW;
            if (scale > 0 && box.width) {
              x = (box.left + box.width / 2 - screen.left) / scale;
              y = (box.top + box.height / 2 - screen.top) / scale;
            }
          }
          this.lpPointer.classList.add("is-shown");
          this.lpPointer.style.transform = `translate(${x + 60}px, ${y + 60}px)`;
          await wait(80);
          this.lpPointer.style.transform = `translate(${x}px, ${y}px)`;
          await wait(this.instant ? 100 : 1100);
          if (done || gen !== this.gen) return;
          button.classList.add("is-pressed");
          await wait(160);
          button.classList.remove("is-pressed");
          finish(true);
        })();
      });
    }

    async receive(file) {
      const gen = this.gen;
      this.toast("Alfred", `Receiving ${file.name} from Desktop…`, "laptop");
      await wait(this.instant ? 100 : 900);
      if (gen !== this.gen) return;
      const row = this.fileRow(file);
      row.classList.add("is-new", "is-selected");
      this.lpList.querySelector(".fx-head").after(row);
      this.toast("Alfred", `Saved ${file.name} in Downloads.`, "laptop");
    }

    // --- Stop -------------------------------------------------------------------------------------------------------------------------------
    // Alfred stopped: every animation ends where it is, the pointer goes away. What was done stays done.
    halt() {
      this.gen += 1;
      this.hidePointer();
      this.olSend.classList.remove("is-target", "is-sending");
      this.npText.classList.remove("is-typing");
      if (this.lpCancel) this.lpCancel();
      this.lpCancel = null;
    }
  }

  Demo.desktop = new Desktop();
})();
