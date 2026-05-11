# SSE Live Notification And Service Protocol Plan

## Purpose

This document gives the next implementation agent a repo-specific, low-hallucination handoff for two things:

1. the correct transport/protocol split across VigilZone services, and
2. the exact live-notification migration needed for this repo, with **SSE as the browser live-notification transport**.

This is intentionally grounded in the current codebase so the next agent does not invent missing services, rewrite working business logic, or waste tokens rediscovering the same context.

---

## Current Repo Reality (Verified)

The current browser live-notification flow is still WebSocket-first.

Verified files:

- `services/backend/api/notification_service.py`
- `services/backend/api/consumers.py`
- `services/backend/api/routing.py`
- `services/backend/server/asgi.py`
- `services/backend/server/urls.py`
- `services/backend/api/views.py`
- `services/backend/api/models.py`
- `web/ui/client/src/hooks/useNotifications.ts`
- `web/ui/package.json`
- `deploy/nginx/nginx.conf`

What is true right now:

- Backend notification persistence and fan-out live in `NotificationService`.
- Browser realtime uses Django Channels groups named `tenant_notifications_{tenant_id}`.
- The browser hook is WebSocket-based in `useNotifications.ts`.
- REST hydration is already correct and should remain:
  - `GET /api/notifications/`
  - `GET /api/notifications/unread-count/`
  - `POST /api/notifications/mark-read/`
  - `GET /api/notifications/transport-status/`
- There is no SSE route today.
- `server/asgi.py` routes all HTTP directly to Django and only special-cases WebSocket.
- Nginx has a WebSocket proxy block for `/ws/notifications/` and no SSE block.
- The frontend does **not** currently have `@microsoft/fetch-event-source` installed.

Important semantic mismatch already present:

- New `Alert` rows are still written with `channel="websocket"` in multiple places, including:
  - `services/backend/api/notification_service.py`
  - `services/backend/api/views.py` backfill path
- That value is now too transport-specific and should become `realtime` for browser live delivery.

---

## Target Protocol Map For The Whole System

Use a mixed protocol model, not one protocol for everything.

### Browser / UI

- Browser -> Backend CRUD/auth/admin: `REST/JSON`
- Browser -> Live notifications: `SSE`
- Browser -> Live video preview: `WebRTC` primary, `HLS` fallback
- Browser -> MJPEG: debug/fallback only

### Cross-Service / Internal

- Backend <-> AI / backend <-> workers async events: `JetStream`
- Reconciler -> MediaMTX control plane: `HTTP/REST`
- Cameras / relay / AI video plane: `RTSP`
- Evidence access: `S3-compatible HTTP + presigned URLs`

### External Integrations

- Email/SMS/push providers: provider-native `HTTP` or `SMTP`
- Third-party callbacks/webhooks: `HTTP`

### What Not To Introduce Here

- Do not add GraphQL for these internal paths.
- Do not add gRPC unless a later high-throughput binary workload justifies it.
- Do not route browser live notifications through JetStream directly.
- Do not turn browser live notifications into polling.

---

## Why SSE Is The Correct Browser Live-Notification Transport Here

For this repo, browser notifications are one-way server-to-client updates. SSE is the better fit because it is:

- cheaper than WebSocket for one-way streams
- simpler to operate behind proxies and cloud ingress
- easier to reconnect
- easier to secure with normal HTTP auth headers
- aligned with the existing architecture, because mark-read and hydration already happen over REST

This means:

- keep REST for hydration and mutation
- use SSE only for low-latency push
- keep JetStream as the backend service event bus
- do not mix browser edge transport with internal service eventing

---

## Architectural Decisions

### Decision 1: Keep notification business logic in backend

Do not introduce a separate notification worker or notification microservice in this change.

`NotificationService` remains the owner of:

- `Alert` creation
- unread-count payload generation
- tenant-group broadcast
- optional email send

Only the browser transport edge changes.

### Decision 2: Keep the channel-layer group model

Do not replace the existing tenant group model.

Keep:

- `tenant_notifications_{tenant_id}`

Both WebSocket compatibility mode and new SSE should consume the same group events during rollout.

### Decision 3: Use SSE with header-based auth

Do not put JWT tokens in query parameters for the new SSE path.

Use:

- `Authorization: Bearer <token>`
- `X-Tenant-ID: <tenant_id>`

That means the frontend should use `fetch`-based SSE, not plain `EventSource`.

### Decision 4: Keep REST as the correctness path

SSE is the fast path for user experience.

Correctness still comes from:

- alert persistence in Postgres
- REST hydration on connect/reconnect
- REST mark-read updates

Do not attempt to make SSE the source of truth.

### Decision 5: Normalize browser-live alert channel naming

