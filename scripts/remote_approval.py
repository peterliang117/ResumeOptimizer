#!/usr/bin/env python3
"""Create and consume job-specific remote approval tokens."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from job_store import DEFAULT_DB, connection, initialize, utc_now
except ImportError:  # pragma: no cover
    from scripts.job_store import DEFAULT_DB, connection, initialize, utc_now


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANSWERS = ROOT / "profile" / "application_answers.json"
SCOPES = {"answer", "transmit", "submit"}
DECISIONS = {
    "answer": "approved",
    "approve": "approved",
    "no": "approved",
    "skip": "skipped",
    "yes": "approved",
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.strip().upper().encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def configured_email(path: Path = DEFAULT_ANSWERS) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    email = str(payload.get("standard_fields", {}).get("email") or "").strip().casefold()
    if not email or "@" not in email:
        raise ValueError("profile/application_answers.json does not contain a valid standard_fields.email.")
    return email


def create_request(
    company: str,
    role: str,
    url: str,
    scope: str,
    *,
    question: str = "",
    proposed_answer: str = "",
    expires_minutes: int = 720,
    db_path: Path = DEFAULT_DB,
    answers_path: Path = DEFAULT_ANSWERS,
) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")
    if not company.strip() or not role.strip() or not url.strip():
        raise ValueError("Company, role, and URL are required.")
    if scope == "answer" and not question.strip():
        raise ValueError("Answer requests require the exact application question.")
    if expires_minutes < 5 or expires_minutes > 1440:
        raise ValueError("expires_minutes must be between 5 and 1440.")
    initialize(db_path)
    token = secrets.token_hex(6).upper()
    now = _now()
    expires = now + timedelta(minutes=expires_minutes)
    with connection(db_path) as conn:
        if scope == "answer":
            conn.execute(
                """UPDATE remote_approvals SET status='superseded', updated_at=?
                   WHERE company=? AND role=? AND url=? AND scope=? AND question=?
                   AND status='pending'""",
                (
                    now.isoformat(), company.strip(), role.strip(), url.strip(),
                    scope, question.strip(),
                ),
            )
        else:
            conn.execute(
                """UPDATE remote_approvals SET status='superseded', updated_at=?
                   WHERE company=? AND role=? AND url=? AND scope=? AND status='pending'""",
                (now.isoformat(), company.strip(), role.strip(), url.strip(), scope),
            )
        conn.execute(
            """INSERT INTO remote_approvals
               (token_hash, company, role, url, scope, question, proposed_answer,
                status, expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                _hash(token), company.strip(), role.strip(), url.strip(), scope,
                question.strip(), proposed_answer.strip(),
                expires.isoformat(), now.isoformat(), now.isoformat(),
            ),
        )
    return {
        "token": token,
        "company": company.strip(),
        "role": role.strip(),
        "url": url.strip(),
        "scope": scope,
        "question": question.strip(),
        "proposed_answer": proposed_answer.strip(),
        "expires_at": expires.isoformat(),
        "recipient": configured_email(answers_path),
        "approve_reply": f"APPROVE {token}",
        "yes_reply": f"YES {token}",
        "no_reply": f"NO {token}",
        "answer_reply": f"ANSWER {token}: <your answer>",
        "skip_reply": f"SKIP {token}",
    }


def decide(
    token: str,
    decision: str,
    sender: str,
    *,
    answer_value: str = "",
    message_url: str = "",
    db_path: Path = DEFAULT_DB,
    answers_path: Path = DEFAULT_ANSWERS,
) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"Unsupported decision: {decision}")
    if sender.strip().casefold() != configured_email(answers_path):
        raise PermissionError("Approval sender does not match the configured Outlook address.")
    initialize(db_path)
    now = _now()
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM remote_approvals WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
        if not row:
            raise ValueError("Unknown approval token.")
        if row["status"] != "pending":
            raise ValueError(f"Approval token is already {row['status']}.")
        if datetime.fromisoformat(row["expires_at"]) <= now:
            conn.execute(
                "UPDATE remote_approvals SET status='expired', updated_at=? WHERE id=?",
                (now.isoformat(), row["id"]),
            )
            raise ValueError("Approval token has expired.")
        status = DECISIONS[decision]
        resolved_answer = ""
        if status == "approved" and row["scope"] == "answer":
            if decision == "approve":
                resolved_answer = str(row["proposed_answer"] or "").strip()
                if not resolved_answer:
                    raise ValueError("This answer request has no proposed answer to approve.")
            elif decision == "yes":
                resolved_answer = "Yes"
            elif decision == "no":
                resolved_answer = "No"
            elif decision == "answer":
                resolved_answer = answer_value.strip()
                if not resolved_answer:
                    raise ValueError("The ANSWER decision requires a non-empty answer value.")
        elif decision in {"answer", "yes", "no"}:
            raise ValueError(f"Decision {decision} is valid only for answer-scope requests.")
        conn.execute(
            """UPDATE remote_approvals
               SET status=?, answer_value=?, decision_email_url=?, decision_at=?, updated_at=?
               WHERE id=?""",
            (
                status, resolved_answer, message_url.strip(), now.isoformat(),
                now.isoformat(), row["id"],
            ),
        )
        values = dict(row)
    values.update({
        "status": status,
        "answer_value": resolved_answer,
        "decision_at": now.isoformat(),
    })
    values.pop("token_hash", None)
    return values


