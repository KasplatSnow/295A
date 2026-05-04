from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.db import models
from .models import Profile, Incident, Camera, Membership, NotificationChannel

User = get_user_model()

def trigger_route_projection_generation():
    from django.db import transaction
    import threading
    from django.core.management import call_command
    
    def _generate():
        try:
            call_command("generate_route_projection")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to generate routes: %s", exc)

    transaction.on_commit(lambda: threading.Thread(target=_generate, daemon=True).start())

@receiver(post_save, sender=Membership)
@receiver(post_delete, sender=Membership)
@receiver(post_save, sender=Profile)
@receiver(post_save, sender=NotificationChannel)
@receiver(post_delete, sender=NotificationChannel)
def sync_routing_projections_on_change(sender, instance, **kwargs):
    trigger_route_projection_generation()



@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
    else:
        # only attempt to save if profile exists (avoid AttributeError)
        if hasattr(instance, "profile"):
            instance.profile.save()


@receiver(post_save, sender=Incident)
def broadcast_incident_notification(sender, instance, created, **kwargs):
    """
    Automatically broadcast notifications when an Incident is created.
    This ensures notifications are sent regardless of whether the incident
    was created via API (IncidentViewSet.perform_create) or Django admin.

    Uses ``transaction.on_commit`` because ``broadcast_incident()`` pushes
    to the channel layer inline (it no longer wraps with its own on_commit).
    """
    if created:
        if getattr(instance, "_skip_broadcast_notification", False):
            return

        from django.db import transaction

        incident_id = instance.pk

        def _broadcast():
            from .notification_service import NotificationService
            from .services.outbox_service import OutboxService
            try:
                inc = Incident.objects.select_related("tenant", "camera").get(pk=incident_id)
                # 1. Immediate WS Ping (Non-blocking browser feedback)
                NotificationService.broadcast_incident(inc)
                
                # 2. Enqueue Background Backfill (Email, Alerts, Unread Counts)
                OutboxService.emit(
                    aggregate_type="incident",
                    aggregate_id=str(incident_id),
                    event_type="incident.created",
                    payload={"incident_id": str(incident_id)}
                )
            except Incident.DoesNotExist:
                import logging
                logging.getLogger(__name__).warning(
                    "Incident %s disappeared before notification dispatch", incident_id
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to broadcast notification for incident %s: %s", incident_id, exc
                )

        transaction.on_commit(_broadcast)


@receiver(post_save, sender=Camera)
def sync_camera_to_mediamtx(sender, instance, **kwargs):
    """
    On camera save, trigger route projection regeneration.

    NOTE: MediaMTX relay provisioning is no longer done here.
    The camera_config_service already persists MediaMTXDesiredPath
    in the same transaction. The relay reconciler worker handles
    the actual MediaMTX API calls.
    """
    trigger_route_projection_generation()

@receiver(post_delete, sender=Camera)
def delete_camera_from_mediamtx(sender, instance, **kwargs):
    """
    On camera delete, trigger route projection regeneration.

    NOTE: MediaMTX path removal is no longer done here.
    The camera_config_service marks the desired path as disabled
    before deleting. The relay reconciler worker handles the
    actual MediaMTX path removal.
    """
    trigger_route_projection_generation()
