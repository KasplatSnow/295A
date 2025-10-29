import { useState } from "react";
import { useLocation } from "wouter";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Search, Eye } from "lucide-react";

interface Incident {
  id: number;
  type: string;
  location: string;
  entity: string;
  confidence: number;
  timestamp: string;
  zone: string;
}

export default function Incidents() {
  const [, setLocation] = useLocation();
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState("all");
  const [filterEntity, setFilterEntity] = useState("all");
  const [filterZone, setFilterZone] = useState("all");

  const incidents: Incident[] = [
    { id: 203, type: "Fire", location: "Kitchen", entity: "Unknown", confidence: 92, timestamp: "Today, 2:31 PM", zone: "Home" },
    { id: 202, type: "Intrusion", location: "Backyard", entity: "Unknown", confidence: 88, timestamp: "Today, 1:12 AM", zone: "Home" },
    { id: 201, type: "Violence", location: "Garage", entity: "Jason P.", confidence: 85, timestamp: "Yesterday, 11:45 PM", zone: "Shared" },
    { id: 200, type: "Intrusion", location: "Front Door", entity: "Unknown Male 01", confidence: 76, timestamp: "Yesterday, 10:30 PM", zone: "Street" },
    { id: 199, type: "Fire", location: "Living Room", entity: "Dev", confidence: 95, timestamp: "2 days ago, 3:15 PM", zone: "Home" },
  ];

  const filteredIncidents = incidents.filter(incident => {
    const matchesSearch = 
      incident.type.toLowerCase().includes(searchQuery.toLowerCase()) ||
      incident.location.toLowerCase().includes(searchQuery.toLowerCase()) ||
      incident.entity.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = filterType === "all" || incident.type.toLowerCase() === filterType.toLowerCase();
    const matchesEntity = filterEntity === "all" || 
      (filterEntity === "known" && !incident.entity.includes("Unknown")) ||
      (filterEntity === "unknown" && incident.entity.includes("Unknown"));
    const matchesZone = filterZone === "all" || incident.zone.toLowerCase() === filterZone.toLowerCase();
    return matchesSearch && matchesType && matchesEntity && matchesZone;
  });

  const getEntityText = (entity: string) => {
    if (entity.includes("Unknown")) {
      return <span className="text-orange-600 font-medium">{entity}</span>;
    }
    return <span>{entity}</span>;
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Incidents</h1>
      </div>

      <Card className="p-6">
        <div className="flex flex-wrap gap-4 mb-6">
          <div className="flex-1 min-w-[200px]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search incidents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10"
                data-testid="input-search-incidents"
              />
            </div>
          </div>
          <Select value={filterType} onValueChange={setFilterType}>
            <SelectTrigger className="w-[180px]" data-testid="select-filter-type">
              <SelectValue placeholder="Type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Types</SelectItem>
              <SelectItem value="fire">Fire</SelectItem>
              <SelectItem value="intrusion">Intrusion</SelectItem>
              <SelectItem value="violence">Violence</SelectItem>
              <SelectItem value="crash">Crash</SelectItem>
            </SelectContent>
          </Select>
          <Select value={filterEntity} onValueChange={setFilterEntity}>
            <SelectTrigger className="w-[180px]" data-testid="select-filter-entity">
              <SelectValue placeholder="Entity" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Entities</SelectItem>
              <SelectItem value="known">Known</SelectItem>
              <SelectItem value="unknown">Unknown</SelectItem>
            </SelectContent>
          </Select>
          <Select value={filterZone} onValueChange={setFilterZone}>
            <SelectTrigger className="w-[180px]" data-testid="select-filter-zone">
              <SelectValue placeholder="Zone" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Zones</SelectItem>
              <SelectItem value="home">Home</SelectItem>
              <SelectItem value="street">Street</SelectItem>
              <SelectItem value="shared">Shared</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Incident ID</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>Entity</TableHead>
              <TableHead>Zone</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead>Time</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredIncidents.map((incident) => (
              <TableRow key={incident.id} className="cursor-pointer hover-elevate" onClick={() => setLocation(`/incidents/${incident.id}`)}>
                <TableCell className="font-medium" data-testid={`text-incident-${incident.id}`}>#{incident.id}</TableCell>
                <TableCell>{incident.type}</TableCell>
                <TableCell>{incident.location}</TableCell>
                <TableCell>{getEntityText(incident.entity)}</TableCell>
                <TableCell>{incident.zone}</TableCell>
                <TableCell>{incident.confidence}%</TableCell>
                <TableCell className="text-muted-foreground">{incident.timestamp}</TableCell>
                <TableCell className="text-right">
                  <Button 
                    size="sm" 
                    variant="outline"
                    onClick={(e) => {
                      e.stopPropagation();
                      setLocation(`/incidents/${incident.id}`);
                    }}
                    data-testid={`button-view-${incident.id}`}
                  >
                    <Eye className="w-3 h-3 mr-1" />
                    View
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
