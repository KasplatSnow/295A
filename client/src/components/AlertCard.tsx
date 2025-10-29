import { Flame, UserX, AlertTriangle, Car } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AlertCardProps {
  type: "fire" | "intrusion" | "violence" | "crash";
  location: string;
  time: string;
  entity?: string;
  confidence?: number;
  onClick?: () => void;
}

const alertConfig = {
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
};

export default function AlertCard({ type, location, time, entity, confidence, onClick }: AlertCardProps) {
  const config = alertConfig[type];
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