Do not keep writing new browser-live alerts as `channel="websocket"`.

Write:

- `channel="realtime"`

This makes the data model transport-neutral and keeps future changes cleaner.

Legacy rows can remain unchanged.

---

## Non-Goals

Do not do any of the following as part of this work:

- rewrite notification fan-out into a new worker topology
- remove JetStream or redesign the internal event bus
- change MediaMTX architecture
- change video preview transport beyond documenting it
- move mark-read into the live connection
- replace REST hydration with live-stream replay
- introduce GraphQL, gRPC, or browser polling
- fully remove WebSocket on day one

---

## Agent Reading Order

To minimize token usage, the next agent should read files in this exact order and stop unless blocked:

1. `services/backend/api/notification_service.py`
2. `services/backend/api/consumers.py`
3. `services/backend/api/routing.py`
4. `services/backend/server/asgi.py`
5. `services/backend/server/urls.py`
6. `services/backend/api/views.py`
7. `services/backend/api/models.py`
8. `web/ui/client/src/hooks/useNotifications.ts`
9. `web/ui/package.json`
10. `deploy/nginx/nginx.conf`

If those files are understood, do not scan the rest of the repo before implementing.

---

## Detailed Implementation Plan

## Phase 1: Backend Realtime Helpers

Create a small shared helper layer so SSE and WebSocket compatibility mode do not diverge.

### [NEW] `services/backend/api/realtime_notifications.py`

Create a helper module with narrow, reusable functions.

Suggested responsibilities:

- `authenticate_user_from_bearer(token: str)`
- `parse_tenant_id(value: str | None)`
- `verify_tenant_membership(user, tenant_id: int) -> bool`
- `build_group_name(tenant_id: int) -> str`
- `filter_notification_for_user(event_data: dict, user_id: int) -> dict | None`
- `encode_sse(event_name: str, data: dict, event_id: str | None = None) -> bytes`

Why:

- `NotificationConsumer.notification_message(...)` already contains important per-user filtering logic.
- If SSE reimplements that logic separately, the two transports will drift.
- Put the shared behavior in one small module and call it from both transports.

Rules:

- Do not move broad business logic here.
- Keep it focused on auth, group naming, and payload filtering/encoding.

---

## Phase 2: Add SSE Consumer

Use Django Channels `AsyncHttpConsumer` for SSE so the backend can keep using the existing tenant group broadcast flow.

### [NEW] `services/backend/api/consumers_sse.py`

Add a new SSE consumer, for example:

- `NotificationSSEConsumer`

Expected behavior:

1. Authenticate using `Authorization` header.
2. Read tenant from `X-Tenant-ID`.
3. Verify membership against `Membership`.
4. Join `tenant_notifications_{tenant_id}` via the channel layer.
5. Respond with:
   - `Content-Type: text/event-stream`
   - `Cache-Control: no-cache`
   - `Connection: keep-alive`
6. Send an initial event confirming connection.
7. Send heartbeat comments or lightweight events every `15-30s`.
8. On `notification_message`, reuse shared per-user filtering helper.
9. Emit SSE records like:
   - `event: notification`
   - `id: <alert_id when available>`
   - `data: <json payload>`
10. Clean up group membership and heartbeat task on disconnect.

Important:

- SSE is server-to-client only.
- Do not add bidirectional client commands.
- `mark_read` stays REST-only.
- Do not implement polling-based SSE.

### Recommended implementation notes

- Use `AsyncHttpConsumer`, not a normal Django streaming view.
- Use a small heartbeat task so proxies do not close idle streams.
- Keep failures isolated to the current connection.
- Log auth failures, membership failures, and send failures clearly.

---

## Phase 3: ASGI Routing For SSE

The current ASGI app sends all HTTP traffic directly to Django. That must change so the SSE path can be handled by Channels.

### [MODIFY] `services/backend/api/routing.py`

Keep the current websocket route for compatibility:

- `^ws/notifications/$`

Add HTTP routing for SSE, for example:

- `^api/notifications/stream/$`

Suggested structure:

- `websocket_urlpatterns`
- `http_urlpatterns`

### [MODIFY] `services/backend/server/asgi.py`

Refactor HTTP routing so the SSE path is handled by Channels before falling back to Django.

Target shape:

- specific HTTP path for SSE -> `NotificationSSEConsumer`
- everything else -> existing `SafeHttpAsgiApp(django_asgi_app)`

Important:

- Do not break normal Django HTTP routing.
- Do not remove the existing WebSocket path yet.
- Keep the fallback safe 503 behavior already present in `SafeHttpAsgiApp`.

---

## Phase 4: Backend Notification Service Normalization

Do not redesign notification persistence. Just make the language and persisted metadata transport-neutral.

### [MODIFY] `services/backend/api/notification_service.py`

