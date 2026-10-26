"""POST /notifications: intake with idempotency (design D6)."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.errors import ApiError, check_idempotency_key, sanitize_errors
from app.db import get_session
from app.idempotency import request_hash
from app.models import Notification
from app.schemas import NotificationAccepted, NotificationRequest
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
    assert winner is not None  # the conflicting row was committed by the other request
    return _replay(winner, digest)
