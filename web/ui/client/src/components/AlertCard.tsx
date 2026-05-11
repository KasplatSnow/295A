import { Flame, UserX, AlertTriangle, Car, HelpCircle, Volume2, ShieldAlert } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AlertCardProps {
  type: "fire" | "intrusion" | "violence" | "crash" | "robbery" | "stranger" | "other" | "audio_anomaly" | "scream" | "gunshot" | "glass_break" | "explosion" | "multimodal_anomaly" | string;
  location: string;
  time: string;
  entity?: string;
  confidence?: number;
  sourceLabel?: string;
  modality?: string;
  modalities?: string[];
  hasAudioEvidence?: boolean;
  hasVideoEvidence?: boolean;
  onClick?: () => void;
}

const alertConfig: Record<string, { icon: React.ComponentType<{ className?: string }>; label: string; color: string }> = {
  fire: {
    icon: Flame,
    label: "Fire",
    color: "text-destructive",
  },
  intrusion: {
    icon: UserX,
    label: "Intrusion",
    color: "text-orange-600",
  },
  violence: {
    icon: AlertTriangle,
    label: "Violence",
    color: "text-orange-600",
  },
  crash: {
    icon: Car,
    label: "Crash",
    color: "text-orange-600",
  },
  robbery: {
    icon: AlertTriangle,
    label: "Robbery",
    color: "text-orange-600",
  },
  stranger: {
    icon: UserX,
    label: "Stranger",
    color: "text-orange-600",
  },
  audio_anomaly: {
    icon: Volume2,
    label: "Audio anomaly",
    color: "text-blue-600",
  },
  scream: {
    icon: Volume2,
    label: "Scream",
    color: "text-red-600",
  },
  gunshot: {
    icon: Volume2,
    label: "Gunshot",
    color: "text-red-600",
  },
  glass_break: {
    icon: Volume2,
    label: "Glass break",
    color: "text-orange-600",
  },
  explosion: {
    icon: Volume2,
    label: "Explosion",
    color: "text-red-600",
  },
  multimodal_anomaly: {
    icon: ShieldAlert,
    label: "Audio-video anomaly",
    color: "text-purple-600",
  },
  other: {
    icon: HelpCircle,
    label: "Alert",
    color: "text-muted-foreground",
  },
};

const defaultConfig = {
  icon: HelpCircle,
  label: "Alert",
  color: "text-muted-foreground",
};

export default function AlertCard({ type, location, time, entity, confidence, sourceLabel, modality, modalities, hasAudioEvidence, hasVideoEvidence, onClick }: AlertCardProps) {
  const config = alertConfig[type] ?? alertConfig[type.toLowerCase()] ?? defaultConfig;
  const Icon = config.icon;

  return (
    <Card 
      className={`p-4 cursor-pointer hover-elevate ${onClick ? 'active-elevate-2' : ''}`}
      onClick={onClick}
      data-testid={`card-alert-${type}`}
    >
      <div className="flex items-start gap-3">
        <div className={`${config.color} mt-1`}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="font-semibold text-sm">{config.label} detected</h4>
          <p className="text-sm text-muted-foreground mt-1">
            {location} — {time}
            {confidence && ` — Confidence ${confidence}%`}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {sourceLabel && (
              <Badge variant={sourceLabel.toLowerCase() === "webcam" ? "secondary" : "outline"} className="text-[10px] px-1.5 py-0">
                {sourceLabel}
              </Badge>
            )}
            {modality === "fusion" && (
              <Badge variant="default" className="text-[10px] px-1.5 py-0 bg-purple-100 text-purple-800 border-purple-200">
                Fusion
              </Badge>
            )}
            {modalities?.includes("audio") && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-blue-700 border-blue-200 bg-blue-50">
                Audio
              </Badge>
            )}
            {modalities?.includes("video") && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-emerald-700 border-emerald-200 bg-emerald-50">
                Video
              </Badge>
            )}
          </div>
          {entity && (
            <p className="text-sm mt-1">
              <span className="text-muted-foreground">Entity:</span>{" "}
              <span className={entity.includes("Unknown") ? "text-orange-600 font-medium" : ""}>{entity}</span>
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
