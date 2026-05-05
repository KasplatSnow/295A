/**
 * SSE notification hook for real-time alerts.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { toast } from './use-toast';
import { playChime, unlockAudio } from '@/lib/audio';

export interface Notification {
  id: string;
  type: 'notification' | 'connection_established' | 'subscribed';
  notification_type?: 'incident' | 'broadcast' | 'test' | 'direct';
  title: string;
  message: string;
  data?: {
    incident_id?: string | number;
    severity?: number;
    severity_level?: string;
    camera_name?: string;
    [key: string]: unknown;
  };
  created_at: string;
  incident_id?: string | number;
  severity?: number;
  severity_level?: string;
  camera_name?: string;
  alert_id?: string;
  is_read?: boolean;
}

export interface UseNotificationsReturn {
  notifications: Notification[];
  unreadCount: number;
  isConnected: boolean;
  isSubscribed: boolean;
  redisReachable: boolean;
  error: string | null;
  connect: (tenantId: number) => void;
  disconnect: () => void;
  markAsRead: (notificationIds: string[]) => Promise<void>;
  markAllAsRead: () => Promise<void>;
  testWebSocket: (tenantId: number) => Promise<void>; // Kept name for backwards compatibility
  clearNotifications: () => void;
}

const MAX_HEALTH_FAILURES = 3;
const BASE_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 15000;

function normalizeId(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  const normalized = String(value).trim();
  return normalized.length > 0 ? normalized : undefined;
}

function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('accessToken') || sessionStorage.getItem('accessToken');
}

function resolveSseUrl(): string {
  const configured = import.meta.env.VITE_SSE_URL as string | undefined;
  if (configured && configured.trim().length > 0) {
    return configured;
  }
  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    const backendPort = import.meta.env.VITE_BACKEND_PORT || '8000';
    const isDev = import.meta.env.DEV;
    if (isDev) {
      return `${protocol}//${window.location.hostname}:${backendPort}/api/notifications/stream/`;
    }
    return `${protocol}//${window.location.host}/api/notifications/stream/`;
  }
  return 'http://localhost:8000/api/notifications/stream/';
}

interface UpsertResult {
  items: Notification[];
  inserted: boolean;
}

export function upsertNotification(prev: Notification[], incoming: Notification): UpsertResult {
  const matchIndex = prev.findIndex((item) => {
    const incomingAlertId = normalizeId(incoming.alert_id);
    const itemAlertId = normalizeId(item.alert_id);

    if (incomingAlertId && itemAlertId) {
      return itemAlertId === incomingAlertId;
    }

    const incomingIncidentId = normalizeId(incoming.incident_id);
    const itemIncidentId = normalizeId(item.incident_id);
    if (incomingIncidentId && itemIncidentId && incoming.created_at && item.created_at) {
      return itemIncidentId === incomingIncidentId && item.created_at === incoming.created_at;
    }

    return item.id === incoming.id;
  });

  if (matchIndex === -1) {
    const updated = [incoming, ...prev].slice(0, 100);
    return { items: updated, inserted: true };
  }

  const copy = [...prev];
  copy[matchIndex] = {
    ...copy[matchIndex],
    ...incoming,
    is_read: incoming.is_read ?? copy[matchIndex].is_read,
  };
  return { items: copy, inserted: false };
}

export function mergeHydratedNotifications(prev: Notification[], incoming: Notification[]): Notification[] {
  const reversedIncoming = [...incoming].reverse();
  return reversedIncoming.reduce(
    (current, item) => upsertNotification(current, item).items,
    prev,
  );
}

export function mergeHydratedUnreadCount(prevUnreadCount: number, hydratedUnreadCount: number): number {
  return Math.max(prevUnreadCount, hydratedUnreadCount);
}

export function useNotifications(): UseNotificationsReturn {
  const queryClient = useQueryClient();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isConnected, setIsConnectedState] = useState(false);
  const isConnectedRef = useRef(false);
  const setIsConnected = useCallback((val: boolean) => {
    isConnectedRef.current = val;
    setIsConnectedState(val);
  }, []);

  const [isSubscribed, setIsSubscribed] = useState(false);
  const [redisReachable, setRedisReachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const tokenRef = useRef<string | null>(null);
  const tenantIdRef = useRef<number | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const sessionGenerationRef = useRef(0);
  const activeSessionRef = useRef<{ token: string | null; tenantId: number | null }>({
    token: null,
    tenantId: null,
  });
  const healthIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const healthFailureCountRef = useRef(0);
  const processedAlertIds = useRef<Set<string>>(new Set());
  const audioUnlockedRef = useRef(false);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const stopHealthPolling = useCallback(() => {
    if (healthIntervalRef.current) {
      clearInterval(healthIntervalRef.current);
      healthIntervalRef.current = null;
    }
  }, []);

  const fetchTransportStatus = useCallback(async (token: string, tenantId: number) => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }

    try {
      const response = await fetch('/api/notifications/transport-status/', {
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Tenant-ID': String(tenantId),
        },
      });
      if (!response.ok) {
        setRedisReachable(false);
        healthFailureCountRef.current += 1;
        if (healthFailureCountRef.current >= MAX_HEALTH_FAILURES) {
          stopHealthPolling();
        }
        return;
      }
      const payload = await response.json();
      setRedisReachable(Boolean(payload?.redis_reachable));
      healthFailureCountRef.current = 0;
    } catch {
      setRedisReachable(false);
      healthFailureCountRef.current += 1;
      if (healthFailureCountRef.current >= MAX_HEALTH_FAILURES) {
        stopHealthPolling();
      }
    }
  }, [stopHealthPolling]);

  const startHealthPolling = useCallback((token: string, tenantId: number) => {
    stopHealthPolling();
    healthFailureCountRef.current = 0;

    fetchTransportStatus(token, tenantId);
    healthIntervalRef.current = setInterval(() => {
      if (tokenRef.current && tenantIdRef.current) {
        fetchTransportStatus(tokenRef.current, tenantIdRef.current);
      }
    }, 15000);
  }, [fetchTransportStatus, stopHealthPolling]);

  const hydrateNotifications = useCallback(async (token: string, tenantId: number) => {
    try {
      const headers = {
        Authorization: `Bearer ${token}`,
        'X-Tenant-ID': String(tenantId),
      };

      const [listResp, unreadResp] = await Promise.all([
        fetch('/api/notifications/?limit=50', { headers }),
        fetch('/api/notifications/unread-count/', { headers }),
      ]);

      if (listResp.ok) {
        const listJson = await listResp.json();
        const items = Array.isArray(listJson?.notifications) ? listJson.notifications : [];
        
        items.forEach((item: any) => {
          const alertId = normalizeId(item.alert_id ?? item.id);
          if (alertId) processedAlertIds.current.add(alertId);
        });

        const mapped: Notification[] = items.map((item: Record<string, unknown>) => {
          const data = (item.data as Record<string, unknown>) || {};
          const severity = (item.severity as number | undefined) ?? (data.severity as number | undefined);
          const severityLevel = (item.severity_level as string | undefined) ?? (data.severity_level as string | undefined);
          const cameraName = (item.camera_name as string | undefined) ?? (data.camera_name as string | undefined);
          const incidentId = (item.incident_id as string | number | undefined) ?? (data.incident_id as string | number | undefined);
          const alertId = normalizeId(item.alert_id ?? item.id);
          
          return {
            id: alertId ? `alert-${alertId}` : `alert-${String(item.id ?? Date.now())}`,
            type: 'notification',
            notification_type: 'incident',
            title: String(item.title ?? 'Notification'),
            message: String(item.message ?? ''),
            data,
            created_at: String(item.created_at ?? new Date().toISOString()),
            incident_id: incidentId,
            severity,
            severity_level: severityLevel,
            camera_name: cameraName,
            alert_id: alertId,
            is_read: Boolean(item.is_read),
          };
        });
        setNotifications((prev) => mergeHydratedNotifications(prev, mapped));
      }

      if (unreadResp.ok) {
        const unreadJson = await unreadResp.json();
        const nextUnreadCount = Number(unreadJson?.unread_count ?? 0);
        console.log('[Notifications] Hydrated unread count:', nextUnreadCount);
        setUnreadCount((prev) => mergeHydratedUnreadCount(prev, nextUnreadCount));
      }
    } catch (err) {
      console.error('[Notifications] Failed to hydrate:', err);
    }
  }, []);

  const disconnect = useCallback(() => {
    sessionGenerationRef.current += 1;
    clearReconnectTimer();
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    stopHealthPolling();
    reconnectAttemptRef.current = 0;
    activeSessionRef.current = { token: null, tenantId: null };
    tokenRef.current = null;
    tenantIdRef.current = null;
    setIsConnected(false);
    setIsSubscribed(false);
    setRedisReachable(false);
  }, [clearReconnectTimer, setIsConnected, stopHealthPolling]);

  const connect = useCallback((tenantId: number) => {
    const token = getStoredToken();
    if (!token) {
      setError("No authentication token found");
      return;
    }

    if (
      isConnectedRef.current &&
      tokenRef.current === token &&
      tenantIdRef.current === tenantId
    ) {
      return;
    }

    if (
      activeSessionRef.current.token === token &&
      activeSessionRef.current.tenantId === tenantId &&
      abortControllerRef.current
    ) {
      return;
    }

    sessionGenerationRef.current += 1;
    const generation = sessionGenerationRef.current;
    clearReconnectTimer();
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    tokenRef.current = token;
    tenantIdRef.current = tenantId;
    activeSessionRef.current = { token, tenantId };
    reconnectAttemptRef.current = 0;
    setError(null);
    setIsConnected(false);
    setIsSubscribed(false);

    hydrateNotifications(token, tenantId);
    startHealthPolling(token, tenantId);

    const baseUrl = resolveSseUrl();
    const sseUrl = `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}&tenant_id=${tenantId}`;
    const ctrl = new AbortController();
    abortControllerRef.current = ctrl;

    const scheduleReconnect = () => {
      if (
        generation !== sessionGenerationRef.current ||
        !activeSessionRef.current.token ||
        !activeSessionRef.current.tenantId
      ) {
        return;
      }

      reconnectAttemptRef.current += 1;
      const delay = Math.min(
        MAX_RECONNECT_DELAY_MS,
        BASE_RECONNECT_DELAY_MS * (2 ** Math.max(0, reconnectAttemptRef.current - 1)),
      );
      clearReconnectTimer();
      reconnectTimerRef.current = setTimeout(() => {
        if (generation !== sessionGenerationRef.current || tenantIdRef.current === null) {
          return;
        }
        connect(tenantIdRef.current);
      }, delay);
    };

    const connectSse = async () => {
      try {
        await fetchEventSource(sseUrl, {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
            'X-Tenant-ID': String(tenantId),
            'Accept': 'text/event-stream',
          },
          signal: ctrl.signal,
          async onopen(response) {
            if (generation !== sessionGenerationRef.current || ctrl.signal.aborted) {
              throw new DOMException('Stale notification stream', 'AbortError');
            }
            if (response.status === 401) {
              throw new Error("unauthorized");
            }
            if (response.ok && response.headers.get('content-type')?.includes('text/event-stream')) {
              reconnectAttemptRef.current = 0;
              setIsConnected(true);
              setError(null);
            } else {
              throw new Error(`Connection failed: ${response.status}`);
            }
          },
          onmessage(event) {
            if (generation !== sessionGenerationRef.current || ctrl.signal.aborted) {
              return;
            }
            try {
              if (event.event === 'ping') return;
              
              const data = JSON.parse(event.data);
              
              if (event.event === 'connected' || event.event === 'subscribed' || data.type === 'connection_established') {
                setIsSubscribed(true);
                return;
              }

              if (event.event === 'notification' || event.event === 'broadcast' || data.type === 'NEW_NOTIFICATION') {
                const payloadData = (data.data || {}) as Record<string, unknown>;
                
                const notification: Notification = {
                  id: event.id || data.alert_id ? `alert-${String(data.alert_id)}` : `sse-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
                  ...data,
                  incident_id: (data.incident_id ?? payloadData.incident_id) as string | number | undefined,
                  severity: (data.severity ?? payloadData.severity) as number | undefined,
                  severity_level: (data.severity_level ?? payloadData.severity_level) as string | undefined,
                  camera_name: (data.camera_name ?? payloadData.camera_name) as string | undefined,
                  is_read: false,
                };

                const alertId = normalizeId(data.alert_id ?? data.id);
                if (alertId && !processedAlertIds.current.has(alertId)) {
                  processedAlertIds.current.add(alertId);
                  
                  if (typeof data.unread_count === 'number') {
                    setUnreadCount(data.unread_count);
                  } else {
                    setUnreadCount((current) => current + 1);
                  }
                  
                  playChime();

                  if (queryClient && notification.incident_id) {
                    queryClient.invalidateQueries({ queryKey: ["incident", String(notification.incident_id)] });
                  }
                }

                setNotifications((prev) => upsertNotification(prev, notification).items);
              }
            } catch (err) {
              console.error('[SSE] Failed to parse message:', err);
            }
          },
          onclose() {
            if (generation !== sessionGenerationRef.current || ctrl.signal.aborted) {
              return;
            }
            console.log('[SSE] Connection closed. Scheduling reconnect.');
            setIsConnected(false);
            setIsSubscribed(false);
            scheduleReconnect();
          },
          onerror(err) {
            if (generation !== sessionGenerationRef.current || ctrl.signal.aborted) {
              return;
            }
            if (err instanceof Error && err.message === "unauthorized") {
              console.log('[SSE] Unauthorized error detected. Triggering token refresh...');
              
              // Trigger a dummy request through the 'api' instance to invoke the refresh interceptor
              import('@/lib/api').then(({ api }) => {
                if (generation !== sessionGenerationRef.current || ctrl.signal.aborted) {
                  return;
                }
                api.get('/auth/context/').then(() => {
                  console.log('[SSE] Token refreshed, reconnecting...');
                  if (generation !== sessionGenerationRef.current || tenantIdRef.current === null) {
                    return;
                  }
                  connect(tenantIdRef.current);
                }).catch(() => {
                  console.error('[SSE] Token refresh failed or session expired.');
                  setError('Session expired. Please log in again.');
                  disconnect();
                });
              });
              
              throw err; 
            }
            console.error('[SSE] Error:', err);
            setError('SSE connection error');
            setIsConnected(false);
            setIsSubscribed(false);
            scheduleReconnect();
          }
        });
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          console.log('[SSE] Connection aborted intentionally.');
          return;
        } else {
          console.error('[SSE] Failed to connect:', err);
          setError('Failed to connect to notification server');
        }

        if (generation === sessionGenerationRef.current && !ctrl.signal.aborted) {
          setIsConnected(false);
          setIsSubscribed(false);
          scheduleReconnect();
        }
      }
    };

    connectSse();

  }, [clearReconnectTimer, disconnect, hydrateNotifications, queryClient, setIsConnected, startHealthPolling]);

  const markAsRead = useCallback(async (notificationIds: string[]) => {
    const token = getStoredToken();
    if (!token) return;

    try {
      const response = await fetch('/api/notifications/mark-read/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          'X-Tenant-ID': String(tenantIdRef.current || ''),
        },
        body: JSON.stringify({ notification_ids: notificationIds }),
      });

      if (response.ok) {
        const readIds = new Set(notificationIds.map((id) => normalizeId(id)).filter(Boolean) as string[]);
        setNotifications((prev) => prev.map((n) => (
          n.alert_id && readIds.has(n.alert_id) ? { ...n, is_read: true } : n
        )));
        setUnreadCount((prev) => Math.max(0, prev - notificationIds.length));
      }
    } catch (err) {
      console.error('[Notifications] Failed to mark as read:', err);
    }
  }, []);

  const markAllAsRead = useCallback(async () => {
    const token = tokenRef.current || getStoredToken();
    if (!token) return;

    try {
      const response = await fetch('/api/notifications/mark-read/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          'X-Tenant-ID': String(tenantIdRef.current || ''),
        },
        body: JSON.stringify({ mark_all: true }),
      });

      if (response.ok) {
        setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
        setUnreadCount(0);
      }
    } catch (err) {
      console.error('[Notifications] Failed to mark all as read:', err);
    }
  }, []);

  const testWebSocket = useCallback(async (tenantId: number) => {
    const token = tokenRef.current || getStoredToken();
    if (!token) throw new Error('Missing auth token');

    const response = await fetch('/api/notifications/test-realtime/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        'X-Tenant-ID': String(tenantId),
      },
    });

    if (!response.ok) {
      throw new Error('Test notification failed');
    }
  }, []);

  const clearNotifications = useCallback(() => {
    setNotifications([]);
    setUnreadCount(0);
  }, []);

  useEffect(() => {
    const unlockFromInteraction = () => {
      if (audioUnlockedRef.current) return;
      audioUnlockedRef.current = true;
      void unlockAudio();
      window.removeEventListener('pointerdown', unlockFromInteraction);
      window.removeEventListener('keydown', unlockFromInteraction);
      window.removeEventListener('touchstart', unlockFromInteraction);
    };

    window.addEventListener('pointerdown', unlockFromInteraction, { passive: true });
    window.addEventListener('keydown', unlockFromInteraction, { passive: true });
    window.addEventListener('touchstart', unlockFromInteraction, { passive: true });

    return () => {
      window.removeEventListener('pointerdown', unlockFromInteraction);
      window.removeEventListener('keydown', unlockFromInteraction);
      window.removeEventListener('touchstart', unlockFromInteraction);
    };
  }, []);

  useEffect(() => () => disconnect(), [disconnect]);

  return {
    notifications,
    unreadCount,
    isConnected,
    isSubscribed,
    redisReachable,
    error,
    connect,
    disconnect,
    markAsRead,
    markAllAsRead,
    testWebSocket,
    clearNotifications,
  };
}
