import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ats_adapter  # noqa: E402
import application_state  # noqa: E402
import discovery  # noqa: E402
import job_store  # noqa: E402
import resume_evidence  # noqa: E402
import queue_maintenance  # noqa: E402
import remote_approval  # noqa: E402
import scheduled_reconcile  # noqa: E402
import search_criteria  # noqa: E402
import tracker_report  # noqa: E402


class SQLiteWorkflowTests(unittest.TestCase):
    def test_search_criteria_treats_seven_days_as_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            criteria_path = Path(tmp) / "criteria.md"
            criteria_path.write_text("- Date posted: past 7 days\n", encoding="utf-8")
            self.assertEqual(search_criteria.read_search_criteria(criteria_path).date_posted, "week")

    def test_active_role_url_upgrade_does_not_create_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            job_store.upsert_job({
                "company": "Example",
                "role": "Data Engineer",
                "url": "https://linkedin.example/jobs/123",
                "status": "resume_ready",
                "base_match_score": 88,
            }, path=db)
            job_store.upsert_job({
                "company": "Example",
                "role": "Data Engineer",
                "url": "https://example.test/careers/456",
                "status": "application_started",
            }, path=db)
            with job_store.connection(db) as conn:
                rows = conn.execute("SELECT * FROM jobs").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["url"], "https://example.test/careers/456")
            self.assertEqual(rows[0]["base_match_score"], 88)

    def test_application_state_canonicalizes_ashby_application_url(self):
        self.assertEqual(
            application_state.canonical_job_url(
                "https://jobs.ashbyhq.com/example/role-id/application?source=linkedin"
            ),
            "https://jobs.ashbyhq.com/example/role-id",
        )
        self.assertEqual(
            application_state.canonical_job_url("https://example.test/jobs/1/application"),
            "https://example.test/jobs/1/application",
        )

    def test_state_transaction_exports_queue_and_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "workflow.db"
            queue = root / "jobs" / "queue.csv"
            tracker = root / "tracker" / "applications.csv"
            job_store.record_application_state(
                {
                    "company": "Example Bank",
                    "role": "Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/jobs/1",
                    "status": "submitted",
                    "priority": "high",
                    "notes": "Submitted from test.",
                },
                {
                    "company": "Example Bank",
                    "role": "Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/jobs/1",
                    "status": "submitted",
                    "stage": "application_received",
                    "notes": "Submitted from test.",
                },
                path=db,
            )
            job_store.export_legacy_csv(queue_path=queue, tracker_path=tracker, path=db)

            self.assertEqual(job_store.queue_rows(db)[0]["status"], "submitted")
            self.assertEqual(job_store.tracker_rows(db)[0]["stage"], "application_received")
            self.assertTrue(queue.exists())
            self.assertTrue(tracker.exists())
            with job_store.connection(db) as conn:
                event_count = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            self.assertGreaterEqual(event_count, 2)

    def test_discovery_requires_fresh_direct_compensated_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            criteria = root / "criteria.md"
            criteria.write_text("- Target pay: at least `$160,000` base\n", encoding="utf-8")
            candidate = {
                "company": "Example Fintech",
                "role": "Data Engineer",
                "source": "Greenhouse",
                "url": "https://example.test/jobs/2",
                "location": "New York, NY",
                "work_mode": "Hybrid",
                "employment_type": "Full-time",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "compensation_low": 165000,
                "compensation_high": 200000,
                "direct_employer": True,
                "match_score": 82,
            }
            job_text = "Build Python and SQL data pipelines, data models, and data quality controls."
            result = discovery.evaluate_candidate(candidate, job_text, criteria_path=criteria, db_path=root / "workflow.db")
            self.assertTrue(result["eligible"])
            self.assertEqual(result["role_family"], "data_engineering")

            candidate["posted_at"] = (datetime.now(timezone.utc) - timedelta(hours=96)).isoformat()
            older_but_eligible = discovery.evaluate_candidate(
                candidate, job_text, criteria_path=criteria, db_path=root / "workflow.db"
            )
            self.assertTrue(older_but_eligible["eligible"])
            self.assertEqual(older_but_eligible["hard_gate"]["freshness_priority"], "standard")

            candidate["posted_at"] = (datetime.now(timezone.utc) - timedelta(hours=169)).isoformat()
            stale = discovery.evaluate_candidate(candidate, job_text, criteria_path=criteria, db_path=root / "workflow.db")
            self.assertFalse(stale["eligible"])
            self.assertTrue(any("168-hour" in item for item in stale["hard_gate"]["hard_filter_failures"]))

    def test_discovery_allows_tier_b_only_for_strong_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            criteria = root / "criteria.md"
            criteria.write_text(
                "\n".join(
                    (
                        "- Target pay: `$160,000`",
                        "- Secondary pay floor: `$145,000`",
                        "- Secondary pay minimum score: 82",
                    )
                ),
                encoding="utf-8",
            )
            candidate = {
                "company": "Example Platform",
                "role": "Data Platform Engineer",
                "source": "Greenhouse",
                "url": "https://example.test/jobs/tier-b",
                "location": "Remote, United States",
                "work_mode": "Remote",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "compensation_high": 150000,
                "direct_employer": True,
                "match_score": 82,
            }
            job_text = "Build SQL and Python ETL pipelines, data models, and data quality controls."
            strong = discovery.evaluate_candidate(
                candidate, job_text, criteria_path=criteria, db_path=root / "workflow.db"
            )
            self.assertTrue(strong["eligible"])
            self.assertEqual(strong["hard_gate"]["compensation_tier"], "B")

            candidate["match_score"] = 81
            weak = discovery.evaluate_candidate(
                candidate, job_text, criteria_path=criteria, db_path=root / "workflow.db"
            )
            self.assertFalse(weak["eligible"])
            self.assertTrue(any("Tier B" in item for item in weak["hard_gate"]["hard_filter_failures"]))

    def test_discovery_limits_active_employer_applications_to_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "workflow.db"
            criteria = root / "criteria.md"
            criteria.write_text(
                "- Target pay: `$160,000`\n- Maximum active applications per employer: 2\n",
                encoding="utf-8",
            )
            for index in range(2):
                job_store.upsert_job(
                    {
                        "company": "Example Security",
                        "role": f"Data Engineer {index}",
                        "url": f"https://example.test/jobs/existing-{index}",
                        "status": "submitted",
                    },
                    path=db,
                )
            candidate = {
                "company": "Example Security",
                "role": "Security Analytics Engineer",
                "source": "Greenhouse",
                "url": "https://example.test/jobs/third",
                "location": "New York, NY",
                "work_mode": "Hybrid",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "compensation_high": 180000,
                "direct_employer": True,
                "match_score": 90,
            }
            result = discovery.evaluate_candidate(
                candidate,
                "Build SQL and Python security data pipelines and reporting controls.",
                criteria_path=criteria,
                db_path=db,
            )
            self.assertFalse(result["eligible"])
            self.assertTrue(any("configured limit is 2" in item for item in result["hard_gate"]["hard_filter_failures"]))

    def test_evidence_validator_rejects_unsupported_tool(self):
        evidence = {"records": [{"source": "profile", "text": "Built SQL and Python ETL pipelines with data quality checks."}]}
        valid_edit = {
            "original": "Built ETL pipelines.",
            "suggested": "Built SQL and Python ETL pipelines with data quality checks.",
            "truth_risk": "low",
        }
        bad_edit = {**valid_edit, "suggested": "Built Spark and Snowflake pipelines.", "original": "Built pipelines."}
        self.assertEqual(resume_evidence.validate_edit_against_evidence(valid_edit, evidence), [])
        self.assertTrue(resume_evidence.validate_edit_against_evidence(bad_edit, evidence))

    def test_ats_plan_only_prefills_exact_known_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            answers = {
                "standard_fields": {"first_name": "Test", "last_name": "Candidate", "email": "test@example.com"},
                "answer_policy": {"allow_prefill_standard_fields": True},
            }
            first = ats_adapter.plan_field("greenhouse", "First Name", answers, db)
            unknown = ats_adapter.plan_field("greenhouse", "Why this company?", answers, db)
            self.assertEqual(first["decision"], "prefill_exact")
            self.assertEqual(unknown["decision"], "manual_review_required")

    def test_scheduled_reconciliation_stores_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            scheduled_reconcile.configure_schedule("outlook_reconciliation", 240, path=db)
            scheduled_reconcile.apply_event(
                {
                    "company": "Example Bank",
                    "role": "Data Engineer",
                    "event": "interview_invitation",
                    "received_date": "2026-07-12",
                    "subject": "Interview invitation",
                    "message_url": "https://outlook.example/message/1",
                },
                db,
            )
            row = job_store.tracker_rows(db)[0]
            self.assertEqual(row["status"], "interview")
            self.assertEqual(row["email_subject"], "Interview invitation")
            self.assertNotIn("body", row)

    def test_linkedin_alert_manifest_has_independent_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            manifest = scheduled_reconcile.alert_discovery_manifest(db)

            self.assertEqual(manifest["source"], "LinkedIn job-alert emails")
            self.assertTrue(manifest["rules"]["lead_only"])
            self.assertFalse(manifest["rules"]["store_message_body"])
            self.assertTrue(manifest["since_datetime"])
            tasks = {row["task_name"] for row in scheduled_reconcile.due_tasks(db)}
            self.assertIn(scheduled_reconcile.ALERT_TASK, tasks)
            self.assertEqual(manifest["schedule"]["interval_minutes"], 120)

            scheduled_reconcile.mark_schedule_run(scheduled_reconcile.ALERT_TASK, path=db)
            refreshed = scheduled_reconcile.alert_discovery_manifest(db)
            self.assertTrue(refreshed["schedule"]["last_run_at"])
            self.assertFalse(refreshed["schedule"]["due"])

    def test_full_pipeline_manifest_has_independent_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            manifest = scheduled_reconcile.pipeline_manifest(db)
            self.assertTrue(manifest["schedule"]["due"])
            self.assertTrue(manifest["rules"]["serialized"])
            self.assertEqual(manifest["rules"]["queue_capacity"], 10)
            self.assertEqual(manifest["schedule"]["interval_minutes"], 30)

            scheduled_reconcile.mark_schedule_run(scheduled_reconcile.PIPELINE_TASK, path=db)
            refreshed = scheduled_reconcile.pipeline_manifest(db)
            self.assertFalse(refreshed["schedule"]["due"])

    def test_remote_approval_is_scoped_expiring_and_single_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({
                "standard_fields": {"email": "owner@example.com"}
            }), encoding="utf-8")
            request = remote_approval.create_request(
                "Example", "Data Engineer", "https://example.test/job/1", "transmit",
                db_path=db, answers_path=answers,
            )
            decision = remote_approval.decide(
                request["token"], "approve", "OWNER@example.com",
                message_url="https://outlook.test/reply/1", db_path=db, answers_path=answers,
            )
            self.assertEqual(decision["status"], "approved")
            used = remote_approval.consume(
                "Example", "Data Engineer", "https://example.test/job/1", "transmit", db_path=db,
            )
            self.assertEqual(used["status"], "consumed")
            with self.assertRaises(PermissionError):
                remote_approval.consume(
                    "Example", "Data Engineer", "https://example.test/job/1", "transmit", db_path=db,
                )

    def test_remote_approval_rejects_wrong_sender_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({
                "standard_fields": {"email": "owner@example.com"}
            }), encoding="utf-8")
            request = remote_approval.create_request(
                "Example", "Data Engineer", "https://example.test/job/2", "transmit",
                db_path=db, answers_path=answers,
            )
            with self.assertRaises(PermissionError):
                remote_approval.decide(
                    request["token"], "approve", "attacker@example.com",
                    db_path=db, answers_path=answers,
                )
            with self.assertRaises(PermissionError):
                remote_approval.consume(
                    "Example", "Data Engineer", "https://example.test/job/2", "submit", db_path=db,
                )

    def test_remote_answer_approval_supports_short_phone_replies(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({
                "standard_fields": {"email": "owner@example.com"}
            }), encoding="utf-8")
            request = remote_approval.create_request(
                "Example", "Data Engineer", "https://example.test/job/answer", "answer",
                question="Are you at least 18?", proposed_answer="Yes",
                db_path=db, answers_path=answers,
            )
            decision = remote_approval.decide(
                request["token"], "approve", "owner@example.com",
                db_path=db, answers_path=answers,
            )
            self.assertEqual(decision["answer_value"], "Yes")
            used = remote_approval.consume(
                "Example", "Data Engineer", "https://example.test/job/answer", "answer", db_path=db,
            )
            self.assertEqual(used["answer_value"], "Yes")

    def test_remote_answer_accepts_yes_no_and_custom_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps({
                "standard_fields": {"email": "owner@example.com"}
            }), encoding="utf-8")
            for index, (decision_name, answer_value, expected) in enumerate((
                ("yes", "", "Yes"),
                ("no", "", "No"),
                ("answer", "Two weeks", "Two weeks"),
            )):
                db = Path(tmp) / f"workflow-{index}.db"
                request = remote_approval.create_request(
                    "Example", "Data Engineer", f"https://example.test/job/{index}", "answer",
                    question="Required question", db_path=db, answers_path=answers,
                )
                result = remote_approval.decide(
                    request["token"], decision_name, "owner@example.com",
                    answer_value=answer_value, db_path=db, answers_path=answers,
                )
                self.assertEqual(result["answer_value"], expected)

    def test_remote_answer_batch_keeps_distinct_questions_and_consumes_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            answers = Path(tmp) / "answers.json"
            answers.write_text(
                json.dumps({"standard_fields": {"email": "owner@example.com"}}),
                encoding="utf-8",
            )
            requests = [
                remote_approval.create_request(
                    "Example", "Data Engineer", "https://example.test/job/batch", "answer",
                    question=question, db_path=db, answers_path=answers,
                )
                for question in ("Question one?", "Question two?")
            ]
            self.assertEqual(len(remote_approval.pending(db)), 2)
            for request in requests:
                remote_approval.decide(
                    request["token"], "no", "owner@example.com",
                    db_path=db, answers_path=answers,
                )
            with self.assertRaises(PermissionError):
                remote_approval.consume(
                    "Example", "Data Engineer", "https://example.test/job/batch", "answer",
                    db_path=db,
                )
            consumed = remote_approval.consume(
                "Example", "Data Engineer", "https://example.test/job/batch", "answer",
                question="Question one?", db_path=db,
            )
            self.assertEqual(consumed["answer_value"], "No")

    def test_scheduled_reconciliation_updates_existing_application_and_skips_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            job_store.record_application_state(
                {
                    "company": "Example Trading",
                    "role": "Data Engineer",
                    "source": "LinkedIn",
                    "url": "https://example.test/jobs/3",
                    "status": "submitted",
                },
                {
                    "company": "Example Trading",
                    "role": "Data Engineer",
                    "source": "LinkedIn",
                    "url": "https://example.test/jobs/3",
                    "status": "submitted",
                    "resume_file": "applications/example/resume.docx",
                },
                path=db,
            )
            event = {
                "company": "Example Trading",
                "role": "Data Engineer",
                "event": "rejected",
                "received_date": "2026-07-15",
                "subject": "Application status",
                "message_url": "https://outlook.example/message/rejection",
            }

            self.assertTrue(scheduled_reconcile.apply_event(event, db))
            rows = job_store.tracker_rows(db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "rejected")
            self.assertEqual(rows[0]["source"], "LinkedIn")
            self.assertEqual(rows[0]["url"], "https://example.test/jobs/3")
            self.assertEqual(rows[0]["resume_file"], "applications/example/resume.docx")
            with job_store.connection(db) as conn:
                event_count = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]

            self.assertFalse(scheduled_reconcile.apply_event(event, db))
            with job_store.connection(db) as conn:
                duplicate_count = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            self.assertEqual(duplicate_count, event_count)

    def test_scheduled_reconciliation_migrates_legacy_identity_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            url = "https://example.test/jobs/legacy"
            job_store.record_application_state(
                {
                    "company": "Legacy Bank",
                    "role": "Data Engineer",
                    "source": "Careers",
                    "url": url,
                    "status": "submitted",
                },
                {
                    "company": "Legacy Bank",
                    "role": "Data Engineer",
                    "source": "Careers",
                    "url": url,
                    "status": "submitted",
                    "resume_file": "applications/legacy/resume.docx",
                },
                path=db,
            )
            with job_store.connection(db) as conn:
                conn.execute(
                    "UPDATE applications SET identity_key = ?",
                    ("identity:legacy bank::data engineer::careers",),
                )

            self.assertTrue(scheduled_reconcile.apply_event({
                "company": "Legacy Bank",
                "role": "Data Engineer",
                "url": url,
                "event": "application_received",
                "received_date": "2026-07-17",
                "subject": "Application received",
                "message_url": "https://outlook.example/message/legacy",
            }, db))

            rows = job_store.tracker_rows(db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["email_status"], "application_received")
            self.assertEqual(rows[0]["resume_file"], "applications/legacy/resume.docx")
            with job_store.connection(db) as conn:
                identity = conn.execute(
                    "SELECT identity_key FROM applications"
                ).fetchone()["identity_key"]
            self.assertEqual(identity, "url:https://example.test/jobs/legacy")

    def test_dashboard_renders_interview_stage_without_follow_up_date(self):
        fields = {
            field: ""
            for field in (
                "date", "company", "role", "source", "url", "status", "resume_file",
                "application_folder", "submitted", "follow_up_date", "stage", "stage_date",
                "next_action", "contact_name", "last_contact_date", "email_status",
                "email_subject", "email_url", "email_last_checked", "notes",
            )
        }
        submitted = fields | {
            "company": "Submitted Co",
            "role": "Data Engineer",
            "status": "submitted",
            "stage": "applied",
        }
        interview = fields | {
            "company": "Interview Co",
            "role": "Senior Data Engineer",
            "status": "interview",
            "stage": "interview_scheduled",
            "stage_date": "2026-07-21",
            "next_action": "Prepare for interview",
        }

        rendered = tracker_report.render([submitted, interview], Path("custom-tracker.csv"))
        panel = rendered.split("<h2>Follow-ups and Next Rounds</h2>", 1)[1].split("</section>", 1)[0]
        self.assertIn("Interview Co", panel)
        self.assertNotIn("Submitted Co", panel)
        self.assertIn('metric-value">1</span><span class="metric-label">Interview pipeline', rendered)

    def test_outcome_calibration_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            for index in range(5):
                job_store.record_application_state(
                    {
                        "company": f"Example {index}",
                        "role": "Data Engineer",
                        "source": "Greenhouse",
                        "url": f"https://example.test/{index}",
                        "role_family": "data_engineering",
                        "status": "interview" if index < 3 else "rejected",
                    },
                    {
                        "company": f"Example {index}",
                        "role": "Data Engineer",
                        "source": "Greenhouse",
                        "url": f"https://example.test/{index}",
                        "status": "interview" if index < 3 else "rejected",
                    },
                    path=db,
                )
            metrics = job_store.outcome_metrics(path=db, min_observations=5)
            group = metrics["groups"][0]
            self.assertTrue(group["calibrated"])
            self.assertLessEqual(abs(group["score_adjustment"]), 5)

    def test_rolling_maintenance_expires_only_unstarted_stale_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "workflow.db"
            stale = datetime.now(timezone.utc) - timedelta(hours=169)
            job_store.upsert_job(
                {
                    "company": "Old Example",
                    "role": "Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/stale",
                    "status": "queued",
                    "posted_at": stale.isoformat(),
                },
                path=db,
            )
            job_store.upsert_job(
                {
                    "company": "Active Example",
                    "role": "Data Engineer",
                    "source": "Greenhouse",
                    "url": "https://example.test/active",
                    "status": "application_started",
                    "posted_at": stale.isoformat(),
                },
                path=db,
            )
            with patch("queue_maintenance.export_legacy_csv") as export_csv:
                result = queue_maintenance.maintain_queue(
                    db_path=db,
                    expire_stale=True,
                    capacity=10,
                    low_watermark=3,
                )
            export_csv.assert_not_called()
            statuses = {row["company"]: row["status"] for row in job_store.queue_rows(db)}
            self.assertEqual(statuses["Old Example"], "outdated")
            self.assertEqual(statuses["Active Example"], "application_started")
            self.assertEqual(len(result["expired"]), 1)


if __name__ == "__main__":
    unittest.main()
