import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { UserPlus, Share2, Shield, FileText, Trash2, Send } from "lucide-react";

export default function Community() {
  const [members, setMembers] = useState([
    { id: "1", name: "Dev Ansodariya", email: "dev@example.com", role: "owner", joined: "Oct 1, 2024" },
    { id: "2", name: "Kumar Harsh", email: "harsh@example.com", role: "admin", joined: "Oct 5, 2024" },
    { id: "3", name: "Cameron Lee", email: "cameron@example.com", role: "member", joined: "Oct 10, 2024" },
    { id: "4", name: "Jason Park", email: "jason@example.com", role: "viewer", joined: "Oct 15, 2024" },
  ]);

  const [sharedCameras, setSharedCameras] = useState([
    { id: "1", camera: "Front Door", sharedWith: "All Members", zone: "Shared", status: "active" },
    { id: "2", camera: "Street Cam", sharedWith: "Neighborhood Watch", zone: "Street", status: "active" },
  ]);

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

  const [auditLogs] = useState([
    { id: "1", time: "2:15 PM", action: "Cameron granted Viewer access to PorchCam (Shared)" },
    { id: "2", time: "1:30 PM", action: "Jason added to Neighborhood Watch group" },
    { id: "3", time: "12:45 PM", action: "Harsh shared Street Cam with All Members" },
    { id: "4", time: "11:20 AM", action: "Dev updated privacy settings for Street zone" },
  ]);

  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("viewer");

  const handleInvite = () => {
    console.log("Inviting:", inviteEmail, inviteRole);
    setInviteEmail("");
    setInviteRole("viewer");
    setIsInviteDialogOpen(false);
  };

  const handleRemove = (id: string) => {
    setMembers(members.filter(m => m.id !== id));
    console.log("Removed member:", id);
  };

  const getRoleBadge = (role: string) => {
    if (role === "owner") return <Badge className="bg-purple-600">Owner</Badge>;
    if (role === "admin") return <Badge className="bg-blue-600">Admin</Badge>;
    if (role === "member") return <Badge variant="secondary">Member</Badge>;
    return <Badge variant="outline">Viewer</Badge>;
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Community</h1>

      <Tabs defaultValue="members" className="w-full">
        <TabsList>
          <TabsTrigger value="members" data-testid="tab-members">Members</TabsTrigger>
          <TabsTrigger value="cameras" data-testid="tab-shared-cameras">Shared Cameras</TabsTrigger>
          <TabsTrigger value="zones" data-testid="tab-zones">Zones & Access</TabsTrigger>
          <TabsTrigger value="audit" data-testid="tab-audit">Audit Log</TabsTrigger>
        </TabsList>

        <TabsContent value="members" className="space-y-4 mt-6">
          <div className="flex justify-between items-center">
            <p className="text-muted-foreground">Manage community members and their access levels</p>
            <Dialog open={isInviteDialogOpen} onOpenChange={setIsInviteDialogOpen}>
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
                      <SelectTrigger id="invite-role" data-testid="select-invite-role">
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
                  <Button variant="outline" onClick={() => setIsInviteDialogOpen(false)}>Cancel</Button>
                  <Button onClick={handleInvite} data-testid="button-send-invite">
                    <Send className="w-4 h-4 mr-2" />
                    Send Invite
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
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
                {members.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell className="font-medium" data-testid={`text-member-${member.id}`}>{member.name}</TableCell>
                    <TableCell>{member.email}</TableCell>
                    <TableCell>{getRoleBadge(member.role)}</TableCell>
                    <TableCell>{member.joined}</TableCell>
                    <TableCell className="text-right">
                      {member.role !== "owner" && (
                        <Button 
                          size="sm" 
                          variant="ghost" 
                          onClick={() => handleRemove(member.id)}
                          data-testid={`button-remove-${member.id}`}
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
            <p className="text-muted-foreground">Manage camera sharing with community members</p>
            <Button data-testid="button-share-camera">
              <Share2 className="w-4 h-4 mr-2" />
              Share Camera
            </Button>
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
                      <Button size="sm" variant="ghost">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </TabsContent>

        <TabsContent value="zones" className="space-y-4 mt-6">
          <p className="text-muted-foreground">Configure access policies and privacy settings for each zone</p>
          
          <div className="grid gap-4">
            {zones.map((zone, idx) => (
              <Card key={idx} className="p-6">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h3 className="text-lg font-semibold flex items-center gap-2">
                      <Shield className="w-5 h-5" />
                      {zone.name}
                      <Badge variant={zone.type === "private" ? "secondary" : "default"} className="ml-2">
                        {zone.type}
                      </Badge>
                    </h3>
                    <p className="text-sm text-muted-foreground mt-1">{zone.description}</p>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">Blur Faces for Non-Household</Label>
                      <p className="text-sm text-muted-foreground">Automatically blur unknown faces in shared feeds</p>
                    </div>
                    <Switch checked={zone.blurFaces} data-testid={`toggle-blur-${zone.name.toLowerCase()}`} />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">Share Only Incident Clips</Label>
                      <p className="text-sm text-muted-foreground">Only share video when incidents are detected</p>
                    </div>
                    <Switch checked={zone.shareOnlyIncidents} data-testid={`toggle-incidents-${zone.name.toLowerCase()}`} />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-base">Disable Audio for Shared Views</Label>
                      <p className="text-sm text-muted-foreground">Mute audio in community-shared feeds</p>
                    </div>
                    <Switch checked={zone.disableAudio} data-testid={`toggle-audio-${zone.name.toLowerCase()}`} />
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="audit" className="space-y-4 mt-6">
          <p className="text-muted-foreground">Track all community access and sharing activities</p>
          
          <Card className="p-6">
            <div className="space-y-4">
              {auditLogs.map((log) => (
                <div key={log.id} className="flex gap-4 items-start pb-4 border-b last:border-0">
                  <FileText className="w-4 h-4 text-muted-foreground mt-0.5" />
                  <div className="flex-1">
                    <p className="text-sm">{log.action}</p>
                    <p className="text-xs text-muted-foreground mt-1">{log.time}</p>
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
