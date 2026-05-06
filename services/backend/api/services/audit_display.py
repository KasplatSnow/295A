from __future__ import annotations

from typing import Any


AUDIT_DISPLAY_MAP = {
    "incident.acknowledge": {
        "title": "Incident acknowledged",
        "type": "incident",
    },
    "incident.resolve": {
        "title": "Incident resolved",
        "type": "incident",
    },
    "entity.create": {
        "title": "Entity created",
        "type": "entity",
    },
    "entity.update": {
        "title": "Entity updated",
        "type": "entity",
    },
    "entity.delete": {
        "title": "Entity deleted",
        "type": "entity",
    },
    "entity.toggle_detection": {
        "title": "Entity detection changed",
        "type": "entity",
    },
    "entity.enqueue_processing": {
        "title": "Entity processing queued",
        "type": "system",
    },
    "entity.processing_succeeded": {
        "title": "Entity processing completed",
        "type": "system",
    },
    "entity.processing_failed": {
        "title": "Entity processing failed",
        "type": "error",
    },
    "membership.upserted": {
        "title": "Membership updated",
        "type": "community",
    },
}


def _prettify_action(action: str) -> str:
    return action.replace(".", " ").replace("_", " ").strip().title() or "Activity"


def _infer_type(action: str, target_type: str) -> str:
    if action.endswith("failed"):
        return "error"
    if target_type in {"incident", "camera", "entity", "invitation"}:
        return target_type
    if action.startswith("membership."):
        return "community"
    return "activity"


def present_audit_log(*, action: str, target_type: str, target_id: Any, meta: Any) -> dict[str, str]:
    mapping = AUDIT_DISPLAY_MAP.get(action, {})
    description = ""
    if isinstance(meta, dict):
        description = str(meta.get("message") or "").strip()
    if not description and target_type and target_id:
        description = f"{target_type.title()} #{target_id}"
    return {
        "display_title": str(mapping.get("title") or _prettify_action(action)),
        "display_type": str(mapping.get("type") or _infer_type(action, target_type)),
        "display_description": description,
    }
