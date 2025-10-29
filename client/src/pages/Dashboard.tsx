import { useState } from "react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import CameraFeed from "@/components/CameraFeed";
import AlertCard from "@/components/AlertCard";
import StatsCard from "@/components/StatsCard";
import { Maximize2, Pause, Play, Activity, Clock, TrendingUp } from "lucide-react";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts";

import frontDoorImg from '@assets/generated_images/Front_door_camera_view_eee34996.png';
import livingRoomImg from '@assets/generated_images/Living_room_camera_view_c398b56c.png';
import garageImg from '@assets/generated_images/Garage_camera_view_63ee9c11.png';
import backyardImg from '@assets/generated_images/Backyard_camera_view_1f8a55c4.png';

const pieData = [
  { name: 'Fire', value: 15, color: 'hsl(var(--destructive))' },
  { name: 'Intrusion', value: 35, color: 'hsl(var(--chart-2))' },
  { name: 'Violence', value: 25, color: 'hsl(var(--chart-3))' },
  { name: 'Crash', value: 25, color: 'hsl(var(--chart-4))' },
];

const latencyData = [
  { time: '00:00', latency: 45 },
  { time: '04:00', latency: 52 },
  { time: '08:00', latency: 48 },
  { time: '12:00', latency: 55 },
  { time: '16:00', latency: 50 },
  { time: '20:00', latency: 47 },
];

export default function Dashboard() {
  const [, setLocation] = useLocation();
  const [isPaused, setIsPaused] = useState(false);

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

  return (
    <div className="p-6 space-y-6">
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
            <h2 className="text-lg font-semibold mb-4">Active Alerts</h2>
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
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    outerRadius={80}
                    fill="#8884d8"
                    dataKey="value"
                  >
                    {pieData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                  <Legend />
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </Card>
          </div>
        </div>

        <div className="lg:col-span-3 space-y-4">
          <div>
            <h2 className="text-lg font-semibold mb-4">System Health</h2>
            
            <Card className="p-4 mb-4">
              <h3 className="text-sm font-semibold mb-3">Latency (ms)</h3>
              <ResponsiveContainer width="100%" height={120}>
                <LineChart data={latencyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip />
                  <Line type="monotone" dataKey="latency" stroke="hsl(var(--primary))" strokeWidth={2} />
                </LineChart>
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
