"""Render the local application tracker as an interactive HTML dashboard."""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from tracker import TRACKER_FIELDS


TERMINAL_STATUSES = {"rejected", "rejected_low_match", "closed", "withdrawn", "expired", "skipped"}
ACTIVE_STATUSES = {
    "queued",
    "analyzed",
    "resume_ready",
    "application_started",
    "applying_waiting_user_answers",
    "applying_waiting_resume_upload",
    "blocked_needs_user_input",
    "needs_manual_review",
    "submitted",
    "interview",
    "offer",
}


def read_tracker(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Tracker not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{field: row.get(field, "") for field in TRACKER_FIELDS} for row in reader]


def cell(value: str) -> str:
    return html.escape(value or "")


def attr(value: str) -> str:
    return html.escape(value or "", quote=True)


def link_cell(url: str, label: str = "Open") -> str:
    if not url:
        return ""
    return f'<a href="{attr(url)}" target="_blank" rel="noreferrer">{cell(label)}</a>'


def local_link(path_value: str) -> str:
    if not path_value:
        return ""
    return f'<span class="path">{cell(path_value.replace(chr(92), "/"))}</span>'


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def due_state(value: str, today: date) -> str:
    due = parse_date(value)
    if not due:
        return ""
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    return "upcoming"


def application_card(row: dict[str, str], today: date) -> str:
    due = due_state(row["follow_up_date"], today)
    edit_button = (
        f'<button class="icon-button edit-button" type="button" title="Edit application" '
        f'data-company="{attr(row["company"])}" data-role="{attr(row["role"])}" '
        f'data-url="{attr(row["url"])}" data-status="{attr(row["status"])}" '
        f'data-stage="{attr(row["stage"])}" data-stage-date="{attr(row["stage_date"])}" '
        f'data-follow-up="{attr(row["follow_up_date"])}" '
        f'data-next-action="{attr(row["next_action"])}" '
        f'data-contact="{attr(row["contact_name"])}">Edit</button>'
    )
    return f"""
      <article class="application-card" data-status="{attr(row['status'])}" data-stage="{attr(row['stage'])}">
        <div class="card-heading">
          <div>
            <h3>{cell(row['company'])}</h3>
            <p>{cell(row['role'])}</p>
          </div>
          {edit_button}
        </div>
        <div class="card-tags">
          <span class="pill status-{attr(row['status']).replace('_', '-')}">{cell(row['status'])}</span>
          <span class="pill stage-pill">{cell(row['stage'] or 'stage not set')}</span>
        </div>
        <dl>
          <div><dt>Next action</dt><dd>{cell(row['next_action'] or 'Monitor for an update')}</dd></div>
          <div><dt>Follow-up</dt><dd class="{due}">{cell(row['follow_up_date'] or 'Not scheduled')}</dd></div>
          <div><dt>Contact</dt><dd>{cell(row['contact_name'] or 'Not recorded')}</dd></div>
          <div><dt>Last contact</dt><dd>{cell(row['last_contact_date'] or row['submitted'] or row['date'])}</dd></div>
        </dl>
        <div class="card-links">
          {link_cell(row['url'], 'Job')}
          {link_cell(row['email_url'], 'Email')}
        </div>
      </article>
    """


def follow_up_item(row: dict[str, str], today: date) -> str:
    state = due_state(row["follow_up_date"], today)
    return f"""
      <article class="follow-up-item follow-up-{state}">
        <div class="follow-up-date">{cell(row['follow_up_date'])}</div>
        <div class="follow-up-main">
          <strong>{cell(row['company'])}</strong>
          <span>{cell(row['role'])}</span>
          <p>{cell(row['next_action'])}</p>
        </div>
        <div class="follow-up-meta">
          <span class="pill stage-pill">{cell(row['stage'] or row['status'])}</span>
          <span>{cell(row['contact_name'])}</span>
          {link_cell(row['email_url'], 'Email')}
        </div>
      </article>
    """


