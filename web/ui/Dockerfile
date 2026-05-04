# ── Stage 1: Build the React UI ──────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app
ARG VITE_API_BASE_URL=/api
ARG VITE_ENABLE_WEBRTC=false
ARG VITE_WEBRTC_VIEWER_BASE_URL=
ARG VITE_HLS_VIEWER_BASE_URL=
ARG VITE_ALLOW_LOOPBACK_WEBRTC_ON_WINDOWS=false
ARG VITE_ENABLE_MEDIAMTX_API_HEALTHCHECK=true
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_ENABLE_WEBRTC=${VITE_ENABLE_WEBRTC}
ENV VITE_WEBRTC_VIEWER_BASE_URL=${VITE_WEBRTC_VIEWER_BASE_URL}
ENV VITE_HLS_VIEWER_BASE_URL=${VITE_HLS_VIEWER_BASE_URL}
ENV VITE_ALLOW_LOOPBACK_WEBRTC_ON_WINDOWS=${VITE_ALLOW_LOOPBACK_WEBRTC_ON_WINDOWS}
ENV VITE_ENABLE_MEDIAMTX_API_HEALTHCHECK=${VITE_ENABLE_MEDIAMTX_API_HEALTHCHECK}
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci

COPY web/ui/ .
RUN npm run build

# ── Stage 2: Serve via Nginx ─────────────────────────────────
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy our custom nginx config template
COPY deploy/nginx/nginx.conf /etc/nginx/templates/nginx.conf.template

# Copy Vite build output (dist/public/) to nginx html root
COPY --from=builder /app/dist/public /usr/share/nginx/html

EXPOSE 8085
CMD ["sh", "-c", "envsubst '${NGINX_BACKEND_UPSTREAM} ${NGINX_AI_UPSTREAM}' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/nginx.conf && nginx -g 'daemon off;'"]
