import StatsCard from '../StatsCard';
import { Activity, TrendingUp, Shield } from 'lucide-react';

export default function StatsCardExample() {
  return (
    <div className="grid grid-cols-3 gap-4 w-full max-w-3xl">
      <StatsCard title="Today" value="3" icon={Activity} description="Active incidents" />
      <StatsCard title="This Week" value="12" icon={TrendingUp} trend={{ value: "8%", isPositive: false }} />
      <StatsCard title="Detection Accuracy" value="91%" icon={Shield} />
    </div>
  );
}