def render(rows: list[dict[str, str]], source_path: Path) -> str:
    today = date.today()
    status_counts = Counter(row["status"] for row in rows)
    active = [row for row in rows if row["status"] in ACTIVE_STATUSES and row["status"] not in TERMINAL_STATUSES]
    interviews = [row for row in active if row["status"] in {"interview", "offer"} or row["stage"]]
    follow_ups = sorted(
        [row for row in active if parse_date(row["follow_up_date"])],
        key=lambda row: row["follow_up_date"],
    )
    overdue = [row for row in follow_ups if parse_date(row["follow_up_date"]) < today]
    mailbox_updates = [row for row in rows if row["email_status"]]

    prioritized_active = sorted(
        active,
        key=lambda row: (
            row["status"] not in {"interview", "offer"},
            not bool(row["follow_up_date"] or row["stage"]),
            row["status"] != "blocked_needs_user_input",
            row["follow_up_date"] or "9999-12-31",
            row["company"].lower(),
        ),
    )[:6]
    active_cards = "".join(application_card(row, today) for row in prioritized_active)
    if not active_cards:
        active_cards = '<p class="empty-state">No active applications.</p>'

    follow_up_items = "".join(follow_up_item(row, today) for row in follow_ups)
    if not follow_up_items:
        follow_up_items = '<p class="empty-state">No follow-ups scheduled.</p>'

    table_rows = "\n".join(
        f"""
        <tr data-row data-status="{attr(row['status'])}" data-stage="{attr(row['stage'])}"
            data-search="{attr(' '.join([row['company'], row['role'], row['status'], row['stage'], row['source'], row['contact_name']]).lower())}">
          <td>{cell(row['date'])}</td>
          <td><strong>{cell(row['company'])}</strong></td>
          <td>{cell(row['role'])}</td>
          <td><span class="pill status-{attr(row['status']).replace('_', '-')}">{cell(row['status'])}</span></td>
          <td>{cell(row['stage'])}</td>
          <td>{cell(row['follow_up_date'])}</td>
          <td>{cell(row['next_action'])}</td>
          <td>{cell(row['contact_name'])}</td>
          <td>{link_cell(row['url'], 'Job')} {link_cell(row['email_url'], 'Email')}</td>
          <td>{local_link(row['resume_file'])}</td>
          <td>{cell(row['notes'])}</td>
        </tr>
        """
        for row in reversed(rows)
    )

    statuses = "".join(
        f'<option value="{attr(name)}">{cell(name)} ({count})</option>'
        for name, count in sorted(status_counts.items())
    )
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Application Tracker</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f5f4;
      --panel: #fff;
      --ink: #172026;
      --muted: #66737b;
      --line: #d7dedb;
      --accent: #087f5b;
      --accent-soft: #dff3eb;
      --blue: #1769aa;
      --blue-soft: #e5f0fa;
      --amber: #9a6700;
      --amber-soft: #fff2c7;
      --red: #b42318;
      --red-soft: #fee9e7;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: "Segoe UI", Arial, sans-serif; line-height: 1.45; }}
    main {{ max-width: 1480px; margin: 0 auto; padding: 24px; }}
    header {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; border-bottom: 1px solid var(--line); padding-bottom: 16px; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 4px; font-size: 28px; }}
    h2 {{ margin-bottom: 14px; font-size: 18px; }}
    h3 {{ margin-bottom: 3px; font-size: 16px; }}
    .meta {{ color: var(--muted); font-size: 12px; margin: 0; }}
    .live-status {{ display: none; align-items: center; gap: 7px; color: var(--accent); }}
    .live-status::before {{ width: 8px; height: 8px; border-radius: 50%; background: var(--accent); content: ""; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric-card, section, .application-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .metric-card {{ padding: 14px 16px; }}
    .metric-value {{ display: block; font-size: 27px; font-weight: 700; }}
    .metric-label {{ color: var(--muted); font-size: 12px; }}
    .dashboard-grid {{ display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(360px, 1fr); gap: 16px; }}
    section {{ padding: 16px; margin-bottom: 16px; }}
    .application-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }}
    .application-card {{ padding: 14px; }}
    .card-heading {{ display: flex; justify-content: space-between; gap: 12px; }}
    .card-heading p {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .card-tags, .card-links {{ display: flex; flex-wrap: wrap; gap: 7px; }}
    .card-links {{ margin-top: 10px; }}
    .card-links a {{ font-size: 12px; font-weight: 650; }}
    dl {{ margin: 12px 0 0; }}
    dl div {{ display: grid; grid-template-columns: 88px 1fr; gap: 8px; padding: 4px 0; font-size: 12px; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    .pill {{ display: inline-block; border: 1px solid #cbd7d2; border-radius: 999px; background: #edf3f1; padding: 2px 8px; font-size: 11px; font-weight: 650; white-space: nowrap; }}
    .status-interview, .status-offer {{ background: var(--blue-soft); border-color: #afcde6; color: #0d568c; }}
    .status-submitted {{ background: var(--accent-soft); border-color: #a8dbc8; color: #086246; }}
    .status-blocked-needs-user-input, .status-needs-manual-review {{ background: var(--amber-soft); border-color: #e9cf70; color: #765000; }}
    .status-rejected, .status-rejected-low-match, .status-expired, .status-skipped, .status-closed {{ background: #eceeef; border-color: #d4d8da; color: #5d666c; }}
    .stage-pill {{ background: #f4f1ff; border-color: #d7ccef; color: #5a4686; }}
    .overdue {{ color: var(--red); font-weight: 700; }}
    .today {{ color: var(--amber); font-weight: 700; }}
    .follow-up-list {{ display: grid; gap: 8px; }}
    .follow-up-item {{ display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 10px; border: 1px solid var(--line); border-radius: 7px; padding: 11px; }}
    .follow-up-date {{ align-self: start; color: var(--accent); font-size: 12px; font-weight: 700; }}
    .follow-up-main {{ display: grid; gap: 2px; min-width: 0; }}
    .follow-up-main span, .follow-up-meta {{ color: var(--muted); font-size: 12px; }}
    .follow-up-main p {{ margin: 5px 0 0; font-size: 12px; }}
    .follow-up-meta {{ grid-column: 2; display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
    input, select, textarea, button {{ font: inherit; }}
    input, select, textarea {{ border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); padding: 8px 10px; }}
    #search {{ min-width: 260px; flex: 1; }}
    button {{ border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); cursor: pointer; padding: 7px 10px; }}
    button.primary {{ border-color: var(--accent); background: var(--accent); color: #fff; }}
    .icon-button {{ padding: 4px 8px; font-size: 12px; }}
    a {{ color: #075985; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .table-wrap {{ max-height: 680px; overflow: auto; border: 1px solid var(--line); border-radius: 7px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; z-index: 1; background: #eaf0ed; white-space: nowrap; }}
    td {{ overflow-wrap: anywhere; max-width: 320px; }}
    .follow-up-overdue {{ background: #fff6f5; border-color: #f0b6b1; }}
    .follow-up-today {{ background: #fffaf0; border-color: #ead39b; }}
    .empty-state {{ color: var(--muted); }}
    dialog {{ width: min(560px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 18px 50px #0003; padding: 0; }}
    dialog::backdrop {{ background: #17202688; }}
    .dialog-body {{ padding: 18px; }}
    .form-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .form-field {{ display: grid; gap: 4px; font-size: 12px; }}
    .form-field.full {{ grid-column: 1 / -1; }}
    .dialog-actions {{ display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }}
    [hidden] {{ display: none !important; }}
    @media (max-width: 960px) {{
      .metrics {{ grid-template-columns: repeat(2, 1fr); }}
      .dashboard-grid {{ grid-template-columns: 1fr; }}
      header {{ align-items: flex-start; flex-direction: column; }}
    }}
    @media (max-width: 560px) {{
      main {{ padding: 14px; }}
      .metrics, .form-grid {{ grid-template-columns: 1fr; }}
      .form-field.full {{ grid-column: auto; }}
      #search {{ min-width: 100%; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Application Tracker</h1>
      <p class="meta">Updated {cell(generated)} · {cell(str(source_path))}</p>
    </div>
    <p class="meta live-status" id="live-status">Live</p>
  </header>

  <div class="metrics">
    <div class="metric-card"><span class="metric-value">{len(active)}</span><span class="metric-label">Active applications</span></div>
    <div class="metric-card"><span class="metric-value">{len(interviews)}</span><span class="metric-label">Interview pipeline</span></div>
    <div class="metric-card"><span class="metric-value">{len(follow_ups)}</span><span class="metric-label">Scheduled follow-ups</span></div>
    <div class="metric-card"><span class="metric-value">{len(overdue)}</span><span class="metric-label">Overdue actions</span></div>
    <div class="metric-card"><span class="metric-value">{len(mailbox_updates)}</span><span class="metric-label">Mailbox-linked updates</span></div>
  </div>

  <div class="dashboard-grid">
    <section>
      <h2>Active Pipeline</h2>
      <div class="application-grid">{active_cards}</div>
    </section>
    <section>
      <h2>Follow-ups and Next Rounds</h2>
      <div class="follow-up-list">{follow_up_items}</div>
    </section>
  </div>

  <section>
    <h2>All Applications</h2>
    <div class="toolbar">
      <input id="search" type="search" placeholder="Search company, role, stage, source, or contact">
      <select id="status-filter"><option value="">All statuses</option>{statuses}</select>
      <select id="stage-filter">
        <option value="">All stages</option>
        <option value="application_received">Application received</option>
        <option value="recruiter_screen_scheduled">Recruiter screen scheduled</option>
        <option value="recruiter_screen_completed">Recruiter screen completed</option>
        <option value="interview_scheduled">Interview scheduled</option>
        <option value="interview_completed">Interview completed</option>
        <option value="next_round">Next round</option>
        <option value="offer_received">Offer received</option>
      </select>
      <button id="clear-filters" type="button">Clear</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Company</th><th>Role</th><th>Status</th><th>Stage</th>
            <th>Follow-up</th><th>Next action</th><th>Contact</th><th>Links</th><th>Resume</th><th>Notes</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </section>
</main>

<dialog id="edit-dialog">
  <form id="edit-form" class="dialog-body">
    <h2>Edit Application</h2>
    <input name="company" type="hidden">
    <input name="role" type="hidden">
    <input name="url" type="hidden">
    <div class="form-grid">
      <label class="form-field"><span>Status</span>
        <select name="status">
          <option>submitted</option><option>interview</option><option>offer</option>
          <option>rejected</option><option>closed</option><option>withdrawn</option>
        </select>
      </label>
      <label class="form-field"><span>Stage</span>
        <select name="stage">
          <option value=""></option>
          <option value="application_received">Application received</option>
          <option value="recruiter_screen_scheduled">Recruiter screen scheduled</option>
          <option value="recruiter_screen_completed">Recruiter screen completed</option>
          <option value="interview_scheduled">Interview scheduled</option>
          <option value="interview_completed">Interview completed</option>
          <option value="next_round">Next round</option>
          <option value="offer_received">Offer received</option>
        </select>
      </label>
      <label class="form-field"><span>Stage date</span><input name="stage_date" type="date"></label>
      <label class="form-field"><span>Follow-up date</span><input name="follow_up_date" type="date"></label>
      <label class="form-field full"><span>Next action</span><input name="next_action"></label>
      <label class="form-field full"><span>Contact</span><input name="contact_name"></label>
    </div>
    <div class="dialog-actions">
      <button id="cancel-edit" type="button">Cancel</button>
      <button class="primary" type="submit">Save</button>
    </div>
  </form>
</dialog>

<script>
  (() => {{
    const rows = [...document.querySelectorAll("[data-row]")];
    const search = document.getElementById("search");
    const statusFilter = document.getElementById("status-filter");
    const stageFilter = document.getElementById("stage-filter");
    const dialog = document.getElementById("edit-dialog");
    const form = document.getElementById("edit-form");
    const liveStatus = document.getElementById("live-status");
    const isLive = window.location.protocol.startsWith("http");

    function filterRows() {{
      const query = search.value.trim().toLowerCase();
      rows.forEach(row => {{
        const visible =
          (!query || row.dataset.search.includes(query)) &&
          (!statusFilter.value || row.dataset.status === statusFilter.value) &&
          (!stageFilter.value || row.dataset.stage === stageFilter.value);
        row.hidden = !visible;
      }});
    }}

    [search, statusFilter, stageFilter].forEach(control => control.addEventListener("input", filterRows));
    document.getElementById("clear-filters").addEventListener("click", () => {{
      search.value = "";
      statusFilter.value = "";
      stageFilter.value = "";
      filterRows();
    }});

    document.querySelectorAll(".edit-button").forEach(button => {{
      button.hidden = !isLive;
      button.addEventListener("click", () => {{
        for (const [name, value] of Object.entries({{
          company: button.dataset.company,
          role: button.dataset.role,
          url: button.dataset.url,
          status: button.dataset.status,
          stage: button.dataset.stage,
          stage_date: button.dataset.stageDate,
          follow_up_date: button.dataset.followUp,
          next_action: button.dataset.nextAction,
          contact_name: button.dataset.contact
        }})) form.elements[name].value = value || "";
        dialog.showModal();
      }});
    }});

    document.getElementById("cancel-edit").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", async event => {{
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(form).entries());
      const response = await fetch("/api/application-update", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload)
      }});
      if (!response.ok) {{
        alert("Update failed");
        return;
      }}
      dialog.close();
      window.location.reload();
    }});

    if (!isLive) return;
    liveStatus.style.display = "flex";
    let currentVersion = null;
    async function checkForUpdates() {{
      try {{
        const response = await fetch("/api/tracker-version", {{ cache: "no-store" }});
        const payload = await response.json();
        if (currentVersion !== null && payload.version !== currentVersion) window.location.reload();
        currentVersion = payload.version;
        liveStatus.textContent = "Live";
      }} catch {{
        liveStatus.textContent = "Disconnected";
      }}
    }}
    checkForUpdates();
    window.setInterval(checkForUpdates, 3000);
  }})();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render tracker/applications.csv as an HTML dashboard.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/application_tracker_summary.html"))
    args = parser.parse_args()

    rows = read_tracker(args.tracker)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(rows, args.tracker), encoding="utf-8")
    print(f"Wrote {args.output} with {len(rows)} tracker rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
