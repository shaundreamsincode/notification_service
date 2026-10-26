"""POST /notifications: intake with idempotency (design D6)."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.errors import ApiError, check_idempotency_key, sanitize_errors
from app.db import get_session
from app.delivery.outcomes import public_error
from app.idempotency import request_hash
from app.models import Notification
from app.schemas import LastError, NotificationAccepted, NotificationRequest, NotificationStatus
from app.templates.registry import TemplateRenderError

router = APIRouter()


def idempotency_key(request: Request) -> str:
    return check_idempotency_key(request)


def _find(session: Session, key: str) -> Any:
    return session.execute(
        select(Notification.uuid, Notification.status, Notification.request_hash).where(
            Notification.idempotency_key == key
        )
    ).first()


def _replay(existing: Any, digest: str) -> NotificationAccepted:
    if existing.request_hash != digest:
        raise ApiError(
            422,
            "idempotency_key_reused",
            "this Idempotency-Key was already used with a different request",
        )
    return NotificationAccepted(uuid=existing.uuid, status=existing.status)


@router.post("/notifications", status_code=202, response_model=NotificationAccepted)
def create_notification(
    body: NotificationRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    session: Session = Depends(get_session),
) -> NotificationAccepted:
    digest = request_hash(body)

    # Key lookup comes before template checks, so a replay never fails because
    # the template has changed since.
    existing = _find(session, key)
    if existing is not None:
        return _replay(existing, digest)

    variant = request.app.state.registry.get(body.template_slug, body.channel)
    if variant is None:
        raise ApiError(
            422,
            "template_not_found",
            f"no template '{body.template_slug}' exists for channel '{body.channel}'",
            [{"field": "template_slug", "message": "no template for this slug and channel"}],
        )
    try:
        context = variant.validate_options(body.template_options)
    except ValidationError as exc:
        raise ApiError(
            422,
            "invalid_template_options",
            "template_options do not match the template's options",
            sanitize_errors(exc.errors(), prefix=("template_options",)),
        ) from None
    try:
        variant.render(context)
    except TemplateRenderError as exc:
        raise ApiError(
            422, "render_failed", f"the template failed to render: {exc}"
        ) from None

    # DO NOTHING instead of catching a unique violation, which would abort the
    # transaction. No row back means a concurrent request won the key.
    inserted = session.execute(
        insert(Notification)
        .values(
            idempotency_key=key,
            request_hash=digest,
            channel=body.channel,
            template_slug=body.template_slug,
            recipient=body.recipient,
            template_options=body.template_options,
            status="pending",
            max_attempts=request.app.state.settings.max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(
            Notification.uuid, Notification.status, Notification.request_hash
        )
    ).first()
    if inserted is not None:
        session.commit()
        return NotificationAccepted(uuid=inserted.uuid, status=inserted.status)

    session.rollback()
    winner = _find(session, key)
    if winner is None:
        # The row that won the key vanished before we could read it back.
        # Not reachable in normal operation; never let it become a 500.
        raise ApiError(
            409, "idempotency_key_conflict", "a concurrent request used this key; retry"
        )
    return _replay(winner, digest)


@router.get("/notifications/{uuid}", response_model=NotificationStatus)
def get_notification(uuid: UUID, session: Session = Depends(get_session)) -> NotificationStatus:
    row = session.execute(
        select(
            Notification.uuid,
            Notification.channel,
            Notification.template_slug,
            Notification.status,
            Notification.attempt_count,
            Notification.last_error,
            Notification.created_at,
            Notification.updated_at,
            Notification.sent_at,
        ).where(Notification.uuid == uuid)
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "no notification with that UUID")
    last_error = None
    if row.last_error is not None:
        code, message = public_error(row.last_error)
        last_error = LastError(code=code, message=message)
    return NotificationStatus(
        uuid=row.uuid,
        channel=row.channel,
        template_slug=row.template_slug,
        status=row.status,
        attempt_count=row.attempt_count,
        last_error=last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        sent_at=row.sent_at,
    )
