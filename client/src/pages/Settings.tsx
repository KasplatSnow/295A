import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function Settings() {
  const [profile, setProfile] = useState({
    fullName: "John Doe",
    email: "john.doe@example.com",
    currentPassword: "",
    newPassword: "",
  });

  const [notifications, setNotifications] = useState({
    email: true,
    push: true,
    sms: false,
  });

  const [preferences, setPreferences] = useState({
    alertSensitivity: "medium",
    dataRetention: "60",
    audioDetection: true,
  });

  const handleSaveProfile = () => {
    console.log("Saving profile:", profile);
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Tabs defaultValue="profile" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="profile" data-testid="tab-profile">Profile</TabsTrigger>
          <TabsTrigger value="notifications" data-testid="tab-notifications">Notifications</TabsTrigger>
          <TabsTrigger value="system" data-testid="tab-system">System Preferences</TabsTrigger>
        </TabsList>

        <TabsContent value="profile" className="space-y-4 mt-6">
          <Card className="p-6">
            <h2 className="text-lg font-semibold mb-4">Profile Information</h2>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="fullName">Full Name</Label>
                <Input
                  id="fullName"
                  value={profile.fullName}
                  onChange={(e) => setProfile({ ...profile, fullName: e.target.value })}
                  data-testid="input-full-name"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={profile.email}
                  onChange={(e) => setProfile({ ...profile, email: e.target.value })}
                  data-testid="input-email"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="currentPassword">Current Password</Label>
                <Input
                  id="currentPassword"
                  type="password"
                  value={profile.currentPassword}
                  onChange={(e) => setProfile({ ...profile, currentPassword: e.target.value })}
                  placeholder="Enter current password"
                  data-testid="input-current-password"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="newPassword">New Password</Label>
                <Input
                  id="newPassword"
                  type="password"
                  value={profile.newPassword}
                  onChange={(e) => setProfile({ ...profile, newPassword: e.target.value })}
                  placeholder="Enter new password"
                  data-testid="input-new-password"
                />
              </div>
              <Button onClick={handleSaveProfile} data-testid="button-save-profile">
                Save Changes
              </Button>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="notifications" className="space-y-4 mt-6">
          <Card className="p-6">
            <h2 className="text-lg font-semibold mb-4">Notification Preferences</h2>
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <Label htmlFor="email-alerts" className="text-base">Email Alerts</Label>
                  <p className="text-sm text-muted-foreground">Receive alerts via email</p>
                </div>
                <Switch
                  id="email-alerts"
                  checked={notifications.email}
                  onCheckedChange={(checked) => setNotifications({ ...notifications, email: checked })}
                  data-testid="toggle-email"
                />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <Label htmlFor="push-alerts" className="text-base">Push Notifications</Label>
                  <p className="text-sm text-muted-foreground">Receive push notifications on your device</p>
                </div>
                <Switch
                  id="push-alerts"
                  checked={notifications.push}
                  onCheckedChange={(checked) => setNotifications({ ...notifications, push: checked })}
                  data-testid="toggle-push"
                />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <Label htmlFor="sms-alerts" className="text-base">SMS Alerts</Label>
                  <p className="text-sm text-muted-foreground">Receive alerts via text message</p>
                </div>
                <Switch
                  id="sms-alerts"
                  checked={notifications.sms}
                  onCheckedChange={(checked) => setNotifications({ ...notifications, sms: checked })}
                  data-testid="toggle-sms"
                />
              </div>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="system" className="space-y-4 mt-6">
          <Card className="p-6">
            <h2 className="text-lg font-semibold mb-4">System Preferences</h2>
            <div className="space-y-6">
              <div className="space-y-2">
                <Label htmlFor="sensitivity">Alert Sensitivity</Label>
                <Select value={preferences.alertSensitivity} onValueChange={(value) => setPreferences({ ...preferences, alertSensitivity: value })}>
                  <SelectTrigger id="sensitivity" data-testid="select-sensitivity">
                    <SelectValue placeholder="Select sensitivity" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">Low</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="retention">Data Retention</Label>
                <Select value={preferences.dataRetention} onValueChange={(value) => setPreferences({ ...preferences, dataRetention: value })}>
                  <SelectTrigger id="retention" data-testid="select-retention">
                    <SelectValue placeholder="Select retention period" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30 days</SelectItem>
                    <SelectItem value="60">60 days</SelectItem>
                    <SelectItem value="90">90 days</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <Label htmlFor="audio-detection" className="text-base">Enable Audio Detection</Label>
                  <p className="text-sm text-muted-foreground">Detect anomalies using audio analysis</p>
                </div>
                <Switch
                  id="audio-detection"
                  checked={preferences.audioDetection}
                  onCheckedChange={(checked) => setPreferences({ ...preferences, audioDetection: checked })}
                  data-testid="toggle-audio"
                />
              </div>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
