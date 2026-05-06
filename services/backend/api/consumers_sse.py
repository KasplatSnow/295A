"""
SSE Consumer for realtime browser notifications.
"""
import asyncio
import logging
from typing import Optional

from urllib.parse import parse_qs

from channels.generic.http import AsyncHttpConsumer
from django.utils import timezone

from .realtime_notifications import (
    authenticate_user_from_bearer,
    parse_tenant_id,
    verify_tenant_membership,
    build_group_name,
    filter_notification_for_user,
    encode_sse
)

logger = logging.getLogger(__name__)

class NotificationSSEConsumer(AsyncHttpConsumer):
    """
    SSE consumer for real-time tenant notifications.
    """
    
    async def handle(self, body):
        """
        Handle the incoming HTTP request to open the SSE stream.
        """
        self.user = None
        self.tenant_id: Optional[int] = None
        self.group_name: Optional[str] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self._disconnect_event = asyncio.Event()
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_complete = False

        # Handle CORS preflight (OPTIONS request)
        method = self.scope.get("method", "").upper()
        if method == "OPTIONS":
            await self.send_response(200, b"", headers=[
                (b"Access-Control-Allow-Origin", b"*"),
                (b"Access-Control-Allow-Methods", b"GET, OPTIONS"),
                (b"Access-Control-Allow-Headers", b"Authorization, X-Tenant-ID"),
                (b"Access-Control-Max-Age", b"86400"),
            ])
            return

        # 1. Extract credentials from Query Params or Headers
        # (EventSource does not support custom headers, so query params are the primary for SSE)
        headers = dict(self.scope.get("headers", []))
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        query_params = parse_qs(query_string)

        # Token extraction
        token = query_params.get("token", [None])[0]
        if not token:
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        self.user = await authenticate_user_from_bearer(token)
        if not self.user:
            logger.warning(f"SSE connection rejected: authentication failed (token_present={bool(token)})")
            await self.send_response(401, b"Unauthorized", headers=[
                (b"Content-Type", b"text/plain"),
                (b"Access-Control-Allow-Origin", b"*"),
            ])
            return

        # 2. Extract tenant identification
        tenant_val = query_params.get("tenant_id", [None])[0]
        if not tenant_val:
            tenant_val = headers.get(b"x-tenant-id", b"").decode("utf-8")
        
        self.tenant_id = parse_tenant_id(tenant_val)
        
        if not self.tenant_id:
            logger.warning("SSE connection rejected: missing or invalid X-Tenant-ID")
            await self.send_response(400, b"Bad Request: Missing X-Tenant-ID", headers=[
                (b"Content-Type", b"text/plain"),
                (b"Access-Control-Allow-Origin", b"*"),
            ])
            return

        # 3. Verify membership
        if not await verify_tenant_membership(self.user, self.tenant_id):
            logger.warning(f"SSE connection rejected: user {self.user.id} not member of tenant {self.tenant_id}")
            await self.send_response(403, b"Forbidden", headers=[
                (b"Content-Type", b"text/plain"),
                (b"Access-Control-Allow-Origin", b"*"),
            ])
            return

        # 4. Accept SSE connection
        await self.send_headers(headers=[
            (b"Cache-Control", b"no-cache"),
            (b"Content-Type", b"text/event-stream"),
            (b"Connection", b"keep-alive"),
            (b"Access-Control-Allow-Origin", b"*"),
        ])

        # 5. Join group
        self.group_name = build_group_name(self.tenant_id)
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        logger.info(f"User {self.user.username} connected to tenant {self.tenant_id} SSE notifications")

        try:
            # 6. Send initial connected event
            welcome_data = {
                "type": "connection_established",
                "message": f"Connected to tenant {self.tenant_id} notifications via SSE",
                "tenant_id": self.tenant_id
            }
            await self.send_body(encode_sse("connected", welcome_data), more_body=True)

            # 7. Start heartbeat task to keep proxy connections alive
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Keep the stream alive until ASGI signals a client disconnect.
            await self._disconnect_event.wait()
        except asyncio.CancelledError:
            logger.debug(f"SSE connection for user {self.user.username} cancelled by server.")
            raise
        finally:
            await self._cleanup()

    async def disconnect(self):
        """Handle a client-initiated disconnect without waiting for Daphne to kill the task."""
        self._disconnect_event.set()
        await self._cleanup()

    async def _cleanup(self):
        async with self._cleanup_lock:
            if self._cleanup_complete:
                return
            self._cleanup_complete = True

            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                try:
                    await self.heartbeat_task
                except asyncio.CancelledError:
                    pass
                self.heartbeat_task = None

            if self.group_name:
                try:
                    await self.channel_layer.group_discard(
                        self.group_name,
                        self.channel_name
                    )
                except Exception as e:
                    logger.error(f"Error discarding group during SSE cleanup: {e}")
                finally:
                    self.group_name = None

            logger.info(
                f"User {self.user.username if getattr(self, 'user', None) else 'unknown'} "
                f"disconnected from tenant {self.tenant_id} SSE notifications"
            )

    async def _heartbeat_loop(self):
        """Send a lightweight event periodically to prevent proxy timeouts."""
        try:
            while True:
                await asyncio.sleep(20)
                await self.send_body(encode_sse("ping", {"time": timezone.now().isoformat()}), more_body=True)
        except asyncio.CancelledError:
            # Expected on connection close
            pass
        except Exception as e:
            logger.error(f"Heartbeat loop error: {e}")

    # ── Channel message handlers ───────────────────────────────

    async def notification_message(self, event):
        """
        Handler for 'notification_message' group messages.
        Broadcasts notification to the SSE stream.
        """
        if not self.user:
            return

        data = filter_notification_for_user(event.get("data", {}), self.user.id)
        if not data:
            return
            
        try:
            event_id = str(data.get("alert_id", "")) if data.get("alert_id") else None
            payload_bytes = encode_sse("notification", data, event_id=event_id)
            await self.send_body(payload_bytes, more_body=True)
        except Exception as e:
            logger.error(f"Failed to push SSE message: {e}")

    async def broadcast_message(self, event):
        """
        Handler for general broadcasts (e.g., system announcements).
        """
        try:
            data = event.get("data", {})
            payload_bytes = encode_sse("broadcast", data)
            await self.send_body(payload_bytes, more_body=True)
        except Exception as e:
            logger.error(f"Failed to push SSE broadcast: {e}")
