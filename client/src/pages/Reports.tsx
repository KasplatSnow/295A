import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Download, FileText, Calendar } from "lucide-react";
import { BarChart, Bar, PieChart, Pie, Cell, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";

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
  { name: 'Fire', value: 15, color: 'hsl(var(--destructive))' },
  { name: 'Intrusion', value: 35, color: 'hsl(var(--chart-2))' },
  { name: 'Violence', value: 25, color: 'hsl(var(--chart-3))' },
  { name: 'Crash', value: 25, color: 'hsl(var(--chart-4))' },
];

const responseTrends = [
  { month: 'Jan', time: 45 },
  { month: 'Feb', time: 38 },
  { month: 'Mar', time: 42 },
  { month: 'Apr', time: 35 },
  { month: 'May', time: 32 },
  { month: 'Jun', time: 28 },
];

export default function Reports() {
  const [dateRange, setDateRange] = useState("last-7-days");
  const [incidentType, setIncidentType] = useState("all");

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
            <BarChart data={incidentsPerDay}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="day" stroke="hsl(var(--muted-foreground))" />
              <YAxis stroke="hsl(var(--muted-foreground))" />
              <Tooltip />
              <Bar dataKey="incidents" fill="hsl(var(--primary))" />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card className="p-6">
          <h2 className="text-lg font-semibold mb-4">Incident Breakdown by Type</h2>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={incidentBreakdown}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={(entry) => `${entry.name}: ${entry.value}%`}
                outerRadius={100}
                fill="#8884d8"
                dataKey="value"
              >
                {incidentBreakdown.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">Response Time Trends</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={responseTrends}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
            <XAxis dataKey="month" stroke="hsl(var(--muted-foreground))" />
            <YAxis stroke="hsl(var(--muted-foreground))" label={{ value: 'Minutes', angle: -90, position: 'insideLeft' }} />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="time" stroke="hsl(var(--chart-3))" strokeWidth={2} name="Avg Response Time" />
          </LineChart>
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
