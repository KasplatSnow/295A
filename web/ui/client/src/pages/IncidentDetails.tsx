import { useRoute } from "wouter";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useState, useEffect, useRef } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, Download, CheckCircle, ShieldCheck } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { api } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { useAuth } from "@/auth/AuthProvider";

interface Incident {
  id: number;
  type: string;
  status: string;
  severity: number;
  started_at: string;
  ended_at: string | null;
  camera: number;
  camera_name: string;
  camera_source_type?: "registered" | "webcam";
  camera_source_label?: string;
  details: Record<string, unknown>;
  media_key: string;
}

const STATUS_BADGE: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  open: "destructive",
  acknowledged: "secondary",
  resolved: "outline",
};

export default function IncidentDetails() {
  const [, params] = useRoute("/incidents/:id");
  const incidentId = params?.id;
  const { toast } = useToast();
  const { atLeast } = useAuth();
  const canManageIncident = atLeast("member");

  const incidentQ = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: async () => {
      const { data } = await api.get(`/incidents/${incidentId}/`);
      return data as Incident;
    },
    enabled: !!incidentId,
    retry: false,
  });

  const ackMut = useMutation({
    mutationFn: async () => {
      const { data } = await api.post(`/incidents/${incidentId}/acknowledge/`);
      return data;
    },
    onSuccess: () => {
      toast({ title: "Incident acknowledged" });
      queryClient.invalidateQueries({ queryKey: ["incident", incidentId] });
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
    },
    onError: () => toast({ title: "Failed to acknowledge", variant: "destructive" }),
  });

  const resolveMut = useMutation({
    mutationFn: async () => {
      const { data } = await api.post(`/incidents/${incidentId}/resolve/`);
      return data;
    },
    onSuccess: () => {
      toast({ title: "Incident resolved" });
      queryClient.invalidateQueries({ queryKey: ["incident", incidentId] });
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
    },
    onError: () => toast({ title: "Failed to resolve", variant: "destructive" }),
  });

  const handleExport = () => {
    toast({ title: "Export started", description: "Generating report…" });
  };

  // ── Hooks that must run unconditionally (before any early returns) ──
  const [frameTick, setFrameTick] = useState(0);
  const [snapshotBlobUrl, setSnapshotBlobUrl] = useState<string | null>(null);
  const prevBlobRef = useRef<string | null>(null);

  // Determine snapshot + clip URLs (safe even when data is undefined)
  const incident = incidentQ.data;
  const clipUrl = (incident?.details as any)?.clip_url ?? "";
  const aiCameraId = String((incident?.details as any)?.ai_camera_id ?? "").trim();
  const snapshotUrl = incident?.media_key
    || (aiCameraId ? `/ai/frame/${encodeURIComponent(aiCameraId)}/` : "");

  // Auto-refresh live frame every 3s when falling back to AI feed
  useEffect(() => {
    if (incident && !incident.media_key && aiCameraId) {
      const iv = setInterval(() => setFrameTick(t => t + 1), 3000);
      return () => clearInterval(iv);
    }
  }, [incident, aiCameraId]);

  // Authenticated fetch for AI frame snapshots
  const snapshotApiUrl = snapshotUrl && !incident?.media_key && aiCameraId
    ? `${snapshotUrl}?quality=60&maxw=640&t=${frameTick}`
    : null;

  useEffect(() => {
    if (!snapshotApiUrl) {
      setSnapshotBlobUrl(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const resp = await api.get(snapshotApiUrl, { responseType: "blob" });
        if (cancelled) return;
        const url = URL.createObjectURL(resp.data);
        if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
        prevBlobRef.current = url;
        setSnapshotBlobUrl(url);
      } catch {
        // ignore — feed may be unavailable
      }
    })();
    return () => { cancelled = true; };
  }, [snapshotApiUrl]);

  // Cleanup blob on unmount
  useEffect(() => {
    return () => { if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current); };
  }, []);

  // The final src for the snapshot image
  const snapshotSrc = incident?.media_key
    ? snapshotUrl
    : (snapshotBlobUrl || "");

  if (incidentQ.isLoading) return <div className="p-6">Loading incident…</div>;
  if (incidentQ.isError || !incidentQ.data) return <div className="p-6 text-destructive">Incident not found.</div>;

  // After early returns, incident is guaranteed to be defined
  const inc = incidentQ.data;

  const formatTime = (iso: string) =>
    new Date(iso).toLocaleString([], { weekday: "long", hour: "2-digit", minute: "2-digit" });

  // Build timeline from details JSON if available, otherwise show detection event
  const timeline: Array<{ time: string; event: string }> = [];
  const det = inc.details as any;
  const audioUrl = det?.evidence?.audio_url ?? null;
  const modality = det?.modality ?? "video";
  const fusion = det?.fusion ?? null;
  const audioEv = det?.audio ?? null;
  const videoEv = det?.video ?? null;
  const recognizedEntity = (det?.recognized_entity && typeof det.recognized_entity === "object")
    ? det.recognized_entity as Record<string, unknown>
    : null;
  if (det?.timeline && Array.isArray(det.timeline)) {
    for (const t of det.timeline) {
      timeline.push({ time: t.time ?? "", event: t.event ?? t.description ?? "" });
    }
  } else {
    timeline.push({
      time: new Date(inc.started_at).toLocaleTimeString(),
      event: `${inc.type.charAt(0).toUpperCase() + inc.type.slice(1)} detected (severity ${inc.severity}/5)`,
    });
    if (inc.status === "acknowledged") {
      timeline.push({ time: "", event: "Incident acknowledged by operator" });
    }
    if (inc.status === "resolved" && inc.ended_at) {
      timeline.push({
        time: new Date(inc.ended_at).toLocaleTimeString(),
        event: "Incident resolved",
      });
    }
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold" data-testid="text-incident-title">
          {inc.type.charAt(0).toUpperCase() + inc.type.slice(1)} Detected
        </h1>
        <Badge variant={STATUS_BADGE[inc.status] ?? "default"} className="capitalize text-sm">
          {inc.status}
        </Badge>
      </div>
      <p className="text-muted-foreground">{formatTime(inc.started_at)}</p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Snapshot */}
        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Evidence Media</h2>
          <div className="relative aspect-video bg-muted rounded-lg overflow-hidden mb-4">
            {clipUrl ? (
              <video
                src={clipUrl}
                controls
                className="w-full h-full object-contain"
                poster={snapshotSrc || undefined}
              />
            ) : snapshotSrc ? (
              <img src={snapshotSrc} alt="Incident snapshot" className="w-full h-full object-cover" />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                No video snapshot available
              </div>
            )}
          </div>
          {audioUrl && (
            <div className="mb-4">
              <h3 className="text-sm font-semibold mb-2">Audio Recording</h3>
              <audio controls src={audioUrl} className="w-full" />
            </div>
          )}
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" data-testid="button-rewind">
              ⏪ Rewind 10s
            </Button>
            <Button variant="outline" size="sm" className="flex-1" data-testid="button-forward">
              Fast Forward 10s ⏩
            </Button>
          </div>
        </Card>

        {/* Right: Info */}
        <Card className="p-6 flex flex-col h-full">
          <h2 className="text-lg font-semibold mb-4">Incident Information</h2>
          <div className="space-y-4 flex-1">
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Type</span>
              <span className="font-medium capitalize" data-testid="text-incident-type">{inc.type.replace(/_/g, " ")}</span>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Camera</span>
              <div className="flex items-center gap-2">
                <span className="font-medium" data-testid="text-incident-location">{inc.camera_name || `#${inc.camera}`}</span>
                <Badge variant={inc.camera_source_type === "webcam" ? "secondary" : "outline"}>
                  {inc.camera_source_label || (inc.camera_source_type === "webcam" ? "Webcam" : "Registered")}
                </Badge>
              </div>
            </div>
            {modality !== "video" && (
              <div className="flex justify-between py-2 border-b">
                <span className="text-muted-foreground">Modality</span>
                <Badge variant="default" className={modality === "fusion" ? "bg-purple-100 text-purple-800 border-purple-200" : "bg-blue-50 text-blue-700 border-blue-200"}>
                  {modality === "fusion" ? "Audio-Video Fusion" : "Audio Only"}
                </Badge>
              </div>
            )}
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Severity</span>
              <span className={`font-medium ${inc.severity >= 4 ? "text-red-600" : ""}`}>
                {inc.severity}/5
              </span>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Status</span>
              <Badge variant={STATUS_BADGE[inc.status] ?? "default"} className="capitalize">
                {inc.status}
              </Badge>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Detected At</span>
              <span className="font-medium">{formatTime(inc.started_at)}</span>
            </div>
            {recognizedEntity && (
              <>
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Recognized Entity</span>
                  <span className="font-medium">{String(recognizedEntity.name ?? recognizedEntity.id ?? "Unknown")}</span>
                </div>
                <div className="flex justify-between py-2 border-b">
                  <span className="text-muted-foreground">Entity Type</span>
                  <span className="font-medium capitalize">{String(recognizedEntity.type ?? recognizedEntity.kind ?? "unknown")}</span>
                </div>
              </>
            )}
            {fusion && (
              <div className="py-2 border-b space-y-2">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Fusion Reason</span>
                  <span className="font-medium text-right ml-4 text-sm">{fusion.reason}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Audio Event</span>
                  <span className="font-medium capitalize text-blue-700">{audioEv?.label?.replace(/_/g, " ") ?? "Unknown"}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Video Event</span>
                  <span className="font-medium capitalize text-emerald-700">{videoEv?.label?.replace(/_/g, " ") ?? "Unknown"}</span>
                </div>
              </div>
            )}
            {(det?.shadow_score !== undefined || det?.uncertainty !== undefined) && (
              <div className="py-2 border-b space-y-2">
                <span className="text-muted-foreground block mb-1">AI Telemetry</span>
                <div className="grid grid-cols-2 gap-4 bg-muted/20 p-3 rounded-md text-sm border">
                  {det?.shadow_score !== undefined && (
                    <div>
                      <span className="text-muted-foreground block text-xs">Learned Shadow Score</span>
                      <span className="font-mono">{Number(det.shadow_score).toFixed(3)}</span>
                    </div>
                  )}
                  {det?.uncertainty !== undefined && (
                    <div>
                      <span className="text-muted-foreground block text-xs">Audio Uncertainty</span>
                      <span className={`font-mono ${Number(det.uncertainty) > 0.6 ? "text-amber-600 font-medium" : ""}`}>
                        {Number(det.uncertainty).toFixed(3)}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
            {inc.ended_at && (
              <div className="flex justify-between py-2 border-b">
                <span className="text-muted-foreground">Ended At</span>
                <span className="font-medium">{formatTime(inc.ended_at)}</span>
              </div>
            )}
          </div>

          <div className="flex gap-3 mt-6">
            {canManageIncident && inc.status === "open" && (
              <Button onClick={() => ackMut.mutate()} disabled={ackMut.isPending} className="flex-1" data-testid="button-acknowledge">
                <CheckCircle className="w-4 h-4 mr-2" />
                Acknowledge
              </Button>
            )}
            {canManageIncident && inc.status !== "resolved" && (
              <Button onClick={() => resolveMut.mutate()} disabled={resolveMut.isPending} variant="secondary" className="flex-1">
                <ShieldCheck className="w-4 h-4 mr-2" />
                Resolve
              </Button>
            )}
            <Button onClick={handleExport} variant="outline" className="flex-1" data-testid="button-export">
              <Download className="w-4 h-4 mr-2" />
              Export Report
            </Button>
          </div>
        </Card>
      </div>

      {/* Timeline */}
      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">Event Timeline</h2>
        <div className="space-y-3">
          {timeline.map((item, idx) => (
            <div key={idx} className="flex gap-4 items-start">
              <div className="text-sm text-muted-foreground w-24 shrink-0">{item.time}</div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-primary"></div>
                  <p className="text-sm">{item.event}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
