import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { UserPlus, Share2, Shield, FileText, Trash2, Send } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { sendInvite } from "@/lib/invitations";
import { getMembers, MembershipRow, removeMember } from "@/lib/memberships";
import { queryClient } from "@/lib/queryClient";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/auth/AuthProvider";

export default function Community() {
  const { atLeast } = useAuth();
  const canManage = atLeast("admin");
  /* ── Shared cameras from API ───────────────────────────────── */
  const camerasQ = useQuery({
    queryKey: ["cameras-community"],
    queryFn: async () => {
      const { data } = await api.get("/cameras/");
      return (Array.isArray(data) ? data : data?.results ?? []) as Array<{
        id: number | string; name: string; site: string; status: string;
      }>;
    },
    retry: false,
  });

  const sharedCameras = (camerasQ.data ?? []).map((c) => ({
    id: String(c.id),
    camera: c.name,
    sharedWith: "All Members",
    zone: c.site || "Shared",
    status: c.status,
  }));

  const [zones] = useState([
    {
      name: "Home",
      type: "private",
      description: "Private zone for household members only",
      blurFaces: false,
      shareOnlyIncidents: false,
      disableAudio: false,
    },
    {
      name: "Street",
      type: "shared",
      description: "Shared with neighborhood for community safety",
      blurFaces: true,
      shareOnlyIncidents: true,
      disableAudio: true,
    },
    {
      name: "Shared",
      type: "community",
      description: "Community accessible areas",
      blurFaces: true,
      shareOnlyIncidents: false,
      disableAudio: false,
    },
  ]);

  /* ── Audit logs from API ──────────────────────────────────── */
  const auditQ = useQuery({
    queryKey: ["audit-logs"],
    queryFn: async () => {
      const { data } = await api.get("/audit/");
      return (Array.isArray(data) ? data : data?.results ?? []) as Array<{
        id: number | string; action: string; target_type: string;
        target_id: string; created_at: string; actor?: number; actor_username?: string;
        display_title?: string; display_description?: string; display_type?: string;
      }>;
    },
    retry: false,
  });

  const auditLogs = (auditQ.data ?? []).map((a) => ({
    id: String(a.id),
    time: new Date(a.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    action: a.display_title ?? a.action.replace(/\./g, " "),
    description: a.display_description ?? `${a.target_type} #${a.target_id}`,
    type: a.display_type ?? "activity",
    actor: a.actor_username ?? "System",
  }));

  const { toast } = useToast();

  const membersQ = useQuery({
    queryKey: ["memberships"],
    queryFn: getMembers,
    retry: false,
  });
  const inviteMut = useMutation({
    mutationFn: ({ email, role }: { email: string; role: string }) =>
      sendInvite(email, role),
    onSuccess: () => {
      toast({
        title: "Invite sent",
        description: "They can accept after signing in.",
      });
    },
    onError: () => {
      toast({ title: "Invite failed", variant: "destructive" });
    },
  });

  const removeMut = useMutation({
    mutationFn: (id: number | string) => removeMember(id),
    onSuccess: async () => {
      toast({ title: "Member removed" });
      await queryClient.invalidateQueries({ queryKey: ["memberships"] });
    },
    onError: () => {
      toast({ title: "Remove failed", variant: "destructive" });
    },
  });
  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("viewer");

  const handleInvite = () => {
    inviteMut.mutate({ email: inviteEmail, role: inviteRole });
    setInviteEmail("");
    setInviteRole("viewer");
    setIsInviteDialogOpen(false);
  };

  const handleRemove = (membershipId: string) => {
    removeMut.mutate(membershipId);
  };

  const getRoleBadge = (role: string) => {
    if (role === "owner") return <Badge className="bg-purple-600">Owner</Badge>;
    if (role === "admin") return <Badge className="bg-blue-600">Admin</Badge>;
    if (role === "member") return <Badge variant="secondary">Member</Badge>;
    return <Badge variant="outline">Viewer</Badge>;
  };

  if (membersQ.isLoading) {
    return <div className="p-6">Loading members…</div>;
  }
  if (membersQ.isError) {
    return <div className="p-6">Failed to load members.</div>;
  }

  const members: MembershipRow[] = membersQ.data ?? [];

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Community</h1>

      <Tabs defaultValue="members" className="w-full">
        <TabsList>
          <TabsTrigger value="members" data-testid="tab-members">
            Members
          </TabsTrigger>
          <TabsTrigger value="cameras" data-testid="tab-shared-cameras">
            Shared Cameras
          </TabsTrigger>
          <TabsTrigger value="zones" data-testid="tab-zones">
            Zones & Access
          </TabsTrigger>
          <TabsTrigger value="audit" data-testid="tab-audit">
            Audit Log
          </TabsTrigger>
        </TabsList>

        <TabsContent value="members" className="space-y-4 mt-6">
          <div className="flex justify-between items-center">
            <p className="text-muted-foreground">
              Manage community members and their access levels
            </p>
            {canManage ? (
              <Dialog
                open={isInviteDialogOpen}
                onOpenChange={setIsInviteDialogOpen}
              >
                <DialogTrigger asChild>
                  <Button data-testid="button-invite-member">
                    <UserPlus className="w-4 h-4 mr-2" />
                    Invite Member
                  </Button>
                </DialogTrigger>
                <DialogContent>
                <DialogHeader>
                  <DialogTitle>Invite New Member</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label htmlFor="invite-email">Email Address</Label>
                    <Input
                      id="invite-email"
                      type="email"
                      placeholder="member@example.com"
                      value={inviteEmail}
                      onChange={(e) => setInviteEmail(e.target.value)}
                      data-testid="input-invite-email"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="invite-role">Role</Label>
                    <Select value={inviteRole} onValueChange={setInviteRole}>
                      <SelectTrigger
                        id="invite-role"
                        data-testid="select-invite-role"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">Admin</SelectItem>
                        <SelectItem value="member">Member</SelectItem>
                        <SelectItem value="viewer">Viewer</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => setIsInviteDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleInvite}
                    data-testid="button-send-invite"
                  >
                    <Send className="w-4 h-4 mr-2" />
                    Send Invite
                  </Button>
                </DialogFooter>
                </DialogContent>
              </Dialog>
            ) : (
              <span className="text-xs text-muted-foreground">Read-only access</span>
            )}
          </div>

          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Joined</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-medium">
                      {m.user.username}
                    </TableCell>
                    <TableCell>{m.user.email}</TableCell>
                    <TableCell>{getRoleBadge(m.role)}</TableCell>
                    <TableCell>
                      {new Date(m.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right">
                      {canManage && m.role !== "owner" && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => removeMut.mutate(m.id)}
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </TabsContent>

        <TabsContent value="cameras" className="space-y-4 mt-6">
          <div className="flex justify-between items-center">
            <p className="text-muted-foreground">
              Manage camera sharing with community members
            </p>
            {canManage && (
              <Button data-testid="button-share-camera">
                <Share2 className="w-4 h-4 mr-2" />
                Share Camera
              </Button>
            )}
          </div>

          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Camera</TableHead>
                  <TableHead>Shared With</TableHead>
                  <TableHead>Zone</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sharedCameras.map((cam) => (
                  <TableRow key={cam.id}>
                    <TableCell className="font-medium">{cam.camera}</TableCell>
                    <TableCell>{cam.sharedWith}</TableCell>
                    <TableCell>{cam.zone}</TableCell>
                    <TableCell>
                      <Badge>{cam.status}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {canManage ? (
                        <Button size="sm" variant="ghost">
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">Read-only</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </TabsContent>

        <TabsContent value="zones" className="space-y-4 mt-6">
          <p className="text-muted-foreground">
            Configure access policies and privacy settings for each zone
          </p>

          <div className="grid gap-4">
            {zones.map((zone, idx) => (
              <Card key={idx} className="p-6">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h3 className="text-lg font-semibold flex items-center gap-2">
                      <Shield className="w-5 h-5" />
                      {zone.name}
                      <Badge
                        variant={
                          zone.type === "private" ? "secondary" : "default"
                        }
                        className="ml-2"
                      >
                        {zone.type}
                      </Badge>
                    </h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      {zone.description}
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">
                        Blur Faces for Non-Household
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Automatically blur unknown faces in shared feeds
                      </p>
                    </div>
                    <Switch
                      checked={zone.blurFaces}
                      disabled={!canManage}
                      data-testid={`toggle-blur-${zone.name.toLowerCase()}`}
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">
                        Share Only Incident Clips
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Only share video when incidents are detected
                      </p>
                    </div>
                    <Switch
                      checked={zone.shareOnlyIncidents}
                      disabled={!canManage}
                      data-testid={`toggle-incidents-${zone.name.toLowerCase()}`}
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">
                        Disable Audio for Shared Views
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Mute audio in community-shared feeds
                      </p>
                    </div>
                    <Switch
                      checked={zone.disableAudio}
                      disabled={!canManage}
                      data-testid={`toggle-audio-${zone.name.toLowerCase()}`}
                    />
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="audit" className="space-y-4 mt-6">
          <p className="text-muted-foreground">
            Track all community access and sharing activities
          </p>

          <Card className="p-6">
            <div className="space-y-4">
              {auditLogs.map((log) => (
                <div
                  key={log.id}
                  className="flex gap-4 items-start pb-4 border-b last:border-0"
                >
                  <FileText className="w-4 h-4 text-muted-foreground mt-0.5" />
                  <div className="flex-1">
                    <p className="text-sm">{log.action}</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {log.actor} · {log.type} · {log.time}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">{log.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
