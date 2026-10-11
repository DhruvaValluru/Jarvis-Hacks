"use strict";
// Alfred's web demo: what Alfred does with a command. No AI runs here. Simple commands take the fast
// path, as in windows-mvp/jarvis/router.py (no "thinking", just the tool and its answer). Anything else
// is matched to one of a few example tasks by forgiving keyword rules and played the way the real
// engine reports a run: "thinking...", one status line per tool call with the real tool names and
// arguments, approvals before anything that sends or submits, then the reply. demo-api.js turns these
// into the app's events and activity log; desktop.js shows them on the simulated PC.

(() => {
  const Demo = window.AlfredDemo = window.AlfredDemo || {};
  const DOCS = "C:\\Users\\Alex\\Documents";
  const REPORT = { name: "Quarterly report.pdf", kind: "pdf", size: "2.4 MB" };
  const SAM = "sam.rivera@contoso.com";
  const DECLINED = "The user declined this action. Do not retry it; ask what they want instead.";
  const EXAMPLES = [
    "Email Sam the quarterly report", "What's on my screen?", "Play some music and set the volume to 30%",
    "Copy the quarterly report to my laptop", "How is my PC doing?", "Fill in the sign-up form in Edge and click Submit",
    "Remind me to stretch in 10 seconds", "Open Notepad and type: Call Sam about the report at 3 pm",
  ];
  const pc = () => Demo.desktop;
  const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
  const pad = (n) => String(n).padStart(2, "0");

  // --- the fast path (router.py) ----------------------------------------------------------------------
  const WAKE = /^\s*(hey|ok|okay|yo)?[\s,]*(alfred|jarvis)\b[\s,.!?]*/i;
  const COMPOUND = /\b(and|then|after that|also)\b/i;
  const trim = (text) => text.replace(/^[\s.!?,]+|[\s.!?,]+$/g, "");

  function clean(text) {
    const out = trim(text.replace(WAKE, "").replace(/\b(please|can you|could you|would you)\b/gi, ""));
    return out.replace(/\s+/g, " ").trim();
  }
  function single(arg) {
    const out = trim(arg);
    return !out || COMPOUND.test(out) ? null : out;
  }

  function route(text) {
    const t = clean(text).toLowerCase();
    let m;
    if (!t) return null;
    if ((m = /^(?:open|launch|start|run) (.+)$/.exec(t))) {
      const arg = single(m[1]);
      if (arg === null) return null;
      if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(arg)) return ["open_url", { url: arg }];
      return ["open_app", { name: arg }];
    }
    if ((m = /^(?:close|quit|exit) (.+)$/.exec(t))) {
      const arg = single(m[1]);
      return arg ? ["close_window", { window: arg }] : null;
    }
    if ((m = /^(?:switch to|focus(?: on)?|go to|show me) (.+)$/.exec(t))) {
      const arg = single(m[1]);
      return arg ? ["focus_window", { window: arg }] : null;
    }
    if (/^(?:(?:play|pause|resume|stop)(?: (?:the )?(?:music|song|video|it))?|play music)$/.test(t)) return ["media_control", { action: "play_pause" }];
    if (/^(?:(?:next|skip)(?: (?:the )?(?:song|track|one|video))?|skip (?:this|it))$/.test(t)) return ["media_control", { action: "next" }];
    if (/^(?:previous|last|go back)(?: (?:a )?(?:song|track|one))?$/.test(t)) return ["media_control", { action: "previous" }];
    if (/^(?:un)?mute(?: (?:the )?(?:sound|audio|volume|it))?$/.test(t)) return ["media_control", { action: "mute" }];
    if ((m = /^(?:set (?:the )?)?volume (?:to |at )?(\d{1,3})(?: ?%| percent)?$/.exec(t))) return ["set_volume", { percent: Number(m[1]) }];
    if (/^(?:turn (?:it|the volume|the sound) up|volume up|louder|turn up the volume)$/.test(t)) return ["media_control", { action: "volume_up", times: 5 }];
    if (/^(?:turn (?:it|the volume|the sound) down|volume down|quieter|turn down the volume)$/.test(t)) return ["media_control", { action: "volume_down", times: 5 }];
    if ((m = /^(?:say|announce) (.+)$/.exec(t))) return ["say", { text: m[1] }];
    if (/^(?:what(?:'s| is) (?:the )?(?:time|date|day)(?: is it)?(?: today)?|what time is it|what day is it)$/.test(t)) return ["system_status", {}];
    if (/^(?:what(?:'s| is) (?:open|running)|list (?:my )?windows)$/.test(t)) return ["list_windows", {}];
    if ((m = /^(?:set a )?timer (?:for )?(\d+) (second|minute|hour)s?$/.exec(t))) {
      const mult = { second: 1, minute: 60, hour: 3600 }[m[2]];
      return ["set_timer", { seconds: Number(m[1]) * mult, message: "Your timer is done." }];
    }
    return null;
  }

  // The tools the fast path can run on the simulated PC. null: not here, so Claude (the scenarios) has a go.
  const FAST = {
    async open_app(r, args) {
      const key = pc().findApp(args.name);
      if (!key) return null;
      const out = `Opened ${pc().name(key)}.`;
      await r.tool("open_app", args, async () => { await pc().open(key); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async close_window(r, args) {
      const key = pc().find(args.window);
      if (!key || !pc().isOpen(key)) return null;
      const out = `Asked "${pc().title(key)}" to close.`;
      await r.tool("close_window", args, async () => { pc().close(key); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async focus_window(r, args) {
      const key = pc().find(args.window);
      if (!key || !pc().isOpen(key)) return null;
      const out = `Switched to "${pc().title(key)}".`;
      await r.tool("focus_window", args, async () => { await pc().focus(key); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async media_control(r, args) {
      const out = `${args.action.replace(/_/g, " ")} done.`;
      await r.tool("media_control", args, async () => { await pc().media(args.action, args.times || 1); return out; },
        { status: false, lead: 120, after: 0 });
      return out;
    },
    async set_volume(r, args) {
      const percent = Math.max(0, Math.min(100, args.percent));
      const out = `Volume set to ${percent}%.`;
      await r.tool("set_volume", args, async () => { await pc().setVolume(percent); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async say(r, args) {
      await r.tool("say", args, async () => { pc().toast("Alfred, out loud", args.text); return "Said it."; }, { status: false, lead: 120, after: 0 });
      return "Said it.";
    },
    async system_status(r, args) {
      let out = "";
      await r.tool("system_status", args, async () => { out = statusText(); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async list_windows(r, args) {
      let out = "";
      await r.tool("list_windows", args, async () => { out = windowList(); return out; }, { status: false, lead: 120, after: 0 });
      return out;
    },
    async set_timer(r, args) {
      const out = timerText(args.seconds);
      await r.tool("set_timer", args, async () => out, { status: false, lead: 120, after: 0 });
      reminder(r, args.seconds, args.message);
      return out;
    },
  };

  // system_status: "It's Saturday 11 October 2026, 03:42 PM. CPU 23%, memory 61% used." (no battery: a desktop)
  function statusText() {
    const now = new Date();
    const day = now.toLocaleDateString("en-GB", { weekday: "long" });
    const month = now.toLocaleDateString("en-GB", { month: "long" });
    const hour = now.getHours() % 12 || 12;
    pc().renderTasks();
    return `It's ${day} ${pad(now.getDate())} ${month} ${now.getFullYear()}, ${pad(hour)}:${pad(now.getMinutes())} `
      + `${now.getHours() < 12 ? "AM" : "PM"}. CPU ${pc().load.cpu}%, memory ${pc().load.memory}% used.`;
  }

  // list_windows: "Open windows (* = active):" then "* Title  [process.exe]" per window.
  function windowList() {
    return `Open windows (* = active):\n${pc().list().map((w) => `${w.active ? "* " : "  "}${w.title}  [${w.process}]`).join("\n")}`;
  }

  function timerText(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `Timer set for ${mins ? `${mins} min ${secs} s` : `${secs} s`}.`;
  }

  // set_timer's reminder. Long timers come sooner in the demo, and the log says so.
  function reminder(r, seconds, message) {
    const delay = seconds <= 20 ? seconds : 8;
    if (delay !== seconds) r.notice(`Demo time: this reminder comes in ${delay} seconds instead of ${humanTime(seconds)}.`);
    r.later(delay, () => {
      r.announce(`Reminder: ${message}`);
      pc().toast("Alfred", `Reminder: ${message}`);
    });
  }

  function humanTime(seconds) {
    if (seconds % 3600 === 0) return seconds === 3600 ? "an hour" : `${seconds / 3600} hours`;
    if (seconds % 60 === 0) return seconds === 60 ? "a minute" : `${seconds / 60} minutes`;
    return seconds === 1 ? "a second" : `${seconds} seconds`;
  }

  // --- example tasks (what Claude would do) ---------------------------------------------------------------------
  async function notepad(r, said) {
    const wanted = noteFor(said);
    await r.think();
    await r.tool("open_app", { name: "Notepad" }, async () => { await pc().open("notepad"); return "Opened Notepad."; });
    await r.tool("ui_set_text", { window: "Notepad", text: wanted.text }, async () => {
      await pc().typeNotepad(wanted.text);
      return 'Set the text of Document "Text editor".';
    });
    r.reply(wanted.literal ? `Done. Notepad is open and says: “${clip(wanted.text, 160)}”`
      : `Done. Notepad is open with ${wanted.label}: ${wanted.items}.`);
  }

  const LISTS = {
    todo: ["a to-do list", "To-do", ["Send Sam the quarterly report", "Book the dentist", "Water the plants", "Call Mom"]],
    packing: ["a packing list", "Packing list", ["Passport", "Phone charger", "Toothbrush", "Rain jacket", "Snacks"]],
    shopping: ["a shopping list", "Shopping list", ["Bananas", "Rice", "Spinach", "Olive oil", "Dark chocolate"]],
  };
  function noteFor(said) {
    const quoted = /["“]([^"”]+)["”]/.exec(said);
    if (quoted) return { literal: true, text: quoted[1].trim() };
    const m = /\b(?:type|write|saying|that says|put|jot down|jot|note down|with the text)\b\s*:?\s*(.+)$/i.exec(said);
    const asked = m ? trim(m[1]) : "";
    if (asked && !/^(?:me )?(?:a|an|my|some|the|something|anything|stuff|it)\b/i.test(asked)) return { literal: true, text: asked };
    if (/\bhaiku|poem\b/i.test(said)) {
      return { literal: true, text: "Blue sky through the glass,\nAlfred types while you sit back.\nOne STOP, and it rests." };
    }
    const kind = /to-?do/i.test(said) ? "todo" : /packing|pack/i.test(said) ? "packing" : "shopping";
    const [label, heading, items] = LISTS[kind];
    const text = `${heading}\n${items.map((item) => `- ${item}`).join("\n")}`;
    return { literal: false, text, label, items: `${items.slice(0, -1).join(", ").toLowerCase()} and ${items[items.length - 1].toLowerCase()}` };
  }

  async function music(r, said, low) {
    const p = pc();
    await r.think();
    if (!p.isOpen("player")) {
      await r.tool("open_app", { name: "Media Player" }, async () => { await p.open("player"); return "Opened Media Player."; });
    }
    const media = (action, times) => r.tool("media_control", times ? { action, times } : { action },
      async () => { await p.media(action, times || 1); return `${action.replace(/_/g, " ")} done.`; });
    const volume = /(\d{1,3})\s*(?:%|percent)/.exec(low) || (/\bvolume\b/.test(low) && /\b(?:to|at)\s+(\d{1,3})\b/.exec(low));
    const pause = /\b(pause|stop)\b/.test(low) && !/\bplay\b/.test(low);
    if (/\b(next|skip)\b/.test(low)) await media("next");
    else if (/\b(previous|last song|go back)\b/.test(low)) await media("previous");
    else if (pause && p.playing) await media("play_pause");
    else if (!pause && /\b(play|music|song|songs|resume|put on|playing)\b/.test(low) && !p.playing) await media("play_pause");
    if (volume) {
      const percent = Math.max(0, Math.min(100, Number(volume[1])));
      await r.tool("set_volume", { percent }, async () => { await p.setVolume(percent); return `Volume set to ${percent}%.`; });
    } else if (/\b(louder|turn (?:it |the volume )?up|volume up|increase)\b/.test(low)) await media("volume_up", 5);
    else if (/\b(quieter|softer|turn (?:it |the volume )?down|volume down|lower|decrease)\b/.test(low)) await media("volume_down", 5);
    else if (/\b(un)?mute\b/.test(low)) await media("mute");
    const now = p.nowPlaying();
    const level = p.muted ? "the sound is muted" : `the volume is at ${p.volume}%`;
    r.reply(now.playing ? `Playing “${now.title}” by ${now.artist}, and ${level}.` : `The music is paused, and ${level}.`);
  }

  async function health(r) {
    const p = pc();
    await r.think();
    await r.tool("system_status", {}, async () => statusText());
    await r.tool("open_app", { name: "Task Manager" }, async () => { await p.open("taskmgr"); return "Opened Task Manager."; });
    await r.tool("ui_read_text", { window: "Task Manager" }, async () => p.taskManagerText());
    const [first, second] = p.busiest();
    const gb = (mb) => (mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
    r.reply(`Your PC is doing fine. CPU is at ${p.load.cpu}% and memory at ${p.load.memory}%. The busiest app is `
      + `${first[0]} (${first[1].toFixed(1)}% CPU, ${gb(first[2])} of memory), then ${second[0]} (${second[1].toFixed(1)}% CPU). Nothing looks stuck.`);
  }

  async function screen(r) {
    const p = pc();
    await r.think();
    await r.tool("list_windows", {}, async () => windowList());
    await r.tool("screenshot", {}, async () => { await p.flash(); return ""; });
    r.reply(describeScreen());
  }

  function describeScreen() {
    const p = pc();
    const shown = p.list().filter((w) => w.key !== "alfred").map((w) => describe(w.key));
    if (!shown.length) return "Only my window is open. The rest of the desktop is empty.";
    if (shown.length === 1) return `Besides my window, only ${shown[0]} is open.`;
    const rest = shown.slice(1);
    const behind = rest.length === 1 ? rest[0] : `${rest.slice(0, -1).join("; ")}; and ${rest[rest.length - 1]}`;
    return `Besides my window, you have ${shown.length} windows open. In front is ${shown[0]}. Behind it: ${behind}.`;
  }

  function describe(key) {
    const p = pc();
    if (key === "notepad") {
      const first = p.notepad.split("\n")[0].trim();
      if (!first) return "Notepad, empty";
      return /list/i.test(first) ? `Notepad, with your ${first.toLowerCase()}` : `Notepad, with the note “${clip(first, 50)}”`;
    }
    if (key === "player") {
      const now = p.nowPlaying();
      return now.playing ? `Media Player, playing “${now.title}” by ${now.artist}` : `Media Player, paused on “${now.title}”`;
    }
    if (key === "explorer") return "File Explorer, in your Documents folder";
    if (key === "edge") return p.edDone.hidden ? "Edge, on the Contoso Events sign-up form" : "Edge, saying you're signed up for the meetup";
    if (key === "outlook") return p.olCompose.hidden ? "Outlook, on your inbox" : "Outlook, with an email you haven't sent";
    if (key === "taskmgr") return "Task Manager";
    return p.title(key);
  }

  async function email(r) {
    const p = pc();
    const message = "Hi Sam,\n\nHere's the quarterly report you asked for. It's attached.\n\nBest,\nAlex";
    const draft = "Quarterly report - Message";
    await r.think();
    r.text("I'll find the report, write the email in Outlook, and ask you before it goes out.");
    await r.tool("ui_read_text", { window: "File Explorer" }, async () => {
      await p.focus("explorer");
      await p.selectFile(REPORT.name);
      return p.explorerText();
    });
    await r.tool("open_app", { name: "Outlook" }, async () => { await p.open("outlook"); return "Opened Outlook."; });
    await r.tool("ui_click", { window: "Outlook", name: "New mail" }, async () => { await p.compose(); return 'Invoked Button "New mail".'; });
    await r.tool("ui_set_text", { window: "Untitled - Message", field: "To", text: SAM }, async () => {
      await p.mailField("to", SAM);
      return 'Set the text of Edit "To".';
    }, { after: 120 });
    await r.tool("ui_set_text", { window: "Untitled - Message", field: "Subject", text: "Quarterly report" }, async () => {
      await p.mailField("subject", "Quarterly report");
      return 'Set the text of Edit "Subject".';
    }, { after: 120 });
    await r.tool("ui_set_text", { window: draft, field: "Message body", text: message }, async () => {
      await p.mailField("body", message);
      return 'Set the text of Document "Message body".';
    }, { after: 120 });
    await r.tool("ui_click", { window: draft, name: "Attach file" }, async () => 'Invoked Button "Attach file".', { lead: 260, after: 120 });
    await r.tool("ui_set_text", { window: "Insert File", field: "File name", text: `${DOCS}\\${REPORT.name}` },
      async () => 'Set the text of Edit "File name".', { lead: 260, after: 120 });
    await r.tool("ui_click", { window: "Insert File", name: "Insert" }, async () => {
      await p.attach(REPORT.name, REPORT.size);
      return 'Invoked Button "Insert".';
    });
    const sent = await r.tool("ui_click", { window: draft, name: "Send" }, async () => {
      await p.sendMail();
      return 'Invoked Button "Send".';
    }, { approve: `click "Send" in ${draft}`, before: () => p.pointAtSend(true) });
    if (sent.declined) {
      p.pointAtSend(false);
      r.reply("Okay, I didn't send it.");
      return;
    }
    r.reply(`Sent. Sam has the quarterly report (${REPORT.name}, ${REPORT.size}).`);
  }

  async function copyToLaptop(r) {
    const p = pc();
    const here = Demo.server ? Demo.server.status().pc_name : "Desktop";
    const dest = `C:\\Users\\Alex\\Downloads\\${REPORT.name}`;
    const args = { from_pc: here, from_path: `${DOCS}\\${REPORT.name}`, to_pc: "Laptop", to_path: "%USERPROFILE%\\Downloads\\" };
    await r.think();
    r.text("I'll copy it from your Documents folder to Downloads on your laptop.");
    let refused = false;
    const copied = await r.tool("copy_file", args, async () => {
      p.showMachine("laptop");
      const yes = await p.ask(`save ${REPORT.name} (${REPORT.size}) as ${dest} (asked by ${here}, another PC)`);
      r.check();
      if (!yes) {
        refused = true;
        throw new Demo.ToolFail("Nobody at Laptop approved this (declined or no answer). Do not retry it; ask the user what they want.");
      }
      await p.receive(REPORT);
      return `Copied ${REPORT.name} (${REPORT.size}) from ${here} to Laptop as ${dest} in 1.3 s.`;
    }, {
      approve: `copy_file(${Demo.pyJson(args)})`,
      before: async () => {
        await p.focus("explorer");
        await p.selectFile(REPORT.name);
      },
    });
    setTimeout(() => p.showMachine("desktop"), 3500);
    if (copied.declined) r.reply("Okay, I won't copy it.");
    else if (refused) r.reply("Your laptop didn't take the file: nobody there approved saving it. Nothing was copied.");
    else r.reply(`Done. ${REPORT.name} is in the Downloads folder on your laptop. Alfred on the laptop asked there first, and it was allowed.`);
  }

  async function form(r) {
    const p = pc();
    await r.think();
    if (!p.edDone.hidden) p.resetForm(); // a new sign-up form each time, so the demo can be tried again
    await r.tool("focus_window", { window: "Edge" }, async () => { await p.focus("edge"); return `Switched to "${p.title("edge")}".`; });
    r.text("This page draws its own form, so I'll look at the screen and use the mouse.");
    const shot = () => r.tool("screenshot", {}, async () => { await p.flash(); return ""; }, { audit: "computer.screenshot" });
    const click = (target, element, options = {}) => {
      const at = p.point(target);
      const move = async () => {
        p.target(at.x, at.y, `left_click [${at.shot.join(", ")}]`);
        await p.moveTo(at.x, at.y);
      };
      return r.tool("left_click", { coordinate: at.shot }, async () => {
        if (!options.approve) await move();
        await p.clickAt(at.x, at.y);
        if (target !== "submit") p.formFocus(target);
        p.target(null);
        return "OK";
      }, { ...options, audit: "computer.left_click", before: options.approve ? move : undefined, declined: DECLINED,
        auditArgs: { coordinate: at.shot, screen_coordinate: [Math.round(at.x * 2.4), Math.round(at.y * 2.4)], element } });
    };
    const type = (target, text) => r.tool("type", { text }, async () => { await p.formType(target, text); return "OK"; },
      { audit: "computer.type" });
    await shot();
    await click("name", 'Edit "Full name"');
    await type("name", "Alex Morgan");
    await click("email", 'Edit "Email"');
    await type("email", "alex.morgan@contoso.com");
    await shot();
    const submitted = await click("submit", 'Button "Submit"', { approve: 'click Button "Submit" on screen' });
    if (submitted.declined) {
      p.hidePointer();
      r.reply("Okay, I didn't submit it. The form is filled in, so you can check it and press Submit yourself.");
      return;
    }
    await p.submitForm();
    await shot();
    p.hidePointer();
    r.reply("Done. I filled in your name and email and pressed Submit. The page says: “Thanks, Alex! You're signed up for the Aero Design Meetup.”");
  }

  const NUMBERS = { a: 1, an: 1, one: 1, two: 2, three: 3, five: 5, ten: 10, fifteen: 15, twenty: 20, thirty: 30 };
  async function remind(r, said) {
    const when = /\b(?:in|for|after)\s+(\d+|an?|one|two|three|five|ten|fifteen|twenty|thirty)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b/i.exec(said);
    const task = /\bremind me\s+(?:to|about|that)?\s*(.+)$/i.exec(said);
    let what = task ? task[1] : "";
    if (when) what = what.replace(when[0], "");
    what = trim(what).replace(/\s+(?:in|at|after)$/i, "");
    await r.think();
    if (!when) {
      r.reply(what ? `When should I remind you to ${what}? For example: in 10 minutes.` : "When should it go off? For example: in 10 minutes.");
      return;
    }
    const n = /^\d+$/.test(when[1]) ? Number(when[1]) : NUMBERS[when[1].toLowerCase()];
    const unit = when[2].toLowerCase().startsWith("h") ? 3600 : when[2].toLowerCase().startsWith("m") ? 60 : 1;
    const seconds = Math.max(1, n * unit);
    const message = what ? `Time to ${what}.` : "Time's up.";
    await r.tool("set_timer", { seconds, message }, async () => timerText(seconds));
    reminder(r, seconds, message);
    r.reply(what ? `Okay, I'll remind you to ${what} in ${humanTime(seconds)}.` : `Okay, a timer for ${humanTime(seconds)} is running.`);
  }

  async function greet(r) {
    await r.think();
    r.reply("Hi! I'm Alfred. I do things on your PC when you ask in plain words, and I ask before I send, delete, buy or "
      + `post anything. In this web demo I work on a simulated PC. ${tryThese()}`);
  }

  async function unknown(r) {
    await r.think();
    r.reply(`In this web demo I only run a few example tasks on a simulated PC, so I can't do that one here. ${tryThese()} `
      + "The Windows app does whatever you ask, on your own PC.");
  }

  let turn = 0;
  function tryThese() {
    const picks = [0, 1, 2].map((i) => EXAMPLES[(turn * 3 + i) % EXAMPLES.length]);
    turn += 1;
    return `Try “${picks[0]}”, “${picks[1]}” or “${picks[2]}”.`;
  }

  // Order matters: the first rule that fits wins.
  const RULES = [
    [/\b(laptop|other (pc|computer))\b/, copyToLaptop, (t) => /\b(copy|send|move|put|transfer|share|get)\b/.test(t)],
    [/\b(e-?mail|mail|send)\b/, email, (t) => /\b(sam|report)\b/.test(t)],
    [/\b(click|submit|sign[- ]?up|form|fill(ed)? (in|out)|computer use)\b/, form],
    [/\b(remind|reminder|timer|alarm)\b/, remind],
    [/\bnote ?pad\b/, notepad],
    [/\b(cpu|processor|memory|ram|battery|slow|performance|task manager|resources?)\b|\bhow('s| is| are)\b.*\b(pc|computer|laptop|system|machine)\b|\b(pc|computer)('s)? (doing|health|status)\b/, health],
    [/\bscreen\b|\bwindows?\b|\bwhat('s| is| are)? (open|running)\b|\bwhat am i (looking at|doing)\b/, screen],
    [/\b(music|song|songs|track|playlist|spotify|volume|louder|quieter|softer|mute|unmute|pause|play|playing|skip)\b/, music],
    [/\b(notes?|type|write|jot|shopping list|to-?do list|list)\b/, notepad],
    // clean() has already taken "can you" out of "what can you do"
    [/^(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|help|what (?:you )?do|who are you)\b/, greet],
  ];

  async function run(text, r) {
    const said = clean(text);
    const low = said.toLowerCase();
    if (/^(new conversation|forget that|reset)$/.test(low)) {
      r.reply("Okay, fresh start.");
      return true;
    }
    const fast = route(text);
    if (fast && FAST[fast[0]]) {
      const out = await FAST[fast[0]](r, fast[1]);
      if (out !== null) {
        r.reply(out);
        return true;
      }
    }
    for (const [pattern, play, also] of RULES) {
      if (pattern.test(low) && (!also || also(low))) {
        await play(r, said, low);
        return true;
      }
    }
    await unknown(r);
    return true;
  }

  Demo.scenarios = { run, route, examples: EXAMPLES };
})();
