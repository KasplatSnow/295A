import { useQuery } from "@tanstack/react-query";
import { getWebRtcViewerBaseUrl, isWebRtcEnabled } from "@/lib/streaming";

interface UseMediamtxHealthResult {
  reachable: boolean;
  checked: boolean;
  activePaths: Set<string>;
}

/**
 * Check if MediaMTX is reachable by hitting its configured base URL.
 * Also retrieves the list of active paths via proxy to ensure we don't load 
 * an iframe for a stream that is not publishing (prevents "stream not found" error).
 */
export function useMediamtxHealth(): UseMediamtxHealthResult {
  const webrtcEnabled = isWebRtcEnabled();
  const apiHealthcheckEnabled = String(import.meta.env.VITE_ENABLE_MEDIAMTX_API_HEALTHCHECK ?? "true").toLowerCase() === "true";
  const baseUrl = getWebRtcViewerBaseUrl();

  const query = useQuery({
    queryKey: ["mediamtx-health"],
    queryFn: async () => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3000);

      try {
        if (apiHealthcheckEnabled) {
          try {
            const pathsRes = await fetch("/mediamtx_api/v3/paths/list", {
              signal: controller.signal,
            });

            if (pathsRes.ok) {
              const data = await pathsRes.json();
              const paths = new Set<string>();
              if (data && data.items) {
                data.items.forEach((item: any) => {
                  if (item.name && item.ready === true) {
                    paths.add(item.name);
                  }
                });
              }
              return { reachable: true, activePaths: paths };
            }
          } catch (apiErr) {
            console.debug("MediaMTX API health check failed, falling back to reachability", apiErr);
          }
        }

        // Any HTTP response from the WebRTC listener proves reachability, even if `/` is not routable.
        const fallbackRes = await fetch(`${baseUrl.replace(/\/$/, "")}/`, {
          method: "HEAD",
          signal: controller.signal,
        });
        return {
          reachable: fallbackRes.ok || fallbackRes.status === 404 || fallbackRes.type === "opaque",
          activePaths: new Set<string>(),
        };
      } catch (err) {
        return { reachable: false, activePaths: new Set<string>() };
      } finally {
        clearTimeout(timeoutId);
      }
    },
    enabled: webrtcEnabled && !!baseUrl,
    staleTime: 5000, // Cache for 5s to avoid proxy storm
  });

  if (!webrtcEnabled || !baseUrl) {
    return { reachable: false, checked: true, activePaths: new Set() };
  }

  return {
    reachable: query.data?.reachable ?? false,
    checked: !query.isPending,
    activePaths: query.data?.activePaths ?? new Set(),
  };
}
