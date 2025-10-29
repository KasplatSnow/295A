import { useRoute } from "wouter";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, Download, CheckCircle } from "lucide-react";
import frontDoorImg from '@assets/generated_images/Front_door_camera_view_eee34996.png';

export default function IncidentDetails() {
  const [, params] = useRoute("/incidents/:id");
  const incidentId = params?.id || "203";

  const incident = {
    id: incidentId,
    number: 203,
    type: "Fire",
    location: "Kitchen",
    confidence: 92,
    timestamp: "Friday, 2:30 PM",
    entity: "Unknown",
    entityMatchSuggestion: "Dev",
    entityMatchConfidence: 41,
    snapshotUrl: frontDoorImg,
  };

  const timeline = [
    { time: "2:30:15 PM", event: "Anomaly detected in Kitchen camera feed" },
    { time: "2:30:18 PM", event: "Fire classification confirmed (92% confidence)" },
    { time: "2:30:20 PM", event: "Alert sent to primary user" },
    { time: "2:30:45 PM", event: "SMS notification delivered" },
    { time: "2:31:03 PM", event: "Email notification sent" },
  ];

  const handleAcknowledge = () => {
    console.log("Incident acknowledged");
  };

  const handleExport = () => {
    console.log("Exporting report");
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold" data-testid="text-incident-title">
          Incident #{incident.number} — {incident.type} Detected
        </h1>
        <p className="text-muted-foreground mt-1">{incident.timestamp}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Video Snapshot</h2>
          <div className="relative aspect-video bg-muted rounded-lg overflow-hidden mb-4">
            <img src={incident.snapshotUrl} alt="Incident snapshot" className="w-full h-full object-cover" />
            <div className="absolute inset-0 flex items-center justify-center">
              <Button size="icon" variant="secondary" className="w-16 h-16 rounded-full" data-testid="button-play-video">
                <Play className="w-8 h-8" />
              </Button>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" data-testid="button-rewind">
              ⏪ Rewind 10s
            </Button>
            <Button variant="outline" size="sm" className="flex-1" data-testid="button-forward">
              Fast Forward 10s ⏩
            </Button>
          </div>
        </Card>

        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Incident Information</h2>
          <div className="space-y-4">
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Type</span>
              <span className="font-medium" data-testid="text-incident-type">{incident.type}</span>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Location</span>
              <span className="font-medium" data-testid="text-incident-location">{incident.location}</span>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Confidence</span>
              <span className="font-medium" data-testid="text-incident-confidence">{incident.confidence}%</span>
            </div>
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Entity</span>
              <span className="font-medium">{incident.entity}</span>
            </div>
            {incident.entityMatchSuggestion && (
              <div className="py-2 border-b">
                <div className="flex justify-between items-center mb-2">
                  <span className="text-muted-foreground">Suggested Match</span>
                  <span className="text-sm text-muted-foreground">{incident.entityMatchConfidence}% confidence</span>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" className="flex-1">
                    Confirm: {incident.entityMatchSuggestion}
                  </Button>
                  <Button size="sm" variant="ghost" className="flex-1">
                    Dismiss
                  </Button>
                </div>
              </div>
            )}
            <div className="flex justify-between py-2 border-b">
              <span className="text-muted-foreground">Detected At</span>
              <span className="font-medium">{incident.timestamp}</span>
            </div>
          </div>

          <div className="flex gap-3 mt-6">
            <Button onClick={handleAcknowledge} className="flex-1" data-testid="button-acknowledge">
              <CheckCircle className="w-4 h-4 mr-2" />
              Acknowledge
            </Button>
            <Button onClick={handleExport} variant="outline" className="flex-1" data-testid="button-export">
              <Download className="w-4 h-4 mr-2" />
              Export Report
            </Button>
          </div>
        </Card>
      </div>

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
