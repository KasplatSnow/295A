import { useState } from "react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import CameraFeed from "@/components/CameraFeed";
import AlertCard from "@/components/AlertCard";
import StatsCard from "@/components/StatsCard";
import { Maximize2, Pause, Play, Activity, Clock, TrendingUp, User, Dog, Car } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts";

import frontDoorImg from '@assets/generated_images/Front_door_camera_view_eee34996.png';
import livingRoomImg from '@assets/generated_images/Living_room_camera_view_c398b56c.png';
import garageImg from '@assets/generated_images/Garage_camera_view_63ee9c11.png';
import backyardImg from '@assets/generated_images/Backyard_camera_view_1f8a55c4.png';

const pieData = [
  { name: 'Fire', value: 15, color: '#EF4444', percentage: '15%' },
  { name: 'Intrusion', value: 35, color: '#F59E0B', percentage: '35%' },
  { name: 'Violence', value: 25, color: '#10B981', percentage: '25%' },
  { name: 'Crash', value: 25, color: '#8B5CF6', percentage: '25%' },
];

const latencyData = [
  { time: '00:00', latency: 45 },
  { time: '04:00', latency: 52 },
  { time: '08:00', latency: 48 },
  { time: '12:00', latency: 55 },
  { time: '16:00', latency: 50 },
  { time: '20:00', latency: 47 },
  { time: '24:00', latency: 43 },
];

const CustomTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-card border border-border rounded-lg shadow-lg p-3">
        <p className="text-sm font-medium">{payload[0].payload.time}</p>
        <p className="text-sm text-primary font-semibold">{payload[0].value}ms</p>
      </div>
    );
  }
  return null;
};

const CustomPieLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percentage, name }: any) => {
  const RADIAN = Math.PI / 180;
  const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
  const x = cx + radius * Math.cos(-midAngle * RADIAN);
  const y = cy + radius * Math.sin(-midAngle * RADIAN);

  return (
    <text 
      x={x} 
      y={y} 
      fill="white" 
      textAnchor={x > cx ? 'start' : 'end'} 
      dominantBaseline="central"
      className="text-xs font-semibold"
    >
      {percentage}
    </text>
  );
};