Required changes:

1. Update docstrings/comments that currently say "WebSocket broadcasting" so they say:
   - realtime browser delivery
   - channel-layer broadcast
   - realtime push

2. Keep `_broadcast_to_channel(...)` as the core backend push method.
   - Do not rename the tenant group model.
   - Do not replace it with direct SSE writes.

3. Change new `Alert` writes for browser-live delivery:
   - from `channel="websocket"`
   - to `channel="realtime"`

4. Consider returning a transport-neutral result key in the service response:
   - preferred: `realtime`
   - temporary compatibility alias: keep `websocket` in results for one release if tests/callers still expect it

Recommended transitional response shape:

```json
{
  "realtime": "sent",
  "websocket": "sent",
  "email": null,
  "push": null,
  "alerts_created": 3
}
```

This lets existing tests and callers survive while new code migrates toward `realtime`.

### [MODIFY] `services/backend/api/views.py`

There is a second browser-alert write path in `_ensure_user_alert_backfill(...)` that still uses:

- `channel="websocket"`

Change that to:

- `channel="realtime"`

Also update notification-related docstrings and comments that still explicitly describe the browser path as WebSocket-only.

### [MODIFY] `services/backend/api/models.py`

The `Alert.channel` comment currently says:

- `email|webhook|sms|push`

It should be updated to reflect actual runtime usage, for example:

- `realtime|email|webhook|sms|push`

No schema change is required just for this comment-level correction.

---

## Phase 5: Notification Endpoints And Naming Cleanup

The current test endpoint names are too transport-specific.

### [MODIFY] `services/backend/server/urls.py`

Add a transport-neutral test endpoint:

- `POST /api/notifications/test-realtime/`

Keep the existing endpoint temporarily as a deprecated alias:

- `POST /api/notifications/test-websocket/`

Both can point to the same implementation during rollout.

### [MODIFY] `services/backend/api/views.py`

Rename or wrap the current test function so the implementation becomes transport-neutral.

Preferred target:

- internal function behavior stays the same
- response/message text stops saying "WebSocket"
- response text says "realtime notification"

Also update `notifications_test_incident(...)` doc wording if it says the final UI hop is "websocket". After this migration it should say "realtime browser stream" or "SSE/browser live transport".

Important:

- Do not change the incident creation/broadcast semantics here.
- This is naming cleanup, not business logic redesign.

---

## Phase 6: Frontend SSE Hook Migration

The frontend hook should switch from WebSocket to header-based SSE while preserving all existing correctness behavior.

### [MODIFY] `web/ui/package.json`

Add:

- `@microsoft/fetch-event-source`

Why:

- native `EventSource` does not cleanly support the `Authorization` header
- the current system already relies on JWT auth
- query-string tokens should not be the new SSE design

### [MODIFY] `web/ui/client/src/hooks/useNotifications.ts`

This is the main frontend implementation change.

Required behavior:

1. Keep REST hydration logic unchanged:
   - `/api/notifications/`
   - `/api/notifications/unread-count/`

2. Keep REST mark-read logic unchanged:
   - `/api/notifications/mark-read/`

3. Replace the WebSocket connection logic with SSE:
   - replace `WebSocket` usage
   - replace `resolveWsUrl()` with `resolveSseUrl()`
   - use `fetchEventSource(...)`
   - pass `Authorization` and `X-Tenant-ID`

4. Remove ping/pong logic.

5. Keep reconnect behavior:
   - automatic retry with bounded backoff
   - re-hydrate after reconnect if needed

6. Preserve payload normalization and dedupe behavior:
   - keep `processedAlertIds`
   - keep `upsertNotification(...)`
   - keep invalidation of incident query cache

7. Keep transport health polling:
   - `/api/notifications/transport-status/`

8. Rename frontend test helper:
   - preferred: `testRealtime(...)`
   - temporary alias: keep `testWebSocket(...)` only if call sites still need it

9. Keep `isConnected` and `isSubscribed` semantics as UI state even though SSE is not a subscription protocol in the WebSocket sense.

Implementation note:

- On stream open, set `isConnected=true` and `isSubscribed=true`.
- On stream failure/abort, set them back to `false`.

Important:

- Do not replace hydration with SSE replay.
- Do not remove REST unread-count refresh behavior.
- Do not invent a new notification payload contract if the current payload already works.

---

## Phase 7: Proxy / Ingress Changes

SSE needs different proxy treatment than WebSocket.

### [MODIFY] `deploy/nginx/nginx.conf`

Add a dedicated SSE location for:

- `/api/notifications/stream/`

Required proxy characteristics:

- `proxy_pass http://django/api/notifications/stream/`
- `proxy_http_version 1.1`
- `proxy_buffering off`
- long read timeout
- long send timeout
- standard forwarded headers