def consume(
    company: str,
    role: str,
    url: str,
    scope: str,
    *,
    question: str = "",
    db_path: Path = DEFAULT_DB,
) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")
    initialize(db_path)
    now = _now()
    with connection(db_path) as conn:
        query = """SELECT * FROM remote_approvals
                   WHERE company=? AND role=? AND url=? AND scope=? AND status='approved'"""
        values: list[str] = [company.strip(), role.strip(), url.strip(), scope]
        if question.strip():
            query += " AND question=?"
            values.append(question.strip())
        rows = conn.execute(query + " ORDER BY decision_at DESC", values).fetchall()
        if scope == "answer" and not question.strip() and len(rows) > 1:
            raise PermissionError(
                "Multiple approved answers match this job; consume with the exact question."
            )
        row = rows[0] if rows else None
        if not row:
            raise PermissionError("No approved remote action matches this company, role, URL, and scope.")
        if datetime.fromisoformat(row["expires_at"]) <= now:
            conn.execute(
                "UPDATE remote_approvals SET status='expired', updated_at=? WHERE id=?",
                (now.isoformat(), row["id"]),
            )
            raise PermissionError("The matching approval has expired.")
        conn.execute(
            "UPDATE remote_approvals SET status='consumed', updated_at=? WHERE id=?",
            (now.isoformat(), row["id"]),
        )
        values = dict(row)
    values.update({"status": "consumed", "consumed_at": now.isoformat()})
    values.pop("token_hash", None)
    return values


def pending(db_path: Path = DEFAULT_DB) -> list[dict]:
    initialize(db_path)
    now = utc_now()
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE remote_approvals SET status='expired', updated_at=? WHERE status='pending' AND expires_at<=?",
            (now, now),
        )
        rows = conn.execute(
            """SELECT company, role, url, scope, question, proposed_answer,
                      status, expires_at, created_at
               FROM remote_approvals WHERE status='pending' ORDER BY created_at"""
        ).fetchall()
    return [dict(row) for row in rows]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage single-use remote job approvals.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--company", required=True)
    create.add_argument("--role", required=True)
    create.add_argument("--url", required=True)
    create.add_argument("--scope", choices=sorted(SCOPES), required=True)
    create.add_argument("--question", default="")
    create.add_argument("--proposed-answer", default="")
    create.add_argument("--expires-minutes", type=int, default=720)
    approve = sub.add_parser("decide")
    approve.add_argument("--token", required=True)
    approve.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    approve.add_argument("--sender", required=True)
    approve.add_argument("--message-url", default="")
    approve.add_argument("--answer-value", default="")
    use = sub.add_parser("consume")
    use.add_argument("--company", required=True)
    use.add_argument("--role", required=True)
    use.add_argument("--url", required=True)
    use.add_argument("--scope", choices=sorted(SCOPES), required=True)
    use.add_argument("--question", default="")
    sub.add_parser("pending")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "create":
        result = create_request(
            args.company, args.role, args.url, args.scope,
            question=args.question, proposed_answer=args.proposed_answer,
            expires_minutes=args.expires_minutes, db_path=args.db,
        )
    elif args.command == "decide":
        result = decide(
            args.token, args.decision, args.sender,
            answer_value=args.answer_value,
            message_url=args.message_url, db_path=args.db,
        )
    elif args.command == "consume":
        result = consume(
            args.company, args.role, args.url, args.scope,
            question=args.question, db_path=args.db,
        )
    else:
        result = pending(args.db)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
