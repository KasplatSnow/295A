import { Flame, UserX, AlertTriangle, Car } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AlertCardProps {
  type: "fire" | "intrusion" | "violence" | "crash";
  location: string;
  time: string;
  status?: "active" | "resolved";
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

export default function AlertCard({ type, location, time, status = "active", onClick }: AlertCardProps) {
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
          <div className="flex items-start justify-between gap-2">
            <h4 className="font-semibold text-sm">{config.label} detected</h4>
            {status && (
              <Badge variant={status === "active" ? "destructive" : "secondary"} className="shrink-0">
                {status}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">{location} at {time}</p>
        </div>
      </div>
    </Card>
  );
}
