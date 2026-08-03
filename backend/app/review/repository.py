from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import IssueProgress, IssueRecord, LinkGate, NegativeFeedbackGroup
from .state import calculate_progress


class ReviewRepositoryError(RuntimeError):
    pass


class ReviewNotFound(ReviewRepositoryError):
    pass


class ConcurrentUpdate(ReviewRepositoryError):
    def __init__(self, current: dict):
        self.current = current
        super().__init__("review record changed concurrently")


class InvalidReviewMutation(ReviewRepositoryError):
    pass


ISSUE_UPDATE_FIELDS = {
    "title",
    "priority",
    "failure_layer",
    "secondary_layers",
    "root_cause",
    "impact_scope",
    "owner",
}


def require_row_version(current: Mapping[str, Any], expected: int) -> None:
    if int(current["row_version"]) != expected:
        raise ConcurrentUpdate(dict(current))


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class PsycopgReviewRepository:
    """Transactional writer for the isolated platform_review schema."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable = psycopg.connect,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._database_url = database_url
        self._connect = connect
        self._now = now

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    @staticmethod
    def _event(
        cursor,
        *,
        issue_id: UUID | str,
        event_type: str,
        actor: str,
        reason: str,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
    ) -> None:
        cursor.execute(
            """
            insert into platform_review.feedback_issue_events
              (issue_id, event_type, actor, reason, before, after)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                issue_id,
                event_type,
                actor,
                reason,
                Jsonb(_jsonable(dict(before or {}))),
                Jsonb(_jsonable(dict(after or {}))),
            ),
        )

    def create_issue(self, data: Mapping[str, Any], *, actor: str, reason: str) -> dict:
        payload = {
            "agent_id": data["agent_id"],
            "origin_turn_key": data.get("origin_turn_key"),
            "title": data["title"],
            "priority": data.get("priority", "P2"),
            "failure_layer": data.get("failure_layer"),
            "secondary_layers": list(data.get("secondary_layers") or []),
            "root_cause": data.get("root_cause", ""),
            "impact_scope": data.get("impact_scope", ""),
            "owner": data.get("owner"),
        }
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    """
                    insert into platform_review.feedback_issues
                      (agent_id, origin_turn_key, title, priority, failure_layer,
                       secondary_layers, root_cause, impact_scope, owner, created_by)
                    values
                      (%(agent_id)s, %(origin_turn_key)s, %(title)s, %(priority)s,
                       %(failure_layer)s, %(secondary_layers)s, %(root_cause)s,
                       %(impact_scope)s, %(owner)s, %(created_by)s)
                    returning *
                    """,
                    {**payload, "created_by": actor},
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=row["id"],
                    event_type="issue_created",
                    actor=actor,
                    reason=reason,
                    after=row,
                )
            return dict(row)
        except Exception as error:
            if isinstance(error, ReviewRepositoryError):
                raise
            raise ReviewRepositoryError("create issue failed") from error

    def update_issue(
        self,
        issue_id: UUID,
        updates: Mapping[str, Any],
        *,
        expected_row_version: int,
        actor: str,
        reason: str,
    ) -> dict:
        unknown = set(updates) - ISSUE_UPDATE_FIELDS
        if unknown:
            raise InvalidReviewMutation(f"unsupported issue fields: {sorted(unknown)}")
        if not updates:
            raise InvalidReviewMutation("issue update is empty")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    "select * from platform_review.feedback_issues where id=%s for update",
                    (issue_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("issue not found")
                require_row_version(before, expected_row_version)
                assignments = [
                    sql.SQL("{} = %s").format(sql.Identifier(field))
                    for field in updates
                ]
                statement = sql.SQL(
                    """
                    update platform_review.feedback_issues
                    set {}, updated_at=now(), row_version=row_version+1
                    where id=%s returning *
                    """
                ).format(sql.SQL(", ").join(assignments))
                values = [
                    list(value) if field == "secondary_layers" else value
                    for field, value in updates.items()
                ]
                after = cursor.execute(statement, (*values, issue_id)).fetchone()
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type="issue_updated",
                    actor=actor,
                    reason=reason,
                    before=before,
                    after=after,
                )
            return dict(after)
        except (ReviewNotFound, ConcurrentUpdate, InvalidReviewMutation):
            raise
        except Exception as error:
            raise ReviewRepositoryError("update issue failed") from error

    def link_turn(
        self,
        issue_id: UUID,
        *,
        agent_id: str,
        source_turn_key: str,
        source_feedback_keys: list[str],
        link_role: str,
        actor: str,
        reason: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    """
                    insert into platform_review.feedback_issue_links
                      (issue_id, agent_id, source_turn_key, source_feedback_keys,
                       link_role, linked_by, link_reason)
                    values (%s, %s, %s, %s, %s, %s, %s)
                    returning *
                    """,
                    (
                        issue_id,
                        agent_id,
                        source_turn_key,
                        sorted(set(source_feedback_keys)),
                        link_role,
                        actor,
                        reason,
                    ),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type="turn_linked",
                    actor=actor,
                    reason=reason,
                    after=row,
                )
            return dict(row)
        except Exception as error:
            raise ReviewRepositoryError("link turn failed") from error

    def move_link(
        self,
        link_id: UUID,
        target_issue_id: UUID,
        *,
        actor: str,
        reason: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    """
                    select * from platform_review.feedback_issue_links
                    where id=%s for update
                    """,
                    (link_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("link not found")
                after = cursor.execute(
                    """
                    update platform_review.feedback_issue_links
                    set issue_id=%s, linked_by=%s, linked_at=now(), link_reason=%s
                    where id=%s returning *
                    """,
                    (target_issue_id, actor, reason, link_id),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=before["issue_id"],
                    event_type="link_moved_out",
                    actor=actor,
                    reason=reason,
                    before=before,
                    after=after,
                )
                self._event(
                    cursor,
                    issue_id=target_issue_id,
                    event_type="link_moved_in",
                    actor=actor,
                    reason=reason,
                    before=before,
                    after=after,
                )
            return dict(after)
        except ReviewNotFound:
            raise
        except Exception as error:
            raise ReviewRepositoryError("move link failed") from error

    def mark_fix_ready(
        self,
        issue_id: UUID,
        *,
        expected_row_version: int,
        actor: str,
        reason: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    "select * from platform_review.feedback_issues where id=%s for update",
                    (issue_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("issue not found")
                require_row_version(before, expected_row_version)
                after = cursor.execute(
                    """
                    update platform_review.feedback_issues
                    set fix_ready_at=now(), updated_at=now(), row_version=row_version+1
                    where id=%s returning *
                    """,
                    (issue_id,),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type="fix_ready",
                    actor=actor,
                    reason=reason,
                    before=before,
                    after=after,
                )
            return dict(after)
        except (ReviewNotFound, ConcurrentUpdate):
            raise
        except Exception as error:
            raise ReviewRepositoryError("mark fix ready failed") from error

    def add_evidence(
        self,
        issue_id: UUID,
        data: Mapping[str, Any],
        *,
        actor: str,
        reason: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                row = cursor.execute(
                    """
                    insert into platform_review.feedback_fix_evidence
                      (issue_id, evidence_type, repository, reference, url, version,
                       commit_sha, release_manifest_ref, environment, observed_at,
                       observed_by, verification_status, verification_details)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            'pending', '{}'::jsonb)
                    returning *
                    """,
                    (
                        issue_id,
                        data["evidence_type"],
                        data.get("repository", ""),
                        data["reference"],
                        data.get("url", ""),
                        data.get("version", ""),
                        data.get("commit_sha", ""),
                        data.get("release_manifest_ref", ""),
                        data.get("environment", ""),
                        data.get("observed_at") or self._now(),
                        actor,
                    ),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type="evidence_added",
                    actor=actor,
                    reason=reason,
                    after=row,
                )
            return dict(row)
        except Exception as error:
            raise ReviewRepositoryError("add evidence failed") from error

    def record_evidence_verification(
        self,
        evidence_id: UUID,
        *,
        status: str,
        details: Mapping[str, Any],
        actor: str,
        reason: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    """
                    select * from platform_review.feedback_fix_evidence
                    where id=%s for update
                    """,
                    (evidence_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("evidence not found")
                after = cursor.execute(
                    """
                    update platform_review.feedback_fix_evidence
                    set verification_status=%s, verification_details=%s
                    where id=%s returning *
                    """,
                    (status, Jsonb(_jsonable(dict(details))), evidence_id),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=before["issue_id"],
                    event_type="evidence_verified" if status == "verified" else "evidence_status_changed",
                    actor=actor,
                    reason=reason,
                    before=before,
                    after=after,
                )
            return dict(after)
        except ReviewNotFound:
            raise
        except Exception as error:
            raise ReviewRepositoryError("verify evidence failed") from error

    def create_or_get_replay(
        self,
        issue_link_id: UUID,
        *,
        idempotency_key: str,
        expected: Mapping[str, Any],
        actor: str,
    ) -> tuple[dict, bool]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                existing = cursor.execute(
                    """
                    select * from platform_review.feedback_replay_runs
                    where issue_link_id=%s and idempotency_key=%s
                    """,
                    (issue_link_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return dict(existing), False
                link = cursor.execute(
                    """
                    select * from platform_review.feedback_issue_links
                    where id=%s and active for update
                    """,
                    (issue_link_id,),
                ).fetchone()
                if link is None:
                    raise ReviewNotFound("active issue link not found")
                attempt = cursor.execute(
                    """
                    select coalesce(max(attempt_no), 0) + 1 as attempt
                    from platform_review.feedback_replay_runs
                    where issue_link_id=%s
                    """,
                    (issue_link_id,),
                ).fetchone()["attempt"]
                row = cursor.execute(
                    """
                    insert into platform_review.feedback_replay_runs
                      (issue_id, issue_link_id, idempotency_key, attempt_no,
                       target_url_fingerprint, expected_version, expected_git_sha,
                       question, context_snapshot, attachment_manifest)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning *
                    """,
                    (
                        link["issue_id"],
                        issue_link_id,
                        idempotency_key,
                        attempt,
                        expected["target_url_fingerprint"],
                        expected["expected_version"],
                        expected["expected_git_sha"],
                        expected["question"],
                        Jsonb(_jsonable(expected.get("context_snapshot", []))),
                        Jsonb(_jsonable(expected.get("attachment_manifest", []))),
                    ),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=link["issue_id"],
                    event_type="replay_started",
                    actor=actor,
                    reason="",
                    after={"replay_id": row["id"], "attempt_no": attempt},
                )
            return dict(row), True
        except ReviewNotFound:
            raise
        except Exception as error:
            raise ReviewRepositoryError("create replay failed") from error

    def finish_replay(
        self,
        replay_id: UUID,
        result: Mapping[str, Any],
        *,
        actor: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    """
                    select * from platform_review.feedback_replay_runs
                    where id=%s for update
                    """,
                    (replay_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("replay not found")
                if before["execution_status"] != "running":
                    return dict(before)
                after = cursor.execute(
                    """
                    update platform_review.feedback_replay_runs set
                      actual_version=%s, actual_git_sha=%s,
                      configured_model=%s, actual_model=%s, actual_model_source=%s,
                      answer=%s, sources=%s, done=%s, trace_id=%s, duration_ms=%s,
                      execution_status=%s, runtime_gate=%s,
                      runtime_failure_reason=%s, completed_at=now()
                    where id=%s returning *
                    """,
                    (
                        result.get("actual_version", ""),
                        result.get("actual_git_sha", ""),
                        result.get("configured_model", ""),
                        result.get("actual_model", ""),
                        result.get("actual_model_source", ""),
                        result.get("answer", ""),
                        Jsonb(_jsonable(result.get("sources", []))),
                        Jsonb(_jsonable(result.get("done", {}))),
                        result.get("trace_id", ""),
                        result.get("duration_ms"),
                        result["execution_status"],
                        result["runtime_gate"],
                        result.get("runtime_failure_reason", ""),
                        replay_id,
                    ),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=before["issue_id"],
                    event_type="replay_finished",
                    actor=actor,
                    reason=result.get("runtime_failure_reason", ""),
                    before={"execution_status": "running"},
                    after={
                        "replay_id": replay_id,
                        "execution_status": after["execution_status"],
                        "runtime_gate": after["runtime_gate"],
                    },
                )
            return dict(after)
        except ReviewNotFound:
            raise
        except Exception as error:
            raise ReviewRepositoryError("finish replay failed") from error

    def review_replay(
        self,
        replay_id: UUID,
        *,
        verdict: str,
        method: str,
        reviewer: str,
        reason: str,
        actor: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    """
                    select * from platform_review.feedback_replay_runs
                    where id=%s for update
                    """,
                    (replay_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("replay not found")
                if before["runtime_gate"] != "passed":
                    raise InvalidReviewMutation("runtime gate has not passed")
                after = cursor.execute(
                    """
                    update platform_review.feedback_replay_runs
                    set semantic_verdict=%s, review_method=%s, reviewer=%s,
                        review_reason=%s, reviewed_at=now()
                    where id=%s returning *
                    """,
                    (verdict, method, reviewer, reason, replay_id),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=before["issue_id"],
                    event_type="replay_semantic_reviewed",
                    actor=actor,
                    reason=reason,
                    before={"semantic_verdict": before["semantic_verdict"]},
                    after={
                        "replay_id": replay_id,
                        "semantic_verdict": verdict,
                        "review_method": method,
                        "reviewer": reviewer,
                    },
                )
            return dict(after)
        except (ReviewNotFound, InvalidReviewMutation):
            raise
        except Exception as error:
            raise ReviewRepositoryError("review replay failed") from error

    def set_disposition(
        self,
        issue_id: UUID,
        *,
        disposition: str,
        canonical_issue_id: UUID | None,
        owner: str | None,
        disposition_reason: str,
        expected_row_version: int,
        actor: str,
    ) -> dict:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                before = cursor.execute(
                    "select * from platform_review.feedback_issues where id=%s for update",
                    (issue_id,),
                ).fetchone()
                if before is None:
                    raise ReviewNotFound("issue not found")
                require_row_version(before, expected_row_version)
                after = cursor.execute(
                    """
                    update platform_review.feedback_issues
                    set disposition=%s, canonical_issue_id=%s, owner=coalesce(%s, owner),
                        disposition_reason=%s, updated_at=now(), row_version=row_version+1
                    where id=%s returning *
                    """,
                    (
                        disposition,
                        canonical_issue_id,
                        owner,
                        disposition_reason,
                        issue_id,
                    ),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type="issue_disposition_set",
                    actor=actor,
                    reason=disposition_reason,
                    before=before,
                    after=after,
                )
            return dict(after)
        except (ReviewNotFound, ConcurrentUpdate):
            raise
        except Exception as error:
            raise ReviewRepositoryError("set disposition failed") from error

    def merge_issue(
        self,
        source_issue_id: UUID,
        target_issue_id: UUID,
        *,
        expected_row_version: int,
        actor: str,
        reason: str,
    ) -> dict:
        if source_issue_id == target_issue_id:
            raise InvalidReviewMutation("issue cannot merge into itself")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                source = cursor.execute(
                    "select * from platform_review.feedback_issues where id=%s for update",
                    (source_issue_id,),
                ).fetchone()
                target = cursor.execute(
                    "select * from platform_review.feedback_issues where id=%s for update",
                    (target_issue_id,),
                ).fetchone()
                if source is None or target is None:
                    raise ReviewNotFound("merge issue not found")
                require_row_version(source, expected_row_version)
                links = cursor.execute(
                    """
                    select * from platform_review.feedback_issue_links
                    where issue_id=%s and active for update
                    """,
                    (source_issue_id,),
                ).fetchall()
                for link in links:
                    existing = cursor.execute(
                        """
                        select * from platform_review.feedback_issue_links
                        where issue_id=%s and agent_id=%s and source_turn_key=%s and active
                        for update
                        """,
                        (target_issue_id, link["agent_id"], link["source_turn_key"]),
                    ).fetchone()
                    if existing:
                        keys = sorted(set(existing["source_feedback_keys"]) | set(link["source_feedback_keys"]))
                        cursor.execute(
                            """
                            update platform_review.feedback_issue_links
                            set source_feedback_keys=%s where id=%s
                            """,
                            (keys, existing["id"]),
                        )
                        cursor.execute(
                            "update platform_review.feedback_issue_links set active=false where id=%s",
                            (link["id"],),
                        )
                    else:
                        cursor.execute(
                            "update platform_review.feedback_issue_links set issue_id=%s where id=%s",
                            (target_issue_id, link["id"]),
                        )
                after = cursor.execute(
                    """
                    update platform_review.feedback_issues
                    set disposition='duplicate', canonical_issue_id=%s,
                        disposition_reason=%s, updated_at=now(), row_version=row_version+1
                    where id=%s returning *
                    """,
                    (target_issue_id, reason, source_issue_id),
                ).fetchone()
                self._event(
                    cursor,
                    issue_id=source_issue_id,
                    event_type="issue_merged",
                    actor=actor,
                    reason=reason,
                    before=source,
                    after=after,
                )
                self._event(
                    cursor,
                    issue_id=target_issue_id,
                    event_type="issue_absorbed",
                    actor=actor,
                    reason=reason,
                    before={"source_issue_id": source_issue_id},
                    after={"target_issue_id": target_issue_id},
                )
            return dict(after)
        except (ReviewNotFound, ConcurrentUpdate, InvalidReviewMutation):
            raise
        except Exception as error:
            raise ReviewRepositoryError("merge issue failed") from error

    def _load_issue_detail(
        self,
        cursor,
        issue_id: UUID,
        *,
        lock_issue: bool = False,
    ) -> dict | None:
        issue_sql = "select * from platform_review.feedback_issues where id=%s"
        if lock_issue:
            issue_sql += " for update"
        issue = cursor.execute(issue_sql, (issue_id,)).fetchone()
        if issue is None:
            return None
        links = cursor.execute(
            """
            select * from platform_review.feedback_issue_links
            where issue_id=%s order by linked_at, id
            """,
            (issue_id,),
        ).fetchall()
        evidence = cursor.execute(
            """
            select * from platform_review.feedback_fix_evidence
            where issue_id=%s order by observed_at, id
            """,
            (issue_id,),
        ).fetchall()
        replays = cursor.execute(
            """
            select * from platform_review.feedback_replay_runs
            where issue_id=%s order by started_at, attempt_no
            """,
            (issue_id,),
        ).fetchall()
        events = cursor.execute(
            """
            select * from platform_review.feedback_issue_events
            where issue_id=%s order by created_at, id
            """,
            (issue_id,),
        ).fetchall()
        detail = {
            "issue": dict(issue),
            "links": [dict(row) for row in links],
            "evidence": [dict(row) for row in evidence],
            "replays": [dict(row) for row in replays],
            "events": [dict(row) for row in events],
        }
        detail["progress"] = self._calculate_detail_progress(detail)
        return detail

    def get_issue_detail(self, issue_id: UUID) -> dict | None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                return self._load_issue_detail(cursor, issue_id)
        except Exception as error:
            raise ReviewRepositoryError("get issue detail failed") from error

    @staticmethod
    def _calculate_detail_progress(detail: Mapping[str, Any]) -> IssueProgress:
        raw_issue = detail["issue"]
        evidence = detail["evidence"]
        verified_merges = [
            item for item in evidence
            if item["evidence_type"] == "merge"
            and item["verification_status"] == "verified"
        ]
        deployments = [
            item for item in evidence
            if item["evidence_type"] == "deployment"
            and item["verification_status"] == "verified"
            and bool((item.get("verification_details") or {}).get("contains_merge"))
        ]
        previous_status = None
        for event in reversed(detail["events"]):
            status = (event.get("after") or {}).get("status")
            if status:
                previous_status = status
                break
        issue = IssueRecord(
            id=raw_issue["id"],
            agent_id=raw_issue["agent_id"],
            title=raw_issue["title"],
            priority=raw_issue["priority"],
            failure_layer=raw_issue["failure_layer"],
            secondary_layers=tuple(raw_issue["secondary_layers"] or ()),
            root_cause=raw_issue["root_cause"],
            impact_scope=raw_issue["impact_scope"],
            owner=raw_issue["owner"],
            fix_ready=raw_issue["fix_ready_at"] is not None,
            verified_merge=bool(verified_merges),
            verified_deployment=bool(deployments),
            merge_ancestor=bool(deployments),
            disposition=raw_issue["disposition"],
            previous_status=previous_status,
            row_version=int(raw_issue["row_version"]),
        )
        latest_by_link: dict[UUID, dict] = {}
        for replay in detail["replays"]:
            latest_by_link[replay["issue_link_id"]] = replay
        links: list[LinkGate] = []
        for raw_link in detail["links"]:
            replay = latest_by_link.get(raw_link["id"])
            echo = ((replay or {}).get("done") or {}).get("loop", {}).get(
                "provider_model_echo", {}
            )
            links.append(LinkGate(
                id=raw_link["id"],
                active=bool(raw_link["active"]),
                link_role=raw_link["link_role"],
                runtime_gate_passed=bool(replay and replay["runtime_gate"] == "passed"),
                runtime_failure_reason=(replay or {}).get("runtime_failure_reason", ""),
                build_identity_matches=(
                    None if replay is None
                    else replay["actual_git_sha"] == replay["expected_git_sha"]
                ),
                model_echo_available=(
                    None if replay is None
                    else bool(echo.get("complete") and echo.get("consistent") and replay["actual_model"])
                ),
                actual_model_matches=(
                    None if replay is None
                    else bool(replay["actual_model"] and replay["actual_model"] == replay["configured_model"])
                ),
                semantic_verdict=(replay or {}).get("semantic_verdict", "pending"),
                review_method=(replay or {}).get("review_method"),
                reviewer=(replay or {}).get("reviewer"),
                review_reason=(replay or {}).get("review_reason", ""),
            ))
        return calculate_progress(issue, links)

    def recalculate_and_record_transition(
        self,
        issue_id: UUID,
        *,
        actor: str,
        reason: str = "",
    ) -> IssueProgress:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                detail = self._load_issue_detail(
                    cursor,
                    issue_id,
                    lock_issue=True,
                )
                if detail is None:
                    raise ReviewNotFound("issue not found")
                progress: IssueProgress = detail["progress"]
                previous = None
                for event in reversed(detail["events"]):
                    previous = (event.get("after") or {}).get("status")
                    if previous:
                        break
                if previous == progress.status:
                    return progress
                event_type = (
                    "issue_closed" if progress.status == "closed"
                    else "issue_reopened" if previous == "closed"
                    else "issue_status_changed"
                )
                self._event(
                    cursor,
                    issue_id=issue_id,
                    event_type=event_type,
                    actor=actor,
                    reason=reason,
                    before={"status": previous},
                    after={
                        "status": progress.status,
                        "missing_gates": progress.missing_gates,
                    },
                )
            return progress
        except ReviewNotFound:
            raise
        except Exception as error:
            raise ReviewRepositoryError("recalculate issue progress failed") from error

    def list_negative_feedback_groups(self) -> list[NegativeFeedbackGroup]:
        with self._connection() as connection, connection.cursor() as cursor:
            rows = cursor.execute(
                """
                select f.agent_id, f.turn_key,
                  coalesce(max(t.question), '') as question,
                  array_agg(f.feedback_key order by f.created_at, f.feedback_key) as feedback_keys
                from platform_read.feedback f
                left join platform_read.turns t on t.turn_key=f.turn_key
                where f.sentiment='negative'
                group by f.agent_id, f.turn_key
                order by min(f.created_at), f.agent_id, f.turn_key
                """
            ).fetchall()
        return [
            NegativeFeedbackGroup(
                agent_id=row["agent_id"],
                turn_key=row["turn_key"],
                question=row["question"],
                feedback_keys=tuple(row["feedback_keys"]),
            )
            for row in rows
        ]

    def backfill_negative_group(
        self,
        group: NegativeFeedbackGroup,
        *,
        actor: str,
    ) -> tuple[bool, bool, bool]:
        """Upsert one negative turn; return issue/link/event creation flags."""
        title = (group.question.strip() or group.turn_key)[:80]
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                issue = cursor.execute(
                    """
                    insert into platform_review.feedback_issues
                      (agent_id, origin_turn_key, title, priority, created_by)
                    values (%s, %s, %s, 'P2', %s)
                    on conflict (agent_id, origin_turn_key)
                      where origin_turn_key is not null do nothing
                    returning *
                    """,
                    (group.agent_id, group.turn_key, title, actor),
                ).fetchone()
                issue_created = issue is not None
                if issue is None:
                    issue = cursor.execute(
                        """
                        select * from platform_review.feedback_issues
                        where agent_id=%s and origin_turn_key=%s
                        """,
                        (group.agent_id, group.turn_key),
                    ).fetchone()
                existing_link = cursor.execute(
                    """
                    select * from platform_review.feedback_issue_links
                    where issue_id=%s and agent_id=%s and source_turn_key=%s and active
                    for update
                    """,
                    (issue["id"], group.agent_id, group.turn_key),
                ).fetchone()
                link_created = False
                if existing_link is None:
                    cursor.execute(
                        """
                        insert into platform_review.feedback_issue_links
                          (issue_id, agent_id, source_turn_key, source_feedback_keys,
                           link_role, linked_by, link_reason)
                        values (%s, %s, %s, %s, 'primary', %s, 'negative feedback backfill')
                        """,
                        (
                            issue["id"],
                            group.agent_id,
                            group.turn_key,
                            sorted(set(group.feedback_keys)),
                            actor,
                        ),
                    )
                    link_created = True
                else:
                    keys = sorted(set(existing_link["source_feedback_keys"]) | set(group.feedback_keys))
                    if keys != list(existing_link["source_feedback_keys"]):
                        cursor.execute(
                            """
                            update platform_review.feedback_issue_links
                            set source_feedback_keys=%s where id=%s
                            """,
                            (keys, existing_link["id"]),
                        )
                event_created = False
                if issue_created:
                    self._event(
                        cursor,
                        issue_id=issue["id"],
                        event_type="issue_backfilled",
                        actor=actor,
                        reason="negative feedback backfill",
                        after={
                            "turn_key": group.turn_key,
                            "feedback_keys": group.feedback_keys,
                        },
                    )
                    event_created = True
            return issue_created, link_created, event_created
        except Exception as error:
            raise ReviewRepositoryError("negative feedback backfill failed") from error

    def list_inbox(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._connection() as connection, connection.cursor() as cursor:
            rows = cursor.execute(
                """
                select f.agent_id, f.turn_key, max(t.question) as question,
                  max(t.answer) as answer,
                  array_agg(f.feedback_key order by f.created_at, f.feedback_key) as feedback_keys,
                  min(f.created_at) as first_feedback_at
                from platform_read.feedback f
                left join platform_read.turns t on t.turn_key=f.turn_key
                where f.sentiment='negative'
                  and not exists (
                    select 1 from platform_review.feedback_issue_links link
                    where link.agent_id=f.agent_id and link.source_turn_key=f.turn_key
                      and link.active
                  )
                group by f.agent_id, f.turn_key
                order by min(f.created_at), f.agent_id, f.turn_key
                limit %s offset %s
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_issues(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._connection() as connection, connection.cursor() as cursor:
            rows = cursor.execute(
                """
                select * from platform_review.feedback_issues
                order by updated_at desc, id limit %s offset %s
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def overview(self) -> dict:
        with self._connection() as connection, connection.cursor() as cursor:
            source = cursor.execute(
                """
                select count(*) as feedback_rows,
                  count(*) filter (where sentiment='negative') as negative_rows,
                  count(distinct (agent_id, turn_key)) filter (where sentiment='negative') as negative_turns,
                  count(*) filter (where sentiment='positive') as positive_rows
                from platform_read.feedback
                """
            ).fetchone()
            issues = cursor.execute(
                """
                select disposition, count(*) as count
                from platform_review.feedback_issues group by disposition
                """
            ).fetchall()
        return {
            **dict(source),
            "dispositions": {row["disposition"]: int(row["count"]) for row in issues},
        }
