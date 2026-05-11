import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, Edit, Trash2, Wifi, WifiOff, Share2, RefreshCw } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { api } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { useAuth } from "@/auth/AuthProvider";

const AI_LANES = [
  { id: "rt_detr", label: "General Object/Person Detection" },
  { id: "person_zone", label: "Zone Line Crossing / Intrusion" },
  { id: "fire_smoke_yolo", label: "Fire & Smoke Detection" },
  { id: "yolov8_fallback", label: "Lightweight Fallback (YOLOv8)" },
  { id: "audio_anomaly", label: "BEATs Audio Anomaly Detection" },
];

interface Camera {
  id: number | string;
  name: string;
  site: string;
  status: "active" | "inactive";
  rtsp_url?: string;
  ai_camera_id: string;
  stream_path?: string;
  audio_enabled?: boolean;
  is_ai_synced?: boolean;
  enabled_lanes?: string[];
  uncertainty_threshold?: number;
  normality_ema_alpha?: number;
  learned_fusion_mode?: "off" | "shadow" | "active";
  created_at: string;
  tenant?: number;
}

export default function Cameras() {
  const { toast } = useToast();
  const { atLeast } = useAuth();
  const canManage = atLeast("member");
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [newCamera, setNewCamera] = useState({
    name: "",
    site: "",
    rtsp_url: "",
    ai_camera_id: "",
    stream_path: "",
    audio_enabled: false,
    enabled_lanes: ["rt_detr", "person_zone"],
    uncertainty_threshold: 0.6,
    normality_ema_alpha: 0.05,
    learned_fusion_mode: "off",
  });

  const [editCamera, setEditCamera] = useState<Camera | null>(null);
  const [editForm, setEditForm] = useState({
    name: "",
    site: "",
    rtsp_url: "",
    ai_camera_id: "",
    stream_path: "",
    audio_enabled: false,
    status: "active",
    enabled_lanes: ["rt_detr", "person_zone"],
    uncertainty_threshold: 0.6,
    normality_ema_alpha: 0.05,
    learned_fusion_mode: "off",
  });

  const camerasQ = useQuery({
    queryKey: ["cameras"],
    queryFn: async () => {
      const { data } = await api.get("/cameras/");
      return (Array.isArray(data) ? data : data?.results ?? []) as Camera[];
    },
    retry: false,
  });

  const addMut = useMutation({
    mutationFn: async (cam: typeof newCamera) => {
      const { data } = await api.post("/cameras/", {
        name: cam.name,
        site: cam.site,
        rtsp_url: cam.rtsp_url,
        ai_camera_id: cam.ai_camera_id,
        stream_path: cam.stream_path,
        audio_enabled: cam.audio_enabled,
        enabled_lanes: cam.enabled_lanes,
        uncertainty_threshold: cam.uncertainty_threshold,
        normality_ema_alpha: cam.normality_ema_alpha,
        learned_fusion_mode: cam.learned_fusion_mode,
        status: "active",
      });
      // AI Sync is now manually triggered via the "Sync AI" button in the table actions
      // to prevent severe hardware utilization from auto-syncing upon camera creation.
      return data;
    },
    onSuccess: () => {
      toast({ title: "Camera added" });
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
      setNewCamera({ name: "", site: "", rtsp_url: "", ai_camera_id: "", stream_path: "", audio_enabled: false, enabled_lanes: ["rt_detr", "person_zone"] });
      setIsDialogOpen(false);
    },
    onError: () => {
      toast({ title: "Failed to add camera", variant: "destructive" });
    },
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number | string) => {
      await api.delete(`/cameras/${id}/`);
    },
    onSuccess: () => {
      toast({ title: "Camera deleted" });
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
    },
    onError: () => {
      toast({ title: "Failed to delete camera", variant: "destructive" });
    },
  });

  const editMut = useMutation({
    mutationFn: async ({ id, ...fields }: { id: number | string; [k: string]: unknown }) => {
      const { data } = await api.patch(`/cameras/${id}/`, fields);
      return data;
    },
    onSuccess: () => {
      toast({ title: "Camera updated" });
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
      setEditCamera(null);
    },
    onError: () => {
      toast({ title: "Failed to update camera", variant: "destructive" });
    },
  });

  const syncMut = useMutation({
    mutationFn: async (id: number | string) => {
      const { data } = await api.post(`/cameras/${id}/sync_to_ai/`);
      return data;
    },
    onSuccess: (data) => {
      toast({ title: "Camera synced to AI", description: `AI ID: ${data.ai_camera_id}` });
      queryClient.invalidateQueries({ queryKey: ["cameras"] });
    },
    onError: () => {
      toast({ title: "AI sync failed", variant: "destructive" });
    },
  });



  const openEditModal = (cam: Camera) => {
    setEditCamera(cam);
    setEditForm({
      name: cam.name,
      site: cam.site ?? "",
      rtsp_url: cam.rtsp_url ?? "",
      ai_camera_id: cam.ai_camera_id ?? "",
      stream_path: cam.stream_path ?? "",
      audio_enabled: cam.audio_enabled ?? false,
      status: cam.status ?? "active",
      enabled_lanes: cam.enabled_lanes ?? ["rt_detr", "person_zone"],
      uncertainty_threshold: cam.uncertainty_threshold ?? 0.6,
      normality_ema_alpha: cam.normality_ema_alpha ?? 0.05,
      learned_fusion_mode: (cam.learned_fusion_mode as "off"|"shadow"|"active") ?? "off",
    });
  };

  const handleAddCamera = () => {
    if (!canManage) return;
    addMut.mutate(newCamera);
  };
  const handleSaveEdit = () => {
    if (!canManage) return;
    if (!editCamera) return;
    editMut.mutate({ id: editCamera.id, ...editForm });
  };

  const handleTestConnection = async (isEdit: boolean = false) => {
    if (!canManage) return;
    const url = isEdit ? editForm.rtsp_url.trim() : newCamera.rtsp_url.trim();
    if (!url) {
      toast({ title: "No URL", description: "Enter an RTSP URL first.", variant: "destructive" });
      return;
    }

    toast({ title: "Testing connection…", description: `Tiered probe started for ${url}` });
    
    try {
      const endpoint = isEdit && editCamera ? `/cameras/${editCamera.id}/test_connection/` : "/cameras/test_connection/";
      const { data } = await api.post(endpoint, { rtsp_url: url, timeout_s: 10 });
      
      if (data.ok) {
        const d = data.details;
        const info = d ? `${d.codec ?? ""} ${d.width ?? ""}x${d.height ?? ""} ${d.fps ?? ""}`.trim() : "";
        toast({ 
          title: "Connection OK", 
          description: `Verified via ${data.method} — ${data.latency_ms}ms${info ? ` [${info}]` : ""}` 
        });
      } else {
        // Map backend categories to user-friendly messages
        const categoryMap: Record<string, string> = {
          network_unreachable: "Network Unreachable: Check camera power and local IP routing.",
          mediamtx_connection_timeout: "Relay Timeout: MediaMTX could not connect to the source URL.",
          mediamtx_api_unavailable: "System Error: The MediaMTX relay service is not responding.",
          unsupported_source: "Unsupported Protocol: This URL type is not supported for streaming.",
        };
        const description = categoryMap[data.category] || data.error || "Unknown probe failure";
        
        toast({ 
          title: "Connection Failed", 
          description, 
          variant: "destructive" 
        });
      }
    } catch (err: any) {
      toast({ 
        title: "Test Failed", 
        description: err.response?.data?.error || "Could not reach backend service", 
        variant: "destructive" 
      });
    }
  };

  const cameras: Camera[] = camerasQ.data ?? [];

  if (camerasQ.isLoading) return <div className="p-6">Loading cameras…</div>;
  if (camerasQ.isError) return <div className="p-6 text-destructive">Failed to load cameras.</div>;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Camera Management</h1>
        {canManage && (
          <div className="flex items-center gap-2">
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
              <DialogTrigger asChild>
                <Button data-testid="button-add-camera">
                  <Plus className="w-4 h-4 mr-2" />
                  Add Camera
                </Button>
              </DialogTrigger>
            <DialogContent>
            <DialogHeader>
              <DialogTitle>Add New Camera</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="camera-name">Camera Name</Label>
                <Input id="camera-name" value={newCamera.name} onChange={(e) => setNewCamera({ ...newCamera, name: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stream-url">Stream URL</Label>
                <Input id="stream-url" placeholder="rtsp://camera.local/stream" value={newCamera.rtsp_url} onChange={(e) => setNewCamera({ ...newCamera, rtsp_url: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="location">Location / Site</Label>
                <Input id="location" value={newCamera.site} onChange={(e) => setNewCamera({ ...newCamera, site: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ai-cam-id">AI Camera ID (optional)</Label>
                <Input id="ai-cam-id" value={newCamera.ai_camera_id} onChange={(e) => setNewCamera({ ...newCamera, ai_camera_id: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stream-path">Stream Path (optional)</Label>
                <Input id="stream-path" value={newCamera.stream_path} onChange={(e) => setNewCamera({ ...newCamera, stream_path: e.target.value })} />
              </div>
              <div className="flex items-center space-x-2 py-2">
                <Checkbox 
                  id="audio-enabled" 
                  checked={newCamera.audio_enabled}
                  onCheckedChange={(checked) => setNewCamera({ ...newCamera, audio_enabled: !!checked })}
                />
                <label htmlFor="audio-enabled" className="text-sm cursor-pointer select-none">
                  Enable Audio Detection (requires capable camera)
                </label>
              </div>
              <div className="space-y-2">
                <Label>AI Detection Lanes</Label>
                <div className="flex flex-col gap-2 border rounded-md p-3 bg-muted/20">
                  {AI_LANES.map(lane => (
                    <div key={lane.id} className="flex items-center space-x-2">
                      <Checkbox 
                        id={`new-lane-${lane.id}`} 
                        checked={newCamera.enabled_lanes.includes(lane.id)}
                        onCheckedChange={(checked) => {
                          const lanes = checked 
                            ? [...newCamera.enabled_lanes, lane.id]
                            : newCamera.enabled_lanes.filter(l => l !== lane.id);
                          setNewCamera({ ...newCamera, enabled_lanes: lanes });
                        }}
                      />
                      <label htmlFor={`new-lane-${lane.id}`} className="text-sm cursor-pointer select-none">{lane.label}</label>
                    </div>
                  ))}
                </div>
              </div>
              
              <div className="space-y-4 border rounded-md p-4 bg-muted/10 mt-4">
                <h4 className="text-sm font-semibold">Phase 2: Multimodal Fusion & Uncertainty</h4>
                <div className="space-y-2">
                  <Label htmlFor="learned-fusion-mode">Learned Fusion Mode</Label>
                  <Select value={newCamera.learned_fusion_mode} onValueChange={(v: "off"|"shadow"|"active") => setNewCamera({ ...newCamera, learned_fusion_mode: v })}>
                    <SelectTrigger id="learned-fusion-mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="off">Off (Deterministic Only)</SelectItem>
                      <SelectItem value="shadow">Shadow (Telemetry Only)</SelectItem>
                      <SelectItem value="active">Active (Gate Alerts)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="uncertainty">Uncertainty Threshold</Label>
                    <Input id="uncertainty" type="number" step="0.1" min="0" max="1" value={newCamera.uncertainty_threshold} onChange={(e) => setNewCamera({ ...newCamera, uncertainty_threshold: parseFloat(e.target.value) || 0.6 })} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="ema">Normality EMA Alpha</Label>
                    <Input id="ema" type="number" step="0.01" min="0" max="1" value={newCamera.normality_ema_alpha} onChange={(e) => setNewCamera({ ...newCamera, normality_ema_alpha: parseFloat(e.target.value) || 0.05 })} />
                  </div>
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => handleTestConnection(false)} data-testid="button-test-connection">Test Connection</Button>
              <Button onClick={handleAddCamera} disabled={addMut.isPending} data-testid="button-save-camera">
                {addMut.isPending ? "Saving…" : "Save"}
              </Button>
            </DialogFooter>
            </DialogContent>
          </Dialog>
          </div>
        )}
      </div>
      {!canManage && (
        <p className="text-sm text-muted-foreground">Viewer role has read-only access to cameras.</p>
      )}

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Camera Name</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>AI Camera ID</TableHead>
              <TableHead>Stream Path</TableHead>
              <TableHead>AI Status</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Added On</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cameras.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                  No cameras registered yet. Click "Add Camera" to get started.
                </TableCell>
              </TableRow>
            )}
            {cameras.map((camera) => (
              <TableRow key={camera.id}>
                <TableCell className="font-medium">{camera.name}</TableCell>
                <TableCell>{camera.site || "—"}</TableCell>
                <TableCell>{camera.ai_camera_id ? <Badge variant="outline">{camera.ai_camera_id}</Badge> : "—"}</TableCell>
                <TableCell>{camera.stream_path ? <Badge variant="outline">{camera.stream_path}</Badge> : "—"}</TableCell>
                <TableCell>
                  {camera.is_ai_synced ? (
                    <Badge variant="default" className="bg-blue-500/10 text-blue-600 hover:bg-blue-500/20 border-blue-500/20">Synced</Badge>
                  ) : (
                    <Badge variant="outline" className="text-muted-foreground">Unsynced</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    {camera.status === "active" ? (
                      <>
                        <Wifi className="w-4 h-4 text-green-600" />
                        <span className="text-green-600">Active</span>
                      </>
                    ) : (
                      <>
                        <WifiOff className="w-4 h-4 text-red-600" />
                        <span className="text-red-600">Inactive</span>
                      </>
                    )}
                  </div>
                </TableCell>
                <TableCell>{new Date(camera.created_at).toLocaleDateString()}</TableCell>
                <TableCell className="text-right">
                  {canManage ? (
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => syncMut.mutate(camera.id)} disabled={syncMut.isPending}>
                        <RefreshCw className="w-3 h-3 mr-1" />
                        Sync AI
                      </Button>
                      <Button size="sm" variant="outline">
                        <Share2 className="w-3 h-3 mr-1" />
                        Share
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => openEditModal(camera)}>
                        <Edit className="w-4 h-4" />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => deleteMut.mutate(camera.id)}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">Read-only</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={canManage && !!editCamera} onOpenChange={(open) => { if (!open) setEditCamera(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Camera</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="edit-name">Camera Name</Label>
              <Input id="edit-name" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-rtsp">Stream URL</Label>
              <Input id="edit-rtsp" placeholder="rtsp://..." value={editForm.rtsp_url} onChange={(e) => setEditForm({ ...editForm, rtsp_url: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-site">Location / Site</Label>
              <Input id="edit-site" value={editForm.site} onChange={(e) => setEditForm({ ...editForm, site: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-ai-id">AI Camera ID</Label>
              <Input id="edit-ai-id" value={editForm.ai_camera_id} onChange={(e) => setEditForm({ ...editForm, ai_camera_id: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-stream-path">Stream Path</Label>
              <Input id="edit-stream-path" value={editForm.stream_path} onChange={(e) => setEditForm({ ...editForm, stream_path: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-status">Status</Label>
              <Select value={editForm.status} onValueChange={(v) => setEditForm({ ...editForm, status: v })}>
                <SelectTrigger id="edit-status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="inactive">Inactive</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center space-x-2 py-2">
              <Checkbox 
                id="edit-audio-enabled" 
                checked={editForm.audio_enabled}
                onCheckedChange={(checked) => setEditForm({ ...editForm, audio_enabled: !!checked })}
              />
              <label htmlFor="edit-audio-enabled" className="text-sm cursor-pointer select-none">
                Enable Audio Detection (requires capable camera)
              </label>
            </div>
            <div className="space-y-2">
              <Label>AI Detection Lanes</Label>
              <div className="flex flex-col gap-2 border rounded-md p-3 bg-muted/20">
                {AI_LANES.map(lane => (
                  <div key={lane.id} className="flex items-center space-x-2">
                    <Checkbox 
                      id={`edit-lane-${lane.id}`} 
                      checked={editForm.enabled_lanes.includes(lane.id)}
                      onCheckedChange={(checked) => {
                        const lanes = checked 
                          ? [...editForm.enabled_lanes, lane.id]
                          : editForm.enabled_lanes.filter(l => l !== lane.id);
                        setEditForm({ ...editForm, enabled_lanes: lanes });
                      }}
                    />
                    <label htmlFor={`edit-lane-${lane.id}`} className="text-sm cursor-pointer select-none">{lane.label}</label>
                  </div>
                ))}
              </div>
              </div>
              
              <div className="space-y-4 border rounded-md p-4 bg-muted/10 mt-4">
                <h4 className="text-sm font-semibold">Phase 2: Multimodal Fusion & Uncertainty</h4>
                <div className="space-y-2">
                  <Label htmlFor="edit-learned-fusion-mode">Learned Fusion Mode</Label>
                  <Select value={editForm.learned_fusion_mode} onValueChange={(v: "off"|"shadow"|"active") => setEditForm({ ...editForm, learned_fusion_mode: v })}>
                    <SelectTrigger id="edit-learned-fusion-mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="off">Off (Deterministic Only)</SelectItem>
                      <SelectItem value="shadow">Shadow (Telemetry Only)</SelectItem>
                      <SelectItem value="active">Active (Gate Alerts)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="edit-uncertainty">Uncertainty Threshold</Label>
                    <Input id="edit-uncertainty" type="number" step="0.1" min="0" max="1" value={editForm.uncertainty_threshold} onChange={(e) => setEditForm({ ...editForm, uncertainty_threshold: parseFloat(e.target.value) || 0.6 })} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="edit-ema">Normality EMA Alpha</Label>
                    <Input id="edit-ema" type="number" step="0.01" min="0" max="1" value={editForm.normality_ema_alpha} onChange={(e) => setEditForm({ ...editForm, normality_ema_alpha: parseFloat(e.target.value) || 0.05 })} />
                  </div>
                </div>
              </div>
            </div>
          <DialogFooter>
            {editCamera && (
              <>
                <Button variant="outline" onClick={() => handleTestConnection(true)} data-testid="button-test-edit-connection">
                  Test Connection
                </Button>
                <Button variant="outline" onClick={() => syncMut.mutate(editCamera.id)} disabled={syncMut.isPending}>
                  <RefreshCw className="w-3 h-3 mr-1" />
                  Sync to AI
                </Button>
              </>
            )}
            <Button onClick={handleSaveEdit} disabled={editMut.isPending}>
              {editMut.isPending ? "Saving…" : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
