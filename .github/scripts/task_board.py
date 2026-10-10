"""Builds docs/STATUS.md (the live task board) from docs/TASKS.md, open PRs and branches.

Run by .github/workflows/task-board.yml. Locally: python .github/scripts/task_board.py --dry-run
Needs `git` and, for PRs, the `gh` CLI with GH_TOKEN set. Never edit STATUS.md by hand.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "docs" / "TASKS.md"
STATUS = ROOT / "docs" / "STATUS.md"

TASK_LINE = re.compile(r"^- \[(?P<state>[ x\-])\] \*\*(?P<id>[0-9A-Z]+(?:\.\d+)?)\*\* (?P<title>.+)$")
AREA_LINE = re.compile(r"^## (?P<area>.+)$")
NEEDS = re.compile(r"needs: ([0-9A-Z., ]+?)\.(?:\s|$)")
BRACKET_ID = re.compile(r"\[([0-9]\.\d+|[A-Z]\d+)\]")
BRANCH_ID = re.compile(r"(?:^|[/_-])([0-9]\.\d+|[A-Za-z]\d+)(?=$|[/_-])")
IGNORED_BRANCHES = {"main", "HEAD"}


def parse_tasks(text: str) -> list[dict]:
    tasks, area = [], ""
    for line in text.splitlines():
        if m := AREA_LINE.match(line):
            area = m["area"]
        elif m := TASK_LINE.match(line):
            title = m["title"]
            needs = NEEDS.search(title)
            tasks.append({
                "id": m["id"],
                "state": {"x": "done", "-": "dropped", " ": "open"}[m["state"]],
                "title": re.split(r" \*Done when", title)[0].split(" needs:")[0].strip(),
                "needs": [n.strip() for n in needs[1].split(",")] if needs else [],
                "area": area,
            })
    return tasks


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def open_prs() -> list[dict]:
    try:
        out = run(["gh", "pr", "list", "--state", "open", "--limit", "100",
                   "--json", "number,title,headRefName,author,isDraft,url,updatedAt"])
        return json.loads(out)
    except (OSError, subprocess.CalledProcessError):
        return []


def branches() -> list[dict]:
    fmt = "%(refname:lstrip=3)|%(committerdate:iso-strict)|%(authorname)|%(subject)"
    out = run(["git", "for-each-ref", f"--format={fmt}", "refs/remotes/origin"])
    rows = []
    for line in out.splitlines():
        name, date, author, subject = line.split("|", 3)
        if name in IGNORED_BRANCHES:
            continue
        log = run(["git", "log", "--format=%s", f"origin/main..origin/{name}"])
        rows.append({"name": name, "date": date[:10], "author": author, "subject": subject, "ahead": len(log.splitlines()), "log": log})
    return rows


def ids_in(*texts: str) -> set[str]:
    found = set()
    for t in texts:
        found |= {i.upper() for i in BRACKET_ID.findall(t or "")}
    return found


def build(tasks: list[dict], prs: list[dict], brs: list[dict], now: str) -> str:
    known = {t["id"] for t in tasks}
    claims: dict[str, list[str]] = {}
    pr_branches = {p["headRefName"] for p in prs}
    unlinked = []

    for p in prs:
        ids = ids_in(p["title"]) | {i.upper() for i in BRANCH_ID.findall(p["headRefName"])}
        ids &= known
        label = f'PR #{p["number"]} ({p["author"]["login"]}, `{p["headRefName"]}`{", draft" if p["isDraft"] else ""})'
        for i in ids:
            claims.setdefault(i, []).append(label)
        if not ids:
            unlinked.append(f'- PR #{p["number"]} "{p["title"]}" on `{p["headRefName"]}`: add a task ID like `[C6]` to the title')

    stale = []
    for b in brs:
        if b["name"] in pr_branches or b["ahead"] == 0:
            continue
        ids = (ids_in(b["log"]) | {i.upper() for i in BRANCH_ID.findall(b["name"])}) & known
        label = f'branch `{b["name"]}` ({b["author"]}, no PR, {b["ahead"]} commits, last {b["date"]})'
        for i in ids:
            claims.setdefault(i, []).append(label)
        if not ids:
            stale.append(f'- {label}: "{b["subject"]}". Open a draft PR with a task ID.')

    done = {t["id"] for t in tasks if t["state"] in ("done", "dropped")}
    n_done = sum(t["state"] == "done" for t in tasks)
    lines = [
        "# Live task board",
        "",
        f"_Auto-generated {now} from [TASKS.md](TASKS.md), open PRs and branches. Do not edit; changes are overwritten._",
        "",
        f"**Progress:** {n_done} of {len(tasks)} tasks done.",
        "",
        "## In progress (claimed)",
        "",
        "| Task | Title | Claimed by |",
        "|---|---|---|",
    ]
    for t in tasks:
        if t["id"] in claims and t["state"] == "open":
            lines.append(f'| **{t["id"]}** | {t["title"]} | {"; ".join(claims[t["id"]])} |')
    lines += ["", "## Ready to start (open, unclaimed, dependencies done)", "", "First 3 per area. Take the lowest number in your area.", ""]
    area, shown = None, 0
    for t in tasks:
        if t["state"] != "open" or t["id"] in claims or any(n not in done for n in t["needs"]):
            continue
        if t["area"] != area:
            area, shown = t["area"], 0
            lines += ["", f"**{area}**", ""]
        if shown < 3:
            lines.append(f'- **{t["id"]}** {t["title"]}')
            shown += 1
    blocked = [t for t in tasks if t["state"] == "open" and t["id"] not in claims and any(n not in done for n in t["needs"])]
    lines += ["", "## Blocked (waiting on dependencies)", ""]
    lines += [f'- **{t["id"]}** waits on {", ".join(n for n in t["needs"] if n not in done)}' for t in blocked] or ["- none"]
    lines += ["", "## Needs attention", ""]
    lines += unlinked + stale or ["- nothing"]
    return "\n".join(lines) + "\n"


def main() -> int:
    tasks = parse_tasks(TASKS.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    board = build(tasks, open_prs(), branches(), now)
    if "--dry-run" in sys.argv:
        print(board)
        return 0
    old = STATUS.read_text(encoding="utf-8") if STATUS.exists() else ""
    strip = lambda s: re.sub(r"_Auto-generated .*? from", "", s)  # noqa: E731 - ignore the timestamp
    if strip(old) != strip(board):
        STATUS.write_text(board, encoding="utf-8")
        print("STATUS.md updated")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
