import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Download, FileText, Calendar } from "lucide-react";
import { BarChart, Bar, PieChart, Pie, Cell, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";

const incidentsPerDay = [
  { day: 'Mon', incidents: 4 },
  { day: 'Tue', incidents: 7 },
  { day: 'Wed', incidents: 3 },
  { day: 'Thu', incidents: 8 },
  { day: 'Fri', incidents: 5 },
  { day: 'Sat', incidents: 2 },
  { day: 'Sun', incidents: 3 },
];

const incidentBreakdown = [
  { name: 'Fire', value: 15, color: '#EF4444', percentage: '15%' },
  { name: 'Intrusion', value: 35, color: '#F59E0B', percentage: '35%' },
  { name: 'Violence', value: 25, color: '#10B981', percentage: '25%' },
  { name: 'Crash', value: 25, color: '#8B5CF6', percentage: '25%' },
];

const responseTrends = [
  { month: 'Jan', time: 45 },
  { month: 'Feb', time: 38 },
  { month: 'Mar', time: 42 },
  { month: 'Apr', time: 35 },
  { month: 'May', time: 32 },
  { month: 'Jun', time: 28 },
];

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-card border border-border rounded-lg shadow-lg p-3">
        <p className="text-sm font-medium mb-1">{label}</p>
        <p className="text-sm text-primary font-semibold">
          {payload[0].value} {payload[0].dataKey === 'time' ? 'min' : 'incidents'}
        </p>
      </div>
    );
  }
  return null;
};

const CustomPieLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percentage }: any) => {
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

export default function Reports() {
  const [dateRange, setDateRange] = useState("last-7-days");
  const [incidentType, setIncidentType] = useState("all");
  const [entityType, setEntityType] = useState("all");
  const [zoneFilter, setZoneFilter] = useState("all");

  const handleDownloadCSV = () => {
    console.log("Downloading CSV");
  };

  const handleExportPDF = () => {
    console.log("Exporting PDF");
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Analytics & Reports</h1>

      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-muted-foreground" />
            <Select value={dateRange} onValueChange={setDateRange}>
              <SelectTrigger className="w-[180px]" data-testid="select-date-range">
                <SelectValue placeholder="Select date range" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="last-7-days">Last 7 days</SelectItem>
                <SelectItem value="last-30-days">Last 30 days</SelectItem>
                <SelectItem value="last-90-days">Last 90 days</SelectItem>
                <SelectItem value="custom">Custom range</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Select value={incidentType} onValueChange={setIncidentType}>
              <SelectTrigger className="w-[180px]" data-testid="select-incident-type">
                <SelectValue placeholder="Incident type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                <SelectItem value="fire">Fire</SelectItem>
                <SelectItem value="intrusion">Intrusion</SelectItem>
                <SelectItem value="violence">Violence</SelectItem>
                <SelectItem value="crash">Crash</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Select value={entityType} onValueChange={setEntityType}>
              <SelectTrigger className="w-[180px]" data-testid="select-entity-type">
                <SelectValue placeholder="Entity type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Entities</SelectItem>
                <SelectItem value="person">Person</SelectItem>
                <SelectItem value="pet">Pet</SelectItem>
                <SelectItem value="vehicle">Vehicle</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
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

          <div className="ml-auto flex gap-2">
            <Button variant="outline" onClick={handleDownloadCSV} data-testid="button-download-csv">
              <Download className="w-4 h-4 mr-2" />
              Download CSV
            </Button>
            <Button variant="outline" onClick={handleExportPDF} data-testid="button-export-pdf">
              <FileText className="w-4 h-4 mr-2" />
              Export PDF
            </Button>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Incidents per Day</h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={incidentsPerDay} barSize={50}>
              <defs>
                <linearGradient id="barGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.95} />
                  <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0.6} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
              <XAxis 
                dataKey="day" 
                stroke="hsl(var(--muted-foreground))" 
                tickLine={false}
                axisLine={false}
              />
              <YAxis 
                stroke="hsl(var(--muted-foreground))" 
                tickLine={false}
                axisLine={false}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'hsl(var(--muted))', opacity: 0.1 }} />
              <Bar 
                dataKey="incidents" 
                fill="url(#barGradient)" 
                radius={[8, 8, 0, 0]}
                animationDuration={1000}
              />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Incident Breakdown by Type</h2>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <defs>
                {incidentBreakdown.map((entry, index) => (
                  <linearGradient key={`gradient-${index}`} id={`gradient-${entry.name}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={entry.color} stopOpacity={0.95} />
                    <stop offset="100%" stopColor={entry.color} stopOpacity={0.75} />
                  </linearGradient>
                ))}
              </defs>
              <Pie
                data={incidentBreakdown}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={(props) => <CustomPieLabel {...props} />}
                outerRadius={110}
                innerRadius={60}
                fill="#8884d8"
                dataKey="value"
                paddingAngle={3}
                animationBegin={0}
                animationDuration={800}
              >
                {incidentBreakdown.map((entry, index) => (
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
                formatter={(value) => <span className="text-sm">{value}</span>}
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

      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">Response Time Trends</h2>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={responseTrends}>
            <defs>
              <linearGradient id="colorResponse" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10B981" stopOpacity={0.4}/>
                <stop offset="95%" stopColor="#10B981" stopOpacity={0.05}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis 
              dataKey="month" 
              stroke="hsl(var(--muted-foreground))" 
              tickLine={false}
              axisLine={false}
            />
            <YAxis 
              stroke="hsl(var(--muted-foreground))" 
              label={{ value: 'Minutes', angle: -90, position: 'insideLeft', style: { fill: 'hsl(var(--muted-foreground))' } }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend 
              iconType="circle"
              formatter={(value) => <span className="text-sm">Avg Response Time</span>}
            />
            <Area 
              type="monotone" 
              dataKey="time" 
              stroke="#10B981" 
              strokeWidth={3}
              fill="url(#colorResponse)"
              name="Avg Response Time"
              animationDuration={1200}
              dot={{ fill: '#10B981', r: 4 }}
              activeDot={{ r: 6, stroke: '#10B981', strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card className="p-6 bg-muted/50">
        <h3 className="font-semibold mb-2">📊 Key Insights</h3>
        <p className="text-sm text-muted-foreground">
          Peak alerts occur between 6 PM and 9 PM. Fire alerts decreased 14% this week compared to last week. 
          Average response time has improved by 38% over the last 6 months.
        </p>
      </Card>
    </div>
  );
}
