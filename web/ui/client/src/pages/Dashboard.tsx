import { useMemo, useState, lazy, Suspense, useEffect } from "react";
import { useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Clock3, Dog, Grid2x2, Maximize2, Shield, TrendingUp, User, Car, Video, Sparkles, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import CameraFeed from "@/components/CameraFeed";
import AlertCard from "@/components/AlertCard";
import StatsCard from "@/components/StatsCard";
import { api } from "@/lib/api";
import { buildWebRtcViewerUrl } from "@/lib/streaming";

import frontDoorImg from '@assets/generated_images/Front_door_camera_view_eee34996.png';

// Lazy load the charts component
const DashboardCharts = lazy(() => import("@/components/DashboardCharts"));

const TYPE_COLORS: Record<string, string> = {
  fire: "#ef4444",
  intrusion: "#f59e0b",
  robbery: "#f97316",
  stranger: "#10b981",
  violence: "#8b5cf6",
  other: "#64748b",
};

function severityLabel(severity?: number) {
  if (!severity) return "Info";
  if (severity >= 5) return "Critical";
  if (severity >= 4) return "Severe";
  if (severity >= 3) return "Moderate";
  if (severity >= 2) return "Low";
  return "Info";
}

function activityAccent(type: string) {
  if (type === "incident") return "border-l-red-500/70";
  if (type === "camera") return "border-l-sky-500/70";
  if (type === "entity") return "border-l-emerald-500/70";
  return "border-l-primary/60";
}

function getRecognizedEntity(details: Record<string, unknown> | undefined) {
  if (!details) return { name: "Unknown", confidence: 0 };
  const recognizedEntity = details.recognized_entity as Record<string, unknown> | undefined;
  const name = recognizedEntity?.name ?? details.entity_name ?? "Unknown";
  const confidence = recognizedEntity?.confidence ?? details.confidence ?? 0;
  return {
    name: String(name),
    confidence: Number(confidence || 0),
  };
}

export default function Dashboard() {
  const [, setLocation] = useLocation();
  const [zoneFilter, setZoneFilter] = useState("all");
  const [selectedCameraId, setSelectedCameraId] = useState<number | null>(null);
  const [isFullscreenOpen, setIsFullscreenOpen] = useState(false);
  const [isDocumentVisible, setIsDocumentVisible] = useState(!document.hidden);
  
  useEffect(() => {
    const handleVisibilityChange = () => setIsDocumentVisible(!document.hidden);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  const dashboardQ = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: async () => {
      const { data } = await api.get("/dashboard/summary/");
      return data as {
        cameras: Array<{ id: number; name: string; site: string; status: string; ai_camera_id: string; stream_path?: string; source_type?: string; is_ai_synced?: boolean }>;
        stats: { today: number; week: number; month: number; open?: number; critical?: number; camera_total?: number; camera_live?: number };
        recent_incidents: Array<any>;
        type_breakdown: Array<{ type: string; count: number }>;
        recent_audit: Array<{ id: number; action: string; actor: string; created_at: string; display_title?: string; display_description?: string; display_type?: string }>;
        ai_healthy?: boolean;
        entities?: Array<{ id: string; name: string; category: string; group: string }>;
        streams_health?: Record<string, any>;
      };
    },
    refetchInterval: isDocumentVisible ? 5000 : false,
    retry: false,
  });

  const data = dashboardQ.data;
  const cameras = useMemo(() => (data?.cameras ?? []).map((camera) => ({
    id: camera.id,
    name: camera.name,
    location: camera.site || "Unknown zone",
    status: (camera.status === "active" ? "active" : "offline") as "active" | "offline",
    isAiSynced: Boolean(camera.is_ai_synced),
    ai_camera_id: camera.ai_camera_id,
    streamUrl: buildWebRtcViewerUrl(camera.stream_path, camera.ai_camera_id),
    imageUrl: camera.id ? `/streams/${camera.id}/snapshot/` : frontDoorImg,
    sourceLabel: camera.source_type === "webcam" ? "Webcam" : "Registered",
    stream_path: camera.stream_path as string,
    health: data?.streams_health?.[String(camera.id)],
  })), [data?.cameras, data?.streams_health]);

  const zoneValues = Array.from(new Set(cameras.map((cam) => cam.location?.trim()).filter(Boolean)));
  const filteredCameras = cameras.filter((camera) => zoneFilter === "all" || camera.location === zoneFilter);
  const selectedCamera = filteredCameras.find((camera) => camera.id === selectedCameraId) ?? filteredCameras[0] ?? null;
  const galleryCameras = filteredCameras.filter((camera) => camera.id !== selectedCamera?.id);

  const stats = data?.stats ?? { today: 0, week: 0, month: 0, open: 0, critical: 0, camera_total: cameras.length, camera_live: cameras.filter((camera) => camera.status === "active").length };
  const pieData = (data?.type_breakdown ?? []).map((item) => ({
    name: item.type.charAt(0).toUpperCase() + item.type.slice(1),
    value: item.count,
    color: TYPE_COLORS[item.type] ?? "#64748b",
  }));

  const alerts = (data?.recent_incidents ?? []).slice(0, 6).map((incident) => {
    const details = incident.details as Record<string, unknown> | undefined;
    const entity = getRecognizedEntity(details);
    return {
      id: incident.id,
      type: (incident.type || "other") as "fire" | "intrusion" | "violence" | "crash",
      location: incident.camera__name || "Unknown",
      time: new Date(incident.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      entity: entity.name,
      confidence: entity.confidence,
      sourceLabel: incident.camera__source_type === "webcam" ? "Webcam" : "Registered",
      severity: incident.severity,
    };
  });

  const knownEntities = (data?.entities ?? []).map((entity) => ({
    name: String(entity.name ?? "Unknown"),
    type: (entity.category === "pet" ? "pet" : entity.category === "vehicle" ? "vehicle" : "person") as "person" | "pet" | "vehicle",
    group: String(entity.group ?? "household"),
  })).filter((entity) => entity.group === "household" || entity.group === "neighbor").slice(0, 8);

  const communityActivity = (data?.recent_audit ?? []).slice(0, 10).map((activity: any) => ({
    time: activity.created_at ? new Date(activity.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "",
    title: String(activity.display_title ?? activity.action ?? "Activity"),
    description: String(activity.display_description ?? ""),
    actor: activity.actor ? String(activity.actor) : "System",
    type: String(activity.display_type ?? "activity"),
  }));

  const getEntityIcon = (type: string) => {
    if (type === "person") return <User className="h-3 w-3" />;
    if (type === "pet") return <Dog className="h-3 w-3" />;
    return <Car className="h-3 w-3" />;
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4" data-testid="zone-chip-bar">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant={zoneFilter === "all" ? "default" : "outline"} onClick={() => setZoneFilter("all")}>All zones</Button>
          {zoneValues.map((zone) => (
            <Button key={zone} size="sm" variant={zoneFilter === zone ? "default" : "outline"} onClick={() => setZoneFilter(zone)}>
              {zone}
            </Button>
          ))}
        </div>
        {dashboardQ.isLoading && <span className="text-sm text-muted-foreground animate-pulse">Refreshing data…</span>}
      </div>

      <div className="grid gap-6 xl:grid-cols-12">
        <div className="space-y-6 xl:col-span-8">
          <Card className="overflow-hidden border-border/80">
            <div className="flex flex-col gap-4 border-b border-border/70 bg-muted/30 p-5 md:flex-row md:items-center md:justify-between">
              <div>
                <h2 className="text-lg font-semibold">Camera wall</h2>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary" className="gap-1"><Grid2x2 className="h-3 w-3" /> {filteredCameras.length} visible</Badge>
                <Button variant="outline" size="sm" onClick={() => setIsFullscreenOpen(true)}>
                  <Maximize2 className="mr-2 h-4 w-4" />
                  Expand wall
                </Button>
              </div>
            </div>
            <div className="space-y-5 p-5">
              {selectedCamera ? (
                <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.9fr)]">
                  <div className="space-y-3">
                    <CameraFeed
                      name={selectedCamera.name}
                      location={selectedCamera.location}
                      status={selectedCamera.status}
                      isAiSynced={selectedCamera.isAiSynced}
                      cameraId={selectedCamera.id}
                      streamPath={selectedCamera.stream_path}
                      imageUrl={selectedCamera.imageUrl}
                      streamUrl={selectedCamera.streamUrl}
                      isFocused={true}
                      health={selectedCamera.health}
                      timestamp={new Date().toLocaleTimeString()}
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{selectedCamera.sourceLabel}</Badge>
                      <Button size="sm" variant="outline" onClick={() => setSelectedCameraId(null)}>
                        Reset focus
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold">Snapshot gallery</h3>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {(galleryCameras.length > 0 ? galleryCameras : filteredCameras).map((camera) => (
                        <button key={camera.id} className="text-left" onClick={() => setSelectedCameraId(camera.id)}>
                          <CameraFeed
                            name={camera.name}
                            location={camera.location}
                            status={camera.status}
                            isAiSynced={camera.isAiSynced}
                            cameraId={camera.id}
                            imageUrl={camera.imageUrl}
                            streamUrl={camera.streamUrl}
                            health={camera.health}
                          />
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
                  {(filteredCameras.length > 0 ? filteredCameras : [{ id: 0, name: 'No cameras', location: '-', status: 'offline' as const, isAiSynced: false, imageUrl: frontDoorImg, streamUrl: undefined, health: undefined }]).map((camera, index) => (
                    <button
                      key={camera.id || index}
                      className="text-left"
                      onClick={() => camera.id && setSelectedCameraId(camera.id)}
                    >
                      <CameraFeed
                        name={camera.name}
                        location={camera.location}
                        status={camera.status}
                        isAiSynced={camera.isAiSynced}
                        cameraId={camera.id || undefined}
                        imageUrl={camera.imageUrl}
                        streamUrl={camera.streamUrl}
                        health={camera.health}
                        timestamp={new Date().toLocaleTimeString()}
                      />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Recent incidents</h2>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setLocation('/incidents')}>View all</Button>
              </div>
              <div className="space-y-3">
                {alerts.length === 0 && <p className="text-sm text-muted-foreground">No recent incidents.</p>}
                {alerts.map((alert) => (
                  <div key={alert.id} className="space-y-2 rounded-2xl border border-border/70 bg-background/60 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline">{severityLabel(alert.severity)}</Badge>
                      <span className="text-xs text-muted-foreground">{alert.sourceLabel}</span>
                    </div>
                    <AlertCard
                      type={alert.type}
                      location={alert.location}
                      time={alert.time}
                      entity={alert.entity}
                      confidence={alert.confidence}
                      sourceLabel={alert.sourceLabel}
                      onClick={() => setLocation(`/incidents/${alert.id}`)}
                    />
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Incident distribution</h2>
                </div>
                <Badge variant={data?.ai_healthy ? 'default' : 'secondary'}>{data?.ai_healthy ? 'AI healthy' : 'AI status unknown'}</Badge>
              </div>
              {pieData.length > 0 ? (
                <div className="min-h-[300px]">
                  <Suspense
                    fallback={
                      <div className="flex h-[250px] w-full items-center justify-center">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground/40" />
                      </div>
                    }
                  >
                    <DashboardCharts data={pieData} />
                  </Suspense>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {pieData.map((item) => (
                      <div key={item.name} className="flex items-center justify-between rounded-xl border border-border/70 px-3 py-2 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                          <span>{item.name}</span>
                        </div>
                        <span className="font-medium">{item.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No incident data yet.</p>
              )}
            </Card>
          </div>
        </div>

        <div className="space-y-6 xl:col-span-4">
          <div className="grid grid-cols-2 gap-3">
            <StatsCard title="Incidents Today" value={String(stats.today)} icon={Activity} />
            <StatsCard title="Open Incidents" value={String(stats.open ?? 0)} icon={AlertTriangle} />
            <StatsCard title="Critical Today" value={String(stats.critical ?? 0)} icon={Shield} />
            <StatsCard title="Live Cameras" value={`${stats.camera_live ?? 0}/${stats.camera_total ?? cameras.length}`} icon={Video} />
          </div>
          <Card className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">Community activity</h2>
                <p className="text-sm text-muted-foreground">Audit trail and shared actions.</p>
              </div>
              <Clock3 className="h-5 w-5 text-muted-foreground" />
            </div>
            <ScrollArea className="h-[22rem] pr-3">
              <div className="space-y-3">
                {communityActivity.length === 0 && <p className="text-sm text-muted-foreground">No recent activity.</p>}
                {communityActivity.map((activity, index) => (
                  <div key={`${activity.title}-${index}`} className={`rounded-2xl border border-border/70 border-l-4 bg-background/60 p-3 ${activityAccent(activity.type)}`}>
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">{activity.title}</p>
                      <span className="text-[11px] text-muted-foreground">{activity.time}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{activity.actor} · {activity.type}</p>
                    {activity.description && <p className="mt-2 text-sm text-muted-foreground">{activity.description}</p>}
                  </div>
                ))}
              </div>
            </ScrollArea>
          </Card>
        </div>
      </div>

      <Dialog open={isFullscreenOpen} onOpenChange={setIsFullscreenOpen}>
        <DialogContent className="max-w-7xl">
          <DialogHeader>
            <DialogTitle>Camera wall</DialogTitle>
          </DialogHeader>
          <div className="grid max-h-[75vh] grid-cols-1 gap-4 overflow-y-auto md:grid-cols-2 xl:grid-cols-3">
            {(filteredCameras.length > 0 ? filteredCameras : cameras).map((camera) => (
              <div key={camera.id}>
                <CameraFeed
                  name={camera.name}
                  location={camera.location}
                  status={camera.status}
                  cameraId={camera.id}
                  imageUrl={camera.imageUrl}
                  streamUrl={camera.streamUrl}
                  health={camera.health}
                  timestamp={new Date().toLocaleTimeString()}
                />
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
