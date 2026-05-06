"""
Unit tests for the notification service.

Tests cover:
- NotificationService.broadcast_incident()
- NotificationService.broadcast_message()
- REST API endpoints for notifications
- WebSocket consumer authentication and group joining
"""

import json
import os
import asyncio
from types import SimpleNamespace
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock, AsyncMock

from api.models import (
    Tenant, Membership, Camera, Incident, Alert, 
)
from api.consumers_sse import NotificationSSEConsumer
from api.notification_service import NotificationService, dispatch_notifications
from ai_integration.incident_ingest import process_alert_event


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'rest_framework_simplejwt.authentication.JWTAuthentication',
        ],
        'DEFAULT_PERMISSION_CLASSES': [
            'rest_framework.permissions.IsAuthenticated',
        ],
    }
)
class NotificationServiceTests(TestCase):
    """Test NotificationService broadcast functions."""
    
    def setUp(self):
        # Create test tenant and user
        self.tenant = Tenant.objects.create(name='Test Tenant')
        self.user = User.objects.create_user(
            username='testuser', 
            email='test@test.com', 
            password='password123'
        )
        Membership.objects.create(
            user=self.user, 
            tenant=self.tenant, 
            role='owner'
        )
        
        # Create camera
        self.camera = Camera.objects.create(
            tenant=self.tenant,
            name='Test Camera',
            status='active'
        )
    
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_incident_creates_alerts(self, mock_get_layer):
        """Test that broadcast_incident creates Alert records for all members."""
        # Mock channel layer
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        
        # Create an incident
        incident = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='intrusion',
            status='open',
            severity=4,
            started_at=timezone.now()
        )
        
        # Call broadcast
        result = NotificationService.broadcast_incident(incident)
        
        # Verify alerts were created for all members
        self.assertGreaterEqual(result['alerts_created'], 1)
        
        # Verify alert content
        alert = Alert.objects.filter(incident=incident).first()
        self.assertIsNotNone(alert)
        self.assertIsNotNone(alert.payload)
        self.assertIn('title', alert.payload)
    
    @patch('sys.platform', 'linux')
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_incident_calls_channel(self, mock_get_layer):
        """Test that broadcast_incident sends to channel layer."""
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        
        incident = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='fire',
            status='open',
            severity=5,
            started_at=timezone.now()
        )
        
        result = NotificationService.broadcast_incident(incident)
        
        # Verify channel broadcast was called at least once
        self.assertGreaterEqual(mock_layer.group_send.call_count, 1)
    
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_message(self, mock_get_layer):
        """Test broadcasting a custom message."""
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        
        result = NotificationService.broadcast_message(
            tenant_id=self.tenant.id,
            title="Test Broadcast",
            message="This is a test message",
            notification_type="broadcast"
        )
        
        # Verify message was sent
        self.assertIsNotNone(result)
    
    @patch('sys.platform', 'linux')
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_handles_channel_unavailable(self, mock_get_layer):
        """Test graceful handling when channel layer is unavailable."""
        mock_get_layer.return_value = None  # Simulate unavailable
        
        incident = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='intrusion',
            status='open',
            severity=3,
            started_at=timezone.now()
        )
        
        result = NotificationService.broadcast_incident(incident)
        
        # Should still create alerts even if channel fails
        self.assertEqual(result['websocket'], 'channel_layer_unavailable')
        self.assertGreaterEqual(result['alerts_created'], 1)
    
    def test_dispatch_notifications_calls_broadcast(self):
        """Test that dispatch_notifications calls broadcast_incident."""
        with patch.object(NotificationService, 'broadcast_incident') as mock_broadcast:
            mock_broadcast.return_value = {'websocket': 'sent', 'alerts_created': 1}
            
            incident = Incident.objects.create(
                tenant=self.tenant,
                camera=self.camera,
                type='intrusion',
                status='open',
                severity=3,
                started_at=timezone.now()
            )
            
            dispatch_notifications(incident)
            
            # broadcast_incident should have been called
            self.assertGreaterEqual(mock_broadcast.call_count, 1)


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'rest_framework_simplejwt.authentication.JWTAuthentication',
        ],
        'DEFAULT_PERMISSION_CLASSES': [
            'rest_framework.permissions.IsAuthenticated',
        ],
    }
)
class NotificationAPITests(APITestCase):
    """Test notification REST API endpoints."""
    
    def setUp(self):
        self.client = APIClient()
        
        # Create users
        self.owner = User.objects.create_user(
            username='owner', 
            email='owner@test.com', 
            password='password123'
        )
        self.member = User.objects.create_user(
            username='member', 
            email='member@test.com', 
            password='password123'
        )
        
        # Create tenant
        self.tenant = Tenant.objects.create(name='Test Tenant')
        
        # Create memberships
        Membership.objects.create(
            user=self.owner, 
            tenant=self.tenant, 
            role='owner'
        )
        Membership.objects.create(
            user=self.member, 
            tenant=self.tenant, 
            role='member'
        )
        
        # Create camera and incident for testing
        self.camera = Camera.objects.create(
            tenant=self.tenant,
            name='Test Camera',
            status='active'
        )
        
        # Create incident with alert
        self.incident = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='intrusion',
            status='open',
            severity=4,
            started_at=timezone.now()
        )
        
        # Create alert
        self.alert = Alert.objects.create(
            incident=self.incident,
            channel='websocket',
            payload={
                'title': 'Test Alert',
                'message': 'This is a test',
                'data': {}
            }
        )
        
        # Get owner token
        response = self.client.post(
            '/api/auth/token/', 
            {'username': 'owner', 'password': 'password123'}, 
            format='json'
        )
        self.token = response.data['access']
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
    
    @patch('api.notification_service.get_channel_layer')
    def test_list_notifications(self, mock_get_layer):
        """Test GET /api/notifications/ returns notifications."""
        mock_get_layer.return_value = MagicMock()
        
        response = self.client.get('/api/notifications/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('notifications', response.data)
        # Should have at least the alert we created
        self.assertGreaterEqual(len(response.data['notifications']), 1)

    @patch('api.notification_service.get_channel_layer')
    def test_list_notifications_falls_back_when_payload_message_missing(self, mock_get_layer):
        """List endpoint should always provide non-empty title/message fallbacks."""
        mock_get_layer.return_value = MagicMock()

        sparse_alert = Alert.objects.create(
            incident=self.incident,
            channel='websocket',
            payload={'data': {}},
        )

        response = self.client.get('/api/notifications/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        matched = next((item for item in response.data['notifications'] if item['id'] == sparse_alert.id), None)
        self.assertIsNotNone(matched)
        self.assertTrue(matched['title'])
        self.assertTrue(matched['message'])
    
    def test_list_notifications_requires_auth(self):
        """Test that listing notifications requires authentication."""
        self.client.credentials()  # Remove auth
        response = self.client.get('/api/notifications/')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_list_notifications_requires_tenant(self):
        """Test that listing notifications requires X-Tenant-ID."""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        response = self.client.get('/api/notifications/')
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    @patch('api.notification_service.get_channel_layer')
    def test_mark_read_single(self, mock_get_layer):
        """Test marking a single notification as read."""
        mock_get_layer.return_value = MagicMock()
        
        response = self.client.post(
            '/api/notifications/mark-read/',
            {'notification_ids': [self.alert.id]},
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify alert was marked
        self.alert.refresh_from_db()
        self.assertIsNotNone(self.alert.delivered_at)
    
    @patch('api.notification_service.get_channel_layer')
    def test_mark_read_all(self, mock_get_layer):
        """Test marking all notifications as read."""
        mock_get_layer.return_value = MagicMock()
        
        response = self.client.post(
            '/api/notifications/mark-read/',
            {'mark_all': True},
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['marked_read'], 1)
    
    @patch('api.notification_service.get_channel_layer')
    def test_unread_count(self, mock_get_layer):
        """Test getting unread notification count."""
        mock_get_layer.return_value = MagicMock()
        
        response = self.client.get('/api/notifications/unread-count/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['unread_count'], 1)

    def test_unread_count_includes_legacy_payload_scoped_alerts(self):
        """Unread count should include legacy alerts with user_id stored in payload."""
        Alert.objects.filter(pk=self.alert.pk).update(user=None, payload={
            'title': 'Legacy Alert',
            'message': 'Legacy payload-scoped notification',
            'user_id': str(self.owner.id),
            'data': {},
        })

        response = self.client.get('/api/notifications/unread-count/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['unread_count'], 1)

    def test_unread_count_does_not_backfill_alerts(self):
        """Unread count should be read-only and must not create alert rows."""
        incident_without_alert = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='fire',
            status='open',
            severity=4,
            started_at=timezone.now(),
        )

        Alert.objects.filter(incident=incident_without_alert).delete()
        before_count = Alert.objects.count()

        response = self.client.get('/api/notifications/unread-count/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Alert.objects.count(), before_count)
        self.assertFalse(Alert.objects.filter(incident=incident_without_alert).exists())

    def test_transport_status_endpoint(self):
        """Test GET /api/notifications/transport-status/ returns health payload."""
        subscriber_payload = {
            'phase': 'waiting',
            'stream': 'vigilzone.ai.incidents',
            'consumer_group': 'vigilzone.ai.incidents.group',
            'last_event_id': 'evt-123',
            'last_stream_entry_id': '1744000000000-0',
        }

        with patch.dict(os.environ, {
            'REDIS_URL': 'redis://default:redispw@localhost:32768/0',
            'AI_INCIDENT_CHANNEL': 'vigilzone.ai.incidents',
        }, clear=False):
            with patch('api.views.create_redis_client') as mock_create_client:
                mock_client = MagicMock()
                mock_client.get.return_value = json.dumps(subscriber_payload)
                mock_create_client.return_value = mock_client

                response = self.client.get('/api/notifications/transport-status/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('uses_redis', response.data)
        self.assertIn('redis_reachable', response.data)
        self.assertIn('subscriber_healthy', response.data)
        self.assertIn('channel_backend', response.data)
        self.assertEqual(response.data['incident_channel'], 'vigilzone.ai.incidents')
        self.assertEqual(response.data['redis']['connection_display'], 'redis://default:***@localhost:32768/0')
        self.assertEqual(response.data['subscriber']['last_event_id'], 'evt-123')
        self.assertTrue(response.data['subscriber_healthy'])
        self.assertTrue(response.data['realtime_ready'])

    def test_transport_status_requires_subscriber_heartbeat(self):
        """Test transport readiness stays false without a fresh subscriber heartbeat."""
        with patch.dict(os.environ, {
            'REDIS_URL': 'redis://default:redispw@localhost:32768/0',
            'AI_INCIDENT_CHANNEL': 'vigilzone.ai.incidents',
        }, clear=False):
            with patch('api.views.create_redis_client') as mock_create_client:
                mock_client = MagicMock()
                mock_client.get.return_value = None
                mock_create_client.return_value = mock_client

                response = self.client.get('/api/notifications/transport-status/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['redis_reachable'])
        self.assertFalse(response.data['subscriber_healthy'])
        self.assertIsNone(response.data['subscriber'])
        self.assertFalse(response.data['realtime_ready'])

    def test_test_incident_endpoint_enqueues_synthetic_incident(self):
        """Test POST /api/notifications/test-incident/ appends a synthetic stream event."""
        with patch.dict(os.environ, {
            'REDIS_URL': 'redis://default:redispw@localhost:32768/0',
            'AI_INCIDENT_CHANNEL': 'vigilzone.ai.incidents',
        }, clear=False):
            with patch('api.views.create_redis_client') as mock_create_client:
                mock_client = MagicMock()
                mock_client.xadd.return_value = '1744000000000-0'
                mock_client.xlen.return_value = 3
                mock_client.get.return_value = None
                mock_create_client.return_value = mock_client

                response = self.client.post(
                    '/api/notifications/test-incident/',
                    {'camera_id': self.camera.id, 'type': 'fire', 'severity': 5},
                    format='json',
                )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['incident_channel'], 'vigilzone.ai.incidents')
        self.assertEqual(response.data['stream_entry_id'], '1744000000000-0')
        self.assertEqual(response.data['stream_length'], 3)
        self.assertTrue(mock_client.xadd.called)
        queued_payload = json.loads(mock_client.xadd.call_args[0][1]['payload'])
        self.assertEqual(queued_payload['event'], 'alert.created')
        self.assertEqual(queued_payload['data']['type'], 'fire')
        self.assertEqual(queued_payload['data']['camera_id'], self.camera.ai_camera_id or self.camera.stream_path or self.camera.name)
    
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_requires_title_and_message(self, mock_get_layer):
        """Test that broadcast requires title and message."""
        mock_get_layer.return_value = MagicMock()
        
        response = self.client.post(
            '/api/notifications/broadcast/',
            {'title': 'Only Title'},
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    @patch('api.notification_service.get_channel_layer')
    @patch('api.views.NotificationService.broadcast_message')
    def test_test_websocket_notification(self, mock_broadcast, mock_get_layer):
        """Test sending a test WebSocket notification."""
        mock_get_layer.return_value = MagicMock()
        mock_broadcast.return_value = "sent"
        
        response = self.client.post(
            '/api/notifications/test-websocket/',
            {},
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('sent to all connected clients', response.data['message'])


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'rest_framework_simplejwt.authentication.JWTAuthentication',
        ],
        'DEFAULT_PERMISSION_CLASSES': [
            'rest_framework.permissions.IsAuthenticated',
        ],
    }
)
class IncidentCreationNotificationTests(APITestCase):
    """Test that creating incidents triggers notifications."""
    
    def setUp(self):
        self.client = APIClient()
        
        self.owner = User.objects.create_user(
            username='owner', 
            email='owner@test.com', 
            password='password123'
        )
        
        self.tenant = Tenant.objects.create(name='Test Tenant')
        Membership.objects.create(
            user=self.owner, 
            tenant=self.tenant, 
            role='owner'
        )
        
        self.camera = Camera.objects.create(
            tenant=self.tenant,
            name='Test Camera',
            status='active'
        )
        
        response = self.client.post(
            '/api/auth/token/', 
            {'username': 'owner', 'password': 'password123'}, 
            format='json'
        )
        self.token = response.data['access']
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
    
    def test_incident_notification_integration(self):
        """Test that creating an incident through the model triggers notifications.
        
        Note: This tests the dispatch_notifications function directly since
        the serializer validation is complex in tests.
        """
        with patch('api.notification_service.get_channel_layer') as mock_layer:
            mock_layer.return_value = MagicMock()
            
            # Create incident
            incident = Incident.objects.create(
                tenant=self.tenant,
                camera=self.camera,
                type='intrusion',
                status='open',
                severity=4,
                started_at=timezone.now()
            )
            
            # Call dispatch directly
            dispatch_notifications(incident)
            
            # Verify alert was created
            alerts = Alert.objects.filter(incident=incident)
            self.assertGreaterEqual(alerts.count(), 1)


class NotificationServiceChannelTests(TestCase):
    """Test NotificationService channel layer interactions."""
    
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Channel Test Tenant')
    
    @patch('sys.platform', 'linux')
    @patch('api.notification_service.get_channel_layer')
    def test_broadcast_to_channel_builds_correct_group_name(self, mock_get_layer):
        """Test that _broadcast_to_channel uses correct group name format."""
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        
        # Call with tenant_id
        NotificationService._broadcast_to_channel(
            tenant_id=42,
            notification_type='test',
            data={'message': 'test'}
        )
        
        # Verify group_send was called
        self.assertGreaterEqual(mock_layer.group_send.call_count, 1)
        
        # Verify the group name format
        call_args = mock_layer.group_send.call_args_list[0]
        group_name = call_args[0][0]
        self.assertEqual(group_name, 'tenant_notifications_42')
    
    def test_build_incident_notification_format(self):
        """Test that _build_incident_notification returns correct format."""
        tenant = Tenant.objects.create(name='Test')
        camera = Camera.objects.create(
            tenant=tenant,
            name='Test Cam',
            status='active'
        )
        incident = Incident.objects.create(
            tenant=tenant,
            camera=camera,
            type='intrusion',
            status='open',
            severity=4,
            started_at=timezone.now()
        )
        
        notification = NotificationService._build_incident_notification(incident)
        
        # Verify structure
        self.assertEqual(notification['type'], 'notification')
        self.assertEqual(notification['notification_type'], 'incident')
        self.assertIn('title', notification)
        self.assertIn('message', notification)
        self.assertIn('data', notification)
        self.assertIn('created_at', notification)
        
        # Verify data contents
        self.assertEqual(notification['data']['incident_id'], str(incident.id))
        self.assertEqual(notification['data']['severity'], 4)
        self.assertEqual(notification['data']['camera_name'], 'Test Cam')


class SyncedAiIncidentNotificationTests(TestCase):
    """Regression tests for AI-synced camera live notifications."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Synced AI Tenant')
        self.user = User.objects.create_user(
            username='synced-user',
            email='synced@test.com',
            password='password123',
        )
        Membership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role='owner',
        )
        self.camera = Camera.objects.create(
            tenant=self.tenant,
            name='Warehouse Entrance',
            ai_camera_id='cam_synced_01',
            stream_path='cam_synced_01',
            source_type=Camera.SourceType.REGISTERED,
            status=Camera.Status.ACTIVE,
        )

    @patch('api.notification_service.get_channel_layer')
    @patch('api.notification_service.NotificationService.broadcast_incident')
    def test_registered_camera_create_event_dispatches_live_notification(self, mock_broadcast, mock_get_layer):
        """Synced-AI camera incidents should notify without requiring a page refresh."""
        mock_get_layer.return_value = MagicMock()
        mock_broadcast.return_value = {'websocket': 'sent', 'alerts_created': 1}

        payload = {
            'id': 'evt-synced-create-1',
            'camera_id': 'cam_synced_01',
            'type': 'fire',
            'severity': 'high',
            'timestamp': timezone.now().isoformat(),
        }

        with self.captureOnCommitCallbacks(execute=True):
            result = process_alert_event(payload, source='redis', event_id='evt-synced-create-1')

        self.assertEqual(result.status, 'created')
        self.assertEqual(Incident.objects.filter(camera=self.camera).count(), 1)
        self.assertEqual(mock_broadcast.call_count, 1)
        self.assertEqual(mock_broadcast.call_args[0][0].camera_id, self.camera.id)

    @patch('api.notification_service.get_channel_layer')
    @patch('api.notification_service.NotificationService.broadcast_incident')
    def test_registered_camera_update_event_dispatches_live_notification(self, mock_broadcast, mock_get_layer):
        """Repeated synced-AI alerts that update an open incident should still notify live."""
        mock_get_layer.return_value = MagicMock()
        mock_broadcast.return_value = {'websocket': 'sent', 'alerts_created': 1}

        existing = Incident.objects.create(
            tenant=self.tenant,
            camera=self.camera,
            type='fire',
            status='open',
            severity=3,
            started_at=timezone.now(),
        )
        mock_broadcast.reset_mock()

        payload = {
            'id': 'evt-synced-update-1',
            'camera_id': 'cam_synced_01',
            'type': 'fire',
            'severity': 'critical',
            'timestamp': timezone.now().isoformat(),
        }

        with self.captureOnCommitCallbacks(execute=True):
            result = process_alert_event(payload, source='redis', event_id='evt-synced-update-1')

        self.assertEqual(result.status, 'updated')
        existing.refresh_from_db()
        self.assertEqual(existing.severity, 5)
        self.assertEqual(mock_broadcast.call_count, 1)
        self.assertEqual(mock_broadcast.call_args[0][0].pk, existing.pk)

    @patch('api.notification_service.get_channel_layer')
    @patch('api.notification_service.NotificationService.broadcast_incident')
    def test_live_webcam_event_uses_tenant_hint_and_dispatches_notification(self, mock_broadcast, mock_get_layer):
        """cam_live alerts should resolve to the tenant's webcam camera and notify immediately."""
        mock_get_layer.return_value = MagicMock()
        mock_broadcast.return_value = {'websocket': 'sent', 'alerts_created': 1}

        webcam_camera = Camera.objects.create(
            tenant=self.tenant,
            name='Live Webcam',
            ai_camera_id='cam_live',
            stream_path='cam_live',
            source_type=Camera.SourceType.WEBCAM,
            status=Camera.Status.ACTIVE,
        )

        payload = {
            'id': 'evt-live-webcam-1',
            'camera_id': 'cam_live',
            'tenant_id': self.tenant.id,
            'source_type': 'live_camera',
            'type': 'intrusion',
            'severity': 'high',
            'timestamp': timezone.now().isoformat(),
        }

        with self.captureOnCommitCallbacks(execute=True):
            result = process_alert_event(payload, source='redis', event_id='evt-live-webcam-1')

        self.assertEqual(result.status, 'created')
        incident = Incident.objects.get(pk=result.incident_id)
        self.assertEqual(incident.tenant_id, self.tenant.id)
        self.assertEqual(incident.camera_id, webcam_camera.id)
        self.assertEqual(mock_broadcast.call_count, 1)
        self.assertEqual(mock_broadcast.call_args[0][0].pk, incident.pk)


class NotificationSSEConsumerTests(IsolatedAsyncioTestCase):
    """Behavioral tests for SSE send failure and shutdown cleanup."""

    async def asyncSetUp(self):
        self.consumer = NotificationSSEConsumer()
        self.consumer.user = SimpleNamespace(username="sse-user")
        self.consumer.tenant_id = 1
        self.consumer.group_name = "tenant_notifications_1"
        self.consumer.channel_name = "test-channel"
        self.consumer.channel_layer = SimpleNamespace(group_discard=AsyncMock())
        self.consumer.send_body = AsyncMock()
        self.consumer._shutdown_event = asyncio.Event()
        self.consumer._send_lock = asyncio.Lock()
        self.consumer._cleanup_lock = asyncio.Lock()
        self.consumer._cleanup_complete = False
        self.consumer.heartbeat_task = None

    async def test_send_event_sets_shutdown_event_when_socket_send_fails(self):
        """A broken socket should mark the stream for shutdown immediately."""
        self.consumer.send_body.side_effect = RuntimeError("socket closed")

        with self.assertRaises(RuntimeError):
            await self.consumer._send_event("ping", {"ok": True})

        self.assertTrue(self.consumer._shutdown_event.is_set())

    async def test_cleanup_is_idempotent_and_discards_group_once(self):
        """Cleanup should cancel heartbeat work and only discard the group once."""
        blocker = asyncio.Event()

        async def wait_forever():
            await blocker.wait()

        self.consumer.heartbeat_task = asyncio.create_task(wait_forever())

        await self.consumer._cleanup()
        await self.consumer._cleanup()

        self.consumer.channel_layer.group_discard.assert_awaited_once_with(
            "tenant_notifications_1",
            "test-channel",
        )
        self.assertIsNone(self.consumer.heartbeat_task)
