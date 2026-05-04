from __future__ import annotations

from typing import Any

from api.models import OutboxEvent


class OutboxService:
    """Writes config-change events into the transactional outbox."""

    @staticmethod
    def emit(
        aggregate_type: str,
        aggregate_id: str | int,
        event_type: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> OutboxEvent:
        return OutboxEvent.objects.create(
            aggregate_type=str(aggregate_type),
            aggregate_id=str(aggregate_id),
            event_type=str(event_type),
            payload=payload or {},
            headers=headers or {},
        )
