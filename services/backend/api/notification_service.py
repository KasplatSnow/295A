"""
Notification service for broadcasting alerts/incidents to all tenant members.

This service provides:
- Real-time browser delivery to all connected tenant members
- Notification history storage
- Multiple notification channels (realtime, future: push, SMS, etc.)
"""

import logging
import uuid
from typing import Optional, Tuple, List, Dict
from datetime import datetime

from django.conf import settings
from django.core.mail import send_mail as django_send_mail
from django.utils import timezone

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Incident, Alert, Membership, NotificationChannel, severity_level_for_value

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Service for sending notifications to all members of a tenant.
    
    Usage:
        from .notification_service import NotificationService
        from django.db import transaction

        transaction.on_commit(lambda: NotificationService.broadcast_incident(instance))
    """

    @staticmethod
    def get_channel_layer():
        """Get the Django Channels channel layer."""
        try:
            return get_channel_layer()
        except Exception as e:
            logger.warning(f"Channel layer not available: {e}")
            return None

    @classmethod
    def broadcast_incident(cls, incident: Incident) -> dict:
        """
        Immediate broadcast of an incident (Real-time WS).
        Heavy tasks (Email/Push/Batching) should be moved to backfill_incident.
        """
        tenant = incident.tenant
        
        # 1. Build notification payload
        notification = cls._build_incident_notification(incident)

        # 2. Push to channel layer INLINE for immediate feedback
        ws_result = cls._broadcast_to_channel(
            tenant_id=tenant.id,
            notification_type="incident",
            data={
                **notification,
                "type": "NEW_NOTIFICATION_PING", # Fast ping, client can fetch details
            },
        )
        
        logger.info(
            "broadcast_incident (ping) incident=%s tenant=%s ws=%s",
            incident.pk, tenant.id, ws_result,
        )
        return {"realtime": ws_result}

    @classmethod
    def backfill_incident(cls, incident_id: str) -> dict:
        """
        Background task to create Alert records and send Email/Push.
        """
        try:
            incident = Incident.objects.select_related("tenant", "camera").get(pk=incident_id)
        except Incident.DoesNotExist:
            return {"error": "incident_not_found"}

        tenant = incident.tenant
        results = {"alerts_created": 0, "email": None, "push": None}

        # 1. Get settings
        try:
            channel_settings = NotificationChannel.objects.get(tenant=tenant)
        except NotificationChannel.DoesNotExist:
            channel_settings = None

        # 2. Build payload
        notification = cls._build_incident_notification(incident)

        # 3. Create alert records (Serialized to avoid duplicates)
        count, alert_mapping = cls._create_alerts_for_members(incident, notification)
        results["alerts_created"] = count

        # 4. Email notification
        if channel_settings and channel_settings.email_enabled:
            email_result = cls._send_email_notification(incident, channel_settings)
            results["email"] = email_result

        # 5. Push notification (FCM)
        if channel_settings and channel_settings.push_enabled:
            push_result = cls._send_push_notification(incident, channel_settings)
            results["push"] = push_result

        # 6. Secondary WS push with full counts
        user_counts = {
            str(u_id): Alert.objects.filter(
                user_id=u_id,
                incident__tenant_id=tenant.id,
                delivered_at__isnull=True,
            ).count()
            for u_id in alert_mapping.keys()
        }

        cls._broadcast_to_channel(
            tenant_id=tenant.id,
            notification_type="incident",
            data={
                **notification,
                "type": "NEW_NOTIFICATION_DETAILS",
                "alert_ids_by_user": alert_mapping,
                "unread_counts_by_user": user_counts,
            },
        )

        return results

    @classmethod
    def broadcast_message(
        cls,
        tenant_id: int,
        title: str,
        message: str,
        notification_type: str = "broadcast",
        data: Optional[dict] = None
    ) -> dict:
        """
        Broadcast a custom message to all members of a tenant.
        
        Args:
            tenant_id: The tenant ID
            title: Notification title
            message: Notification message body
            notification_type: Type of notification (broadcast, system, etc.)
            data: Additional data payload
            
        Returns:
            dict with broadcast status
        """
        notification = {
            "type": "notification",
            "notification_type": notification_type,
            "title": title,
            "message": message,
            "data": data or {},
            "created_at": timezone.now().isoformat(),
        }

        result = cls._broadcast_to_channel(
            tenant_id=tenant_id,
            notification_type=notification_type,
            data=notification
        )

        logger.info(f"Broadcast message to tenant {tenant_id}: {title}")

        return result

    @classmethod
    def _broadcast_to_channel(
        cls,
        tenant_id: int,
        notification_type: str,
        data: dict
    ) -> str:
        """
        Send notification to Django Channels group for tenant.
        
        Args:
            tenant_id: The tenant ID (used for group name)
            notification_type: Type of notification
            data: Notification payload
            
        Returns:
            "sent" if successful, error message otherwise
        """
        group_name = f"tenant_notifications_{tenant_id}"

        # On Windows, async_to_sync(channel_layer.group_send) often deadlocks
        # when run inside synchronous management commands (subscribe_incidents)
        # or synchronous webhooks. We bypass it by natively publishing to the
        # Redis backend using the same wire format as channels_redis 4.x.
        import sys
        if sys.platform == "win32":
            try:
                import redis as _redis_mod
                import msgpack
                import time as _time
                import secrets
                from server.redis_runtime import resolve_backend_redis_settings

                _settings = resolve_backend_redis_settings()
                if _settings.url:
                    client = _redis_mod.from_url(_settings.url)
                else:
                    client = _redis_mod.Redis(
                        host=_settings.host,
                        port=_settings.port,
                        db=_settings.db,
                        password=_settings.password or None,
                        socket_timeout=5,
                    )

                # channels_redis 4.x stores groups under "asgi:group:<name>"
                # IMPORTANT: The group key uses "asgi:" with colon, but the
                # per-channel message key uses "asgi" WITHOUT colon (prefix is
                # concatenated directly with the channel name).
                group_key = f"asgi:group:{group_name}"
                channels = client.zrange(group_key, 0, -1)

                if not channels:
                    logger.debug(
                        "Direct push: no channels in group %s (0 WS clients connected). "
                        "Hydration polling will catch up.",
                        group_name,
                    )
                    try:
                        client.close()
                    except Exception:
                        pass
                    # Do NOT fall through to async_to_sync on Windows — it deadlocks.
                    return "no_ws_clients"

                for channel_bytes in channels:
                    channel_name = channel_bytes.decode("utf-8")

                    # Resolve the per-channel Redis key exactly as channels_redis does:
                    # channel_key = prefix + non_local_name(channel)
                    # where prefix is "asgi" (NO colon!) and non_local strips
                    # everything after "!" but keeps the "!" marker.
                    non_local = channel_name
                    if "!" in channel_name:
                        non_local = channel_name.split("!")[0] + "!"
                    channel_key = "asgi" + non_local

                    # Build message matching channels_redis 4.x wire format.
                    # __asgi_channel__ must be a LIST (channels_redis maps
                    # multiple channels to the same redis key and expects a list).
                    msg = {
                        "__asgi_channel__": [channel_name],
                        "type": "notification_message",
                        "data": data,
                    }
                    # channels_redis 4.x prepends 12 random bytes to msgpack payload
                    packed = msgpack.packb(msg)
                    payload = secrets.token_bytes(12) + packed

                    client.zadd(channel_key, {payload: _time.time()})
                    client.expire(channel_key, 60)

                logger.info(
                    "Direct push succeeded for group %s to %d channel(s).",
                    group_name, len(channels),
                )
                try:
                    client.close()
                except Exception:
                    pass
                return "sent"
            except Exception as e:
                logger.error(
                    "Failed direct synchronous Redis push for group %s: %s",
                    group_name, e,
                )
                # Fall through to channel_layer as last resort
                pass

        channel_layer = cls.get_channel_layer()
        if not channel_layer:
            return "channel_layer_unavailable"

        try:
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    "type": "notification_message",
                    "data": data
                }
            )
            return "sent"
        except Exception as e:
            logger.error(f"Failed to broadcast to channel {group_name}: {e}")
            return f"error: {str(e)}"

    @classmethod
    def _build_incident_notification(
        cls,
        incident: Incident
    ) -> dict:
        """Build notification payload from an incident."""
        severity_labels = {1: "Low", 2: "Medium-Low", 3: "Medium", 4: "High", 5: "Critical"}
        
        severity_level = severity_level_for_value(incident.severity)
        notification = {
            "type": "notification",
            "notification_type": "incident",
            "title": f"🚨 {incident.get_type_display()} Detected",
            "message": f"{severity_labels.get(incident.severity, 'Unknown')} severity incident at {incident.camera.name}",
            "data": {
                "incident_id": str(incident.id),
                "type": incident.type,
                "status": incident.status,
                "severity": incident.severity,
                "severity_level": severity_level,
                "camera_id": incident.camera_id,
                "camera_name": incident.camera.name,
                "stream_path": getattr(incident.camera, 'stream_path', None),
                "started_at": incident.started_at.isoformat() if incident.started_at else None,
                "details": incident.details,
            },
            "severity": incident.severity,
            "severity_level": severity_level,
            "created_at": timezone.now().isoformat(),
        }
        
        return notification

    @classmethod
    def _create_alerts_for_members(cls, incident: Incident, notification: dict) -> Tuple[int, Dict[str, str]]:
        """
        Creates Alert records for all members of the tenant using atomic bulk_create.
        Returns (count, user_to_alert_map).
        """
        import json
        from ai_integration.redis_queue import get_redis_client
        redis_client = get_redis_client()
        tenant_id_str = str(incident.tenant.id)
        route_key = f"route:{tenant_id_str}:{tenant_id_str}:{incident.type}:{incident.severity}"
        
        target_user_ids = []
        try:
            cached_raw = redis_client.get(route_key)
            if cached_raw:
                target_user_ids = json.loads(cached_raw).get("push", [])
                logger.info("Notification Route Cache HIT: %s (recipients=%d)", route_key, len(target_user_ids))
            else:
                logger.info("Notification Route Cache MISS: %s", route_key)
                cached_raw = None
        except Exception as exc:
            logger.warning("Redis route projection error: %s", exc)
            cached_raw = None

        if not cached_raw:
            # Fallback: scan Postgres
            members = Membership.objects.filter(tenant=incident.tenant).select_related("user", "user__profile")
            for member in members:
                if member.user.profile.allows_instant_notification(incident.severity):
                    target_user_ids.append(str(member.user.id))
        
        alerts_to_create = []
        user_to_alert_map = {}
        
        from django.db import transaction
        
        with transaction.atomic():
            # Lock the incident row to serialize concurrent broadcast threads
            # and prevent duplicate Alert creation.
            try:
                Incident.objects.select_for_update().get(pk=incident.pk)
            except Incident.DoesNotExist:
                return 0, {}

            # Prevent duplicates: Check existing alerts for this incident
            existing_alerts = Alert.objects.filter(incident=incident).values_list("user_id", "id")
            existing_alert_map = {str(uid): str(aid) for uid, aid in existing_alerts if uid is not None}
            
            for user_id_str in target_user_ids:
                if user_id_str in existing_alert_map:
                    user_to_alert_map[user_id_str] = existing_alert_map[user_id_str]
                    continue

                alert_id = uuid.uuid4()
                alert = Alert(
                    id=alert_id,
                    incident=incident,
                    user_id=user_id_str,
                    channel="realtime",
                    payload={
                        "title": notification["title"],
                        "message": notification["message"],
                        "data": notification.get("data", {}),
                        "alert_id": str(alert_id),
                        "user_id": user_id_str,
                    }
                )
                alerts_to_create.append(alert)
                user_to_alert_map[user_id_str] = str(alert_id)
                
            if alerts_to_create:
                Alert.objects.bulk_create(alerts_to_create)
            
        return len(alerts_to_create), user_to_alert_map

    @classmethod
    def _send_email_notification(
        cls,
        incident: Incident,
        channel_settings: NotificationChannel
    ) -> str:
        """Send email notification for an incident."""
        if not channel_settings.email_recipients:
            return "no_recipients"

        try:
            subject = f"[VigilZone] {incident.get_type_display()} — Severity {incident.severity}"
            body = (
                f"Incident #{incident.pk}\n"
                f"Type: {incident.get_type_display()}\n"
                f"Camera: {incident.camera.name}\n"
                f"Severity: {incident.severity}/5\n"
                f"Time: {incident.started_at}\n"
                f"Details: {incident.details.get('message', '')}"
            )

            django_send_mail(
                subject=subject,
                message=body,
                from_email=None,
                recipient_list=channel_settings.email_recipients,
            )
            return "sent"
        except Exception as e:
            logger.warning(f"Email notification failed: {e}")
            return f"error: {str(e)}"

    @classmethod
    def _send_push_notification(
        cls,
        incident: Incident,
        channel_settings: NotificationChannel
    ) -> str:
        """Send push notification via FCM."""
        if not channel_settings.fcm_tokens:
            return "no_tokens"

        # TODO: Implement FCM push notification
        # This requires firebase-admin SDK and service account credentials
        logger.info(f"Push notification would be sent to {len(channel_settings.fcm_tokens)} devices")
        return "not_implemented"

    @classmethod
    def notify_specific_users(
        cls,
        tenant_id: int,
        user_ids: list,
        title: str,
        message: str,
        notification_type: str = "direct",
        data: Optional[dict] = None
    ) -> dict:
        """
        Send notification to specific users (not all tenant members).
        
        Args:
            tenant_id: The tenant ID
            user_ids: List of user IDs to notify
            title: Notification title
            message: Notification message
            notification_type: Type of notification
            data: Additional data
            
        Returns:
            dict with status
        """
        notification = {
            "type": "notification",
            "notification_type": notification_type,
            "title": title,
            "message": message,
            "data": data or {},
            "created_at": timezone.now().isoformat(),
        }

        # Broadcast to entire tenant (channel layer broadcasts to all)
        # In a more complex implementation, you could track individual user groups
        result = cls._broadcast_to_channel(
            tenant_id=tenant_id,
            notification_type=notification_type,
            data=notification
        )

        return {
            "users_notified": len(user_ids),
            "status": result
        }


# ── Integration with existing dispatch_notifications ───────────────

def dispatch_notifications(incident: Incident):
    """
    Legacy function for backwards compatibility.
    Now delegates to the NotificationService.
    """
    NotificationService.broadcast_incident(incident)
