import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Checkbox } from "@/components/ui/checkbox";
import { Search, Plus, User, Dog, Car, Eye, Edit, Trash2, Upload } from "lucide-react";

interface Entity {
  id: string;
  name: string;
  type: "person" | "pet" | "vehicle";
  group: "household" | "neighbor" | "watchlist";
  lastSeen?: string;
  cameras?: string[];
  imageUrl?: string;
}

export default function Entities() {
  const [entities, setEntities] = useState<Entity[]>([
    { id: "1", name: "Dev", type: "person", group: "household", lastSeen: "Today, 2:15 PM", cameras: ["Front Door", "Living Room"], imageUrl: undefined },
    { id: "2", name: "Harsh", type: "person", group: "household", lastSeen: "Today, 1:30 PM", cameras: ["Garage"], imageUrl: undefined },
    { id: "3", name: "Cameron", type: "person", group: "household", lastSeen: "Today, 3:45 PM", cameras: ["Backyard"], imageUrl: undefined },
    { id: "4", name: "Bella", type: "pet", group: "household", lastSeen: "Today, 12:00 PM", cameras: ["Backyard", "Living Room"], imageUrl: undefined },
    { id: "5", name: "Unknown Male 01", type: "person", group: "watchlist", lastSeen: "Yesterday, 11:30 PM", cameras: ["Front Door"], imageUrl: undefined },
    { id: "6", name: "Gray SUV", type: "vehicle", group: "watchlist", lastSeen: "2 days ago", cameras: ["Street"], imageUrl: undefined },
  ]);

  const [filterType, setFilterType] = useState("all");
  const [filterGroup, setFilterGroup] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [newEntity, setNewEntity] = useState({
    name: "",
    type: "person" as const,
    group: "household" as const,
    notes: "",
    consentObtained: false,
  });

  const filteredEntities = entities.filter(entity => {
    const matchesType = filterType === "all" || entity.type === filterType;
    const matchesGroup = filterGroup === "all" || entity.group === filterGroup;
    const matchesSearch = entity.name.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesType && matchesGroup && matchesSearch;
  });

  const handleAddEntity = () => {
    const entity: Entity = {
      id: Date.now().toString(),
      name: newEntity.name,
      type: newEntity.type,
      group: newEntity.group,
      lastSeen: "Never",
      cameras: [],
    };
    setEntities([...entities, entity]);
    setNewEntity({ name: "", type: "person", group: "household", notes: "", consentObtained: false });
    setIsAddDialogOpen(false);
    console.log("Entity added:", entity);
  };

  const handleDelete = (id: string) => {
    setEntities(entities.filter(e => e.id !== id));
    console.log("Entity deleted:", id);
  };

  const getTypeIcon = (type: string) => {
    if (type === "person") return <User className="w-4 h-4" />;
    if (type === "pet") return <Dog className="w-4 h-4" />;
    return <Car className="w-4 h-4" />;
  };

  const getGroupBadge = (group: string) => {
    if (group === "household") return <Badge className="bg-green-600">Household</Badge>;
    if (group === "neighbor") return <Badge variant="secondary">Neighbor</Badge>;
    return <Badge variant="destructive">Watchlist</Badge>;
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-2xl font-bold">Entities</h1>
        
        <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
          <DialogTrigger asChild>
            <Button data-testid="button-add-entity">
              <Plus className="w-4 h-4 mr-2" />
              Add Entity
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Add New Entity</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="entity-type">Type</Label>
                <Select value={newEntity.type} onValueChange={(value: any) => setNewEntity({ ...newEntity, type: value })}>
                  <SelectTrigger id="entity-type" data-testid="select-entity-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="person">Person</SelectItem>
                    <SelectItem value="pet">Pet</SelectItem>
                    <SelectItem value="vehicle">Vehicle</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="entity-name">Display Name</Label>
                <Input
                  id="entity-name"
                  placeholder="e.g., John Doe, Bella, Gray SUV"
                  value={newEntity.name}
                  onChange={(e) => setNewEntity({ ...newEntity, name: e.target.value })}
                  data-testid="input-entity-name"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="entity-notes">Notes (Optional)</Label>
                <Textarea
                  id="entity-notes"
                  placeholder="Additional information..."
                  value={newEntity.notes}
                  onChange={(e) => setNewEntity({ ...newEntity, notes: e.target.value })}
                  data-testid="input-entity-notes"
                />
              </div>

              <div className="space-y-2">
                <Label>Reference Media</Label>
                <div className="border-2 border-dashed rounded-lg p-8 text-center">
                  <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground mb-1">Drag & drop or click to upload</p>
                  <p className="text-xs text-muted-foreground">Upload 3-5 clear, frontal photos in varied lighting</p>
                  <Button variant="outline" size="sm" className="mt-3" data-testid="button-upload-images">
                    Choose Files
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="entity-group">Group</Label>
                <Select value={newEntity.group} onValueChange={(value: any) => setNewEntity({ ...newEntity, group: value })}>
                  <SelectTrigger id="entity-group" data-testid="select-entity-group">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="household">Household</SelectItem>
                    <SelectItem value="neighbor">Neighbor</SelectItem>
                    <SelectItem value="watchlist">Watchlist</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-start space-x-2 pt-2">
                <Checkbox
                  id="consent"
                  checked={newEntity.consentObtained}
                  onCheckedChange={(checked) => setNewEntity({ ...newEntity, consentObtained: checked as boolean })}
                  data-testid="checkbox-consent"
                />
                <Label htmlFor="consent" className="text-sm leading-tight cursor-pointer">
                  I have obtained consent to store and use this entity's images for recognition.
                </Label>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsAddDialogOpen(false)}>Cancel</Button>
              <Button onClick={handleAddEntity} disabled={!newEntity.name || !newEntity.consentObtained} data-testid="button-save-entity">
                Save Entity
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <Card className="p-6">
        <div className="flex flex-wrap gap-4 mb-6">
          <div className="flex-1 min-w-[200px]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search entities..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10"
                data-testid="input-search-entities"
              />
            </div>
          </div>
          <Select value={filterType} onValueChange={setFilterType}>
            <SelectTrigger className="w-[180px]" data-testid="select-filter-type">
              <SelectValue placeholder="Type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Types</SelectItem>
              <SelectItem value="person">Person</SelectItem>
              <SelectItem value="pet">Pet</SelectItem>
              <SelectItem value="vehicle">Vehicle</SelectItem>
            </SelectContent>
          </Select>
          <Select value={filterGroup} onValueChange={setFilterGroup}>
            <SelectTrigger className="w-[180px]" data-testid="select-filter-group">
              <SelectValue placeholder="Group" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Groups</SelectItem>
              <SelectItem value="household">Household</SelectItem>
              <SelectItem value="neighbor">Neighbor</SelectItem>
              <SelectItem value="watchlist">Watchlist</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {filteredEntities.length === 0 ? (
          <div className="text-center py-12">
            <User className="w-16 h-16 mx-auto mb-4 text-muted-foreground" />
            <h3 className="text-lg font-semibold mb-2">No entities yet</h3>
            <p className="text-muted-foreground mb-4">Add family members, pets, or vehicles for smarter alerts.</p>
            <Button onClick={() => setIsAddDialogOpen(true)}>
              <Plus className="w-4 h-4 mr-2" />
              Add Your First Entity
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredEntities.map((entity) => (
              <Card key={entity.id} className="p-4">
                <div className="flex items-start gap-3">
                  <Avatar className="w-12 h-12">
                    <AvatarImage src={entity.imageUrl} />
                    <AvatarFallback className="bg-primary/10 text-primary">
                      {getTypeIcon(entity.type)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="font-semibold truncate" data-testid={`text-entity-${entity.id}`}>{entity.name}</h3>
                      {getGroupBadge(entity.group)}
                    </div>
                    <p className="text-xs text-muted-foreground capitalize">{entity.type}</p>
                    {entity.lastSeen && (
                      <p className="text-xs text-muted-foreground mt-1">Last seen: {entity.lastSeen}</p>
                    )}
                    {entity.cameras && entity.cameras.length > 0 && (
                      <p className="text-xs text-muted-foreground mt-1">Cameras: {entity.cameras.join(", ")}</p>
                    )}
                  </div>
                </div>
                <div className="flex gap-2 mt-4">
                  <Button size="sm" variant="outline" className="flex-1" data-testid={`button-view-${entity.id}`}>
                    <Eye className="w-3 h-3 mr-1" />
                    View
                  </Button>
                  <Button size="sm" variant="outline" className="flex-1" data-testid={`button-edit-${entity.id}`}>
                    <Edit className="w-3 h-3 mr-1" />
                    Edit
                  </Button>
                  <Button 
                    size="sm" 
                    variant="ghost" 
                    onClick={() => handleDelete(entity.id)}
                    data-testid={`button-delete-${entity.id}`}
                  >
                    <Trash2 className="w-3 h-3" />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
