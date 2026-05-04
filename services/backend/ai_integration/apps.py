import logging
import os
import threading

from django.apps import AppConfig
from server.runtime_services import get_ai_base_url, get_public_base_url

logger = logging.getLogger(__name__)


def _auto_register_webhook():
    """Best-effort webhook registration on startup (non-blocking)."""
    import time
    time.sleep(10)  # Give AI module time to boot

    import requests

    ai_base = get_ai_base_url()
    public_url = get_public_base_url()
    webhook_secret = os.getenv("AI_WEBHOOK_SECRET", "")
    callback_url = f"{public_url}/api/ai/webhook/receive/"

    payload = {"url": callback_url, "events": ["alert.created"]}
    if webhook_secret:
        payload["secret"] = webhook_secret

    for attempt in range(3):
        try:
            resp = requests.post(f"{ai_base}/webhooks", json=payload, timeout=10)
            if resp.status_code in (200, 201):
                data = resp.json()
                webhook_id = data.get("id", "")
                if webhook_id:
                    try:
                        from api.models import ServiceWebhook
                        from api.services.webhook_registry_service import WebhookRegistryService

                        WebhookRegistryService().register_webhook(
                            webhook_id=webhook_id,
                            url=callback_url,
                            events=list(payload.get("events") or []),
                            active=True,
                            has_secret=bool(webhook_secret),
                            source=ServiceWebhook.Source.BACKEND,
                            metadata={"managed_by": "ai_integration.apps"},
                        )
                    except Exception as sync_exc:
                        logger.warning("Failed to persist webhook registration locally: %s", sync_exc)
                logger.info(
                    "Auto-registered webhook with AI module: id=%s url=%s",
                    data.get("id", "?"),
                    callback_url,
                )
                return
            logger.warning("AI webhook registration returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.debug("Webhook registration attempt %d failed: %s", attempt + 1, e)
        time.sleep(5)

    logger.warning(
        "Could not auto-register webhook after 3 attempts. "
        "Run 'python manage.py register_ai_webhook' manually."
    )


class AiIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai_integration"
    verbose_name = "AI Integration"

    def ready(self):
        # Webhook registration is an explicit fallback path now.
        debug_mode = os.getenv("DJANGO_DEBUG", "1") not in ("0", "false", "False")
        auto_register = os.getenv("AI_AUTO_REGISTER_WEBHOOK", "0")
        auto_register_enabled = auto_register.lower() in ("1", "true", "yes")

        if auto_register_enabled and (os.getenv("RUN_MAIN") == "true" or not debug_mode):
            thread = threading.Thread(target=_auto_register_webhook, daemon=True)
            thread.start()
