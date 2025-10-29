import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Plus, Edit, Trash2, Wifi, WifiOff } from "lucide-react";

interface Camera {
  id: string;
  name: string;
  location: string;
  status: "active" | "offline";
  addedOn: string;
  streamUrl: string;
}

export default function Cameras() {
  const [cameras, setCameras] = useState<Camera[]>([
    { id: "1", name: "FrontDoorCam", location: "Entrance", status: "active", addedOn: "Oct 21, 2024", streamUrl: "rtsp://camera1.local" },
    { id: "2", name: "GarageCam", location: "Garage", status: "active", addedOn: "Oct 20, 2024", streamUrl: "rtsp://camera2.local" },
    { id: "3", name: "BackyardCam", location: "Backyard", status: "offline", addedOn: "Oct 19, 2024", streamUrl: "rtsp://camera3.local" },
  ]);
  
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [newCamera, setNewCamera] = useState({ name: "", location: "", streamUrl: "" });

  const handleAddCamera = () => {
    const camera: Camera = {
      id: Date.now().toString(),
      name: newCamera.name,
      location: newCamera.location,
      status: "active",
      addedOn: new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
      streamUrl: newCamera.streamUrl,
    };
    setCameras([...cameras, camera]);
    setNewCamera({ name: "", location: "", streamUrl: "" });
    setIsDialogOpen(false);
    console.log("Camera added:", camera);
  };

  const handleTestConnection = () => {
    console.log("Testing connection to:", newCamera.streamUrl);
  };

  const handleDelete = (id: string) => {
    setCameras(cameras.filter(c => c.id !== id));
    console.log("Camera deleted:", id);
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Camera Management</h1>
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
                <Input
                  id="camera-name"
                  placeholder="e.g., FrontDoorCam"
                  value={newCamera.name}
                  onChange={(e) => setNewCamera({ ...newCamera, name: e.target.value })}
                  data-testid="input-camera-name"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stream-url">Stream URL</Label>
                <Input
                  id="stream-url"
                  placeholder="rtsp://camera.local/stream"
                  value={newCamera.streamUrl}
                  onChange={(e) => setNewCamera({ ...newCamera, streamUrl: e.target.value })}
                  data-testid="input-stream-url"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="zone-tag">Zone Tag</Label>
                <Input
                  id="zone-tag"
                  placeholder="e.g., Entrance, Backyard"
                  value={newCamera.location}
                  onChange={(e) => setNewCamera({ ...newCamera, location: e.target.value })}
                  data-testid="input-zone-tag"
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={handleTestConnection} data-testid="button-test-connection">
                Test Connection
              </Button>
              <Button onClick={handleAddCamera} data-testid="button-save-camera">Save</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Camera Name</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Added On</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cameras.map((camera) => (
              <TableRow key={camera.id}>
                <TableCell className="font-medium" data-testid={`text-camera-${camera.id}`}>{camera.name}</TableCell>
                <TableCell>{camera.location}</TableCell>
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
                        <span className="text-red-600">Offline</span>
                      </>
                    )}
                  </div>
                </TableCell>
                <TableCell>{camera.addedOn}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button size="icon" variant="ghost" data-testid={`button-edit-${camera.id}`}>
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button 
                      size="icon" 
                      variant="ghost" 
                      onClick={() => handleDelete(camera.id)}
                      data-testid={`button-delete-${camera.id}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