Do not configure this path as a WebSocket upgrade route.

Example operational requirements:

- buffered proxying must be disabled
- the stream must remain open for a long time
- heartbeats from the backend should keep the connection alive

The existing WebSocket Nginx location can remain during rollout and be removed later.

---

## Phase 8: Optional WebSocket Deprecation

Do not remove WebSocket immediately.

Recommended rollout:

### Step 1

- Add SSE backend path
- add SSE frontend hook
- keep WebSocket compatibility path untouched

### Step 2

- make SSE the default browser live transport
- keep WebSocket route available for a short compatibility window

### Step 3

- once the frontend is fully cut over and verified, deprecate and later remove:
  - `/ws/notifications/`
  - WebSocket-specific frontend code
  - WebSocket-specific test naming

This should happen only after production verification.

---

## Exact File-Level Change List

### New files

- `services/backend/api/realtime_notifications.py`
- `services/backend/api/consumers_sse.py`

### Modified backend files

- `services/backend/api/notification_service.py`
- `services/backend/api/consumers.py`
- `services/backend/api/routing.py`
- `services/backend/server/asgi.py`
- `services/backend/server/urls.py`
- `services/backend/api/views.py`
- `services/backend/api/models.py`

### Modified frontend files

- `web/ui/package.json`
- `web/ui/client/src/hooks/useNotifications.ts`

### Modified deployment files

- `deploy/nginx/nginx.conf`

### Tests/docs that will likely need updates

- `services/backend/api/test_notifications.py`
- `services/backend/api/test_management_commands.py`
- any frontend tests covering `useNotifications.ts`
- docs/comments that still say "WebSocket" where browser live transport is now SSE

---

## Implementation Guardrails For The Next Agent

These rules are intentionally explicit to reduce hallucination and prevent unnecessary refactors.

### Do

- reuse the existing `tenant_notifications_{tenant_id}` group
- keep `NotificationService.broadcast_incident(...)` as the business owner
- preserve current notification payload shape as much as possible
- reuse the existing per-user filtering behavior for `alert_ids_by_user` and `unread_counts_by_user`
- use header-based auth for SSE
- keep REST hydration and mark-read as-is
- normalize new browser-live alert channel values to `realtime`
- keep WebSocket temporarily during migration

### Do Not

- do not build a notification microservice
- do not move browser live notifications onto JetStream directly
- do not replace the alert persistence flow
- do not invent a new browser notification schema
- do not use query-string JWTs for the new SSE design
- do not turn SSE into polling
- do not remove WebSocket before frontend SSE is verified
- do not touch MediaMTX, RTSP, or browser video transport as part of this change

---

## Verification Plan

## Backend Verification

1. Auth and membership
- open SSE stream with valid token + valid tenant -> success
- open SSE stream with invalid token -> rejected
- open SSE stream with valid token but wrong tenant -> rejected

2. Group delivery
- broadcast a test message to a tenant
- confirm only members of that tenant receive the stream event

3. Per-user filtering
- send payload with `alert_ids_by_user`
- confirm each connected user receives only their own `alert_id` and unread count

4. Keepalive behavior
- leave the stream idle behind nginx
- confirm it stays open because of heartbeat traffic

5. Persistence semantics
- create incident
- confirm `Alert` rows are still created
- confirm new rows use `channel="realtime"` for browser live delivery paths

## Frontend Verification

1. Initial hydrate
- page load -> notifications list and unread count populate from REST

2. Live push
- trigger test realtime notification
- confirm dropdown updates without refresh
- confirm unread count increments exactly once

3. Reconnect
- restart backend or nginx
- confirm the client reconnects and rehydrates correctly

4. Mark-read
- mark one notification read
- confirm REST mark-read still works
- confirm unread count decrements correctly

## Proxy Verification

1. SSE through nginx
- connect through proxied `/api/notifications/stream/`
- confirm the stream remains open
- confirm there is no buffering delay

2. Compatibility
- if WebSocket is still enabled during rollout, confirm existing WebSocket route still works until cutover is complete

---

## Acceptance Criteria

This migration is done when all of the following are true:

- browser live notifications use SSE by default
- REST hydration and mark-read remain unchanged and correct
- backend still persists alerts before pushing live updates
- new browser-live alerts no longer write `channel="websocket"`
- nginx supports SSE without buffering issues
- WebSocket remains only as temporary compatibility, not the primary browser path
- no new notification microservice was introduced

---

## One-Line Mental Model

Use `REST` for browser reads/writes, `SSE` for browser live notifications, `JetStream` for cross-service events, `REST` for MediaMTX control, and keep the existing backend notification service as the owner of alert persistence and tenant-group broadcast.
