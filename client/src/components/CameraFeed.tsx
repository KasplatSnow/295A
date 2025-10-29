import { Video, Wifi, WifiOff } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface CameraFeedProps {
  name: string;
  location: string;
  status: "active" | "offline";
  imageUrl?: string;
  timestamp?: string;
}

export default function CameraFeed({ name, location, status, imageUrl, timestamp }: CameraFeedProps) {
  return (
    <Card className="overflow-hidden">
      <div className="relative aspect-video bg-muted">
        {imageUrl ? (
          <img src={imageUrl} alt={name} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Video className="w-12 h-12 text-muted-foreground" />
          </div>
        )}
        <div className="absolute top-2 left-2">
          <Badge variant={status === "active" ? "default" : "destructive"} className="gap-1">
            {status === "active" ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
            {status === "active" ? "Live" : "Offline"}
          </Badge>
        </div>
        {timestamp && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
            {timestamp}
          </div>
        )}
      </div>
      <div className="p-3">
        <h3 className="font-semibold text-sm" data-testid={`text-camera-${name.toLowerCase().replace(/\s/g, '-')}`}>{name}</h3>
        <p className="text-xs text-muted-foreground">{location}</p>
      </div>
    </Card>
  );
}