export default function Dashboard() {
  const [, setLocation] = useLocation();
  const [isPaused, setIsPaused] = useState(false);
  const [zoneFilter, setZoneFilter] = useState("all");

  const cameras = [
    { name: "Front Door", location: "Entrance", status: "active" as const, imageUrl: frontDoorImg },
    { name: "Living Room", location: "Interior", status: "active" as const, imageUrl: livingRoomImg },
    { name: "Garage", location: "Garage", status: "active" as const, imageUrl: garageImg },
    { name: "Backyard", location: "Outdoor", status: "active" as const, imageUrl: backyardImg },
  ];

  const alerts = [
    { id: 1, type: "fire" as const, location: "Kitchen", time: "2:31 PM", entity: "Unknown", confidence: 92 },
    { id: 2, type: "intrusion" as const, location: "Backyard", time: "1:12 AM", entity: "Unknown", confidence: 88 },
    { id: 3, type: "violence" as const, location: "Garage", time: "11:45 PM", entity: "Jason P. (Neighbor)", confidence: 85 },
  ];

  const knownEntities = [
    { name: "Dev", type: "person" as const },
    { name: "Harsh", type: "person" as const },
    { name: "Cameron", type: "person" as const },
    { name: "Bella", type: "pet" as const },
  ];

  const watchlist = [
    { name: "Unknown Male 01", type: "person" as const },
    { name: "Gray SUV", type: "vehicle" as const },
  ];

  const communityActivity = [
    { time: "10 min ago", action: "Jason added PorchCam to Shared zone" },
    { time: "1 hour ago", action: "Nina approved Neighborhood Watch invite" },
    { time: "2 hours ago", action: "Cameron shared Street Cam with All Members" },
  ];

  const getEntityIcon = (type: string) => {
    if (type === "person") return <User className="w-3 h-3" />;
    if (type === "pet") return <Dog className="w-3 h-3" />;
    return <Car className="w-3 h-3" />;
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex gap-3 flex-wrap">
        <Select value={zoneFilter} onValueChange={setZoneFilter}>
          <SelectTrigger className="w-[180px]" data-testid="select-zone-filter">
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

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-4 space-y-4">
          <div>
            <h2 className="text-lg font-semibold mb-4">Live Feeds</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-1 gap-3">
              {cameras.map((camera, idx) => (
                <CameraFeed
                  key={idx}
                  name={camera.name}
                  location={camera.location}
                  status={camera.status}
                  imageUrl={camera.imageUrl}
                  timestamp={new Date().toLocaleTimeString()}
                />
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" data-testid="button-fullscreen">
              <Maximize2 className="w-4 h-4 mr-2" />
              View Fullscreen
            </Button>
            <Button
              variant="outline"
              className="flex-1"
              onClick={() => setIsPaused(!isPaused)}
              data-testid="button-pause-stream"
            >
              {isPaused ? <Play className="w-4 h-4 mr-2" /> : <Pause className="w-4 h-4 mr-2" />}
              {isPaused ? "Resume" : "Pause"} Stream
            </Button>
          </div>
        </div>

        <div className="lg:col-span-5 space-y-6">
          <div>
            <h2 className="text-lg font-semibold mb-4">Recent Alerts</h2>
            <div className="space-y-3">
              {alerts.map((alert) => (
                <AlertCard
                  key={alert.id}
                  type={alert.type}
                  location={alert.location}
                  time={alert.time}
                  entity={alert.entity}
                  confidence={alert.confidence}
                  onClick={() => setLocation(`/incidents/${alert.id}`)}
                />
              ))}
            </div>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-4">Incident Summary</h2>
            <div className="grid grid-cols-3 gap-3">
              <StatsCard title="Today" value="3" icon={Activity} />
              <StatsCard title="Week" value="12" icon={Clock} />
              <StatsCard title="Month" value="45" icon={TrendingUp} />
            </div>

            <Card className="mt-4 p-4">
              <h3 className="text-sm font-semibold mb-2">Anomaly Type Breakdown</h3>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <defs>
                    {pieData.map((entry, index) => (
                      <linearGradient key={`gradient-${index}`} id={`gradient-${entry.name}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={entry.color} stopOpacity={0.95} />
                        <stop offset="100%" stopColor={entry.color} stopOpacity={0.75} />
                      </linearGradient>
                    ))}
                  </defs>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={(props) => <CustomPieLabel {...props} />}
                    outerRadius={85}
                    innerRadius={45}
                    fill="#8884d8"
                    dataKey="value"
                    paddingAngle={2}
                    animationBegin={0}
                    animationDuration={800}
                  >
                    {pieData.map((entry, index) => (
                      <Cell 
                        key={`cell-${index}`} 
                        fill={`url(#gradient-${entry.name})`}
                        stroke="hsl(var(--background))"
                        strokeWidth={2}
                      />
                    ))}
                  </Pie>
                  <Legend 
                    verticalAlign="bottom" 
                    height={36}
                    iconType="circle"
                    formatter={(value) => <span className="text-xs">{value}</span>}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (active && payload && payload.length) {
                        return (
                          <div className="bg-card border border-border rounded-lg shadow-lg p-3">
                            <p className="text-sm font-medium">{payload[0].name}</p>
                            <p className="text-sm text-primary font-semibold">{payload[0].value}%</p>
                          </div>
                        );
                      }
                      return null;
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </Card>
          </div>
        </div>

        <div className="lg:col-span-3 space-y-4">
          <div>
            <h2 className="text-lg font-semibold mb-4">Entities</h2>
            <Card className="p-4">
              <h3 className="text-sm font-semibold mb-3">Known Entities</h3>
              <div className="flex flex-wrap gap-2">
                {knownEntities.map((entity, idx) => (
                  <Badge key={idx} variant="secondary" className="gap-1" data-testid={`chip-entity-${idx}`}>
                    {getEntityIcon(entity.type)}
                    {entity.name}
                  </Badge>
                ))}
              </div>
            </Card>

            <Card className="p-4 mt-3">
              <h3 className="text-sm font-semibold mb-3">Watchlist</h3>
              <div className="flex flex-wrap gap-2">
                {watchlist.map((entity, idx) => (
                  <Badge key={idx} variant="destructive" className="gap-1" data-testid={`chip-watchlist-${idx}`}>
                    {getEntityIcon(entity.type)}
                    {entity.name}
                  </Badge>
                ))}
              </div>
            </Card>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-4">Community Activity</h2>
            <Card className="p-4">
              <div className="space-y-3">
                {communityActivity.map((activity, idx) => (
                  <div key={idx} className="text-sm">
                    <p className="text-muted-foreground text-xs mb-1">{activity.time}</p>
                    <p>{activity.action}</p>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div>
            <h2 className="text-lg font-semibold mb-4">System Health</h2>
            
            <Card className="p-4 mb-4">
              <h3 className="text-sm font-semibold mb-3">Latency (ms)</h3>
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={latencyData}>
                  <defs>
                    <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
                  <XAxis 
                    dataKey="time" 
                    tick={{ fontSize: 10 }} 
                    stroke="hsl(var(--muted-foreground))"
                    tickLine={false}
                  />
                  <YAxis 
                    tick={{ fontSize: 10 }} 
                    stroke="hsl(var(--muted-foreground))"
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area 
                    type="monotone" 
                    dataKey="latency" 
                    stroke="hsl(var(--primary))" 
                    strokeWidth={2.5}
                    fill="url(#colorLatency)"
                    animationDuration={1000}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </Card>

            <div className="space-y-3">
              <StatsCard
                title="Detection Accuracy"
                value="91%"
                description="Last 24 hours"
              />
              <StatsCard
                title="System Uptime"
                value="99.2%"
                description="Last 30 days"
              />
              <StatsCard
                title="Frames Processed"
                value="1.2M"
                description="Today"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
