import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/auth/AuthProvider";
import { api } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { BellRing, Volume2 } from "lucide-react";
import { VZ_SETTINGS_KEYS } from "@/lib/audio";

interface ProfileData {
  id: number;
  user: string;
  username: string;
  email: string;
  bio: string;
  notify_email: boolean;
  notify_push: boolean;
  notify_sms: boolean;
  instant_notification_levels: string[];
  alert_sensitivity: string;
  data_retention_days: number;
  audio_detection: boolean;
  blur_faces: boolean;
  consent_required: boolean;
}

interface NotificationSettingsData {
  id?: number;
  email_enabled: boolean;
  push_enabled: boolean;
  email_recipients: string[];
  fcm_tokens: string[];
  severity_threshold: "high" | "medium" | "all";
  instant_notification_levels: string[];
  available_instant_notification_levels: Array<{ value: string; label: string }>;
}

const DEFAULT_LEVEL_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "severe", label: "Severe" },
  { value: "moderate", label: "Moderate" },
  { value: "low", label: "Low" },
  { value: "info", label: "Info" },
];

export default function Settings() {
  const { user, atLeast } = useAuth();
  const { toast } = useToast();
  const canManage = atLeast("member");

  const profileQ = useQuery({
    queryKey: ["profile-me"],
    queryFn: async () => {
      const { data } = await api.get("/profile/me/");
      return data as ProfileData;
    },
    retry: false,
  });

  const notificationSettingsQ = useQuery({
    queryKey: ["notification-settings"],
    queryFn: async () => {
      const { data } = await api.get("/notifications/settings/");
      return data as NotificationSettingsData;
    },
    retry: false,
  });

  const saveProfileMut = useMutation({
    mutationFn: async (patch: Partial<ProfileData>) => {
      const { data } = await api.patch("/profile/me/", patch);
      return data;
    },
    onSuccess: () => {
      toast({ title: "Settings saved" });
      queryClient.invalidateQueries({ queryKey: ["profile-me"] });
      queryClient.invalidateQueries({ queryKey: ["notification-settings"] });
    },
    onError: () => toast({ title: "Failed to save", variant: "destructive" }),
  });

  const saveNotificationSettingsMut = useMutation({
    mutationFn: async (patch: Partial<NotificationSettingsData>) => {
      const { data } = await api.patch("/notifications/settings/", patch);
      return data as NotificationSettingsData;
    },
    onSuccess: () => {
      toast({ title: "Notification preferences saved" });
      queryClient.invalidateQueries({ queryKey: ["notification-settings"] });
      queryClient.invalidateQueries({ queryKey: ["profile-me"] });
    },
    onError: () => toast({ title: "Failed to save notifications", variant: "destructive" }),
  });

  const p = profileQ.data;
  const tenantNotificationSettings = notificationSettingsQ.data;

  const [profile, setProfile] = useState({
    fullName: user?.username || "",
    email: user?.email || "",
    bio: "",
  });

  const [notifications, setNotifications] = useState({
    email: true,
    push: true,
    sms: false,
    sound: localStorage.getItem(VZ_SETTINGS_KEYS.NOTIFY_SOUND) !== "false",
  });

  const [preferences, setPreferences] = useState({
    alertSensitivity: "medium",
    dataRetention: "60",
    audioDetection: true,
    blurFaces: true,
    consentRequired: true,
  });

  const [instantLevels, setInstantLevels] = useState<string[]>(["critical", "severe", "moderate"]);
  const [tenantNotificationChannel, setTenantNotificationChannel] = useState({
    emailEnabled: false,
    pushEnabled: false,
    severityThreshold: "high" as "high" | "medium" | "all",
    emailRecipients: "",
  });

  useEffect(() => {
    if (p) {
      setProfile({
        fullName: p.username || user?.username || "",
        email: p.email || user?.email || "",
        bio: p.bio || "",
      });
      setNotifications((prev) => ({ ...prev, email: p.notify_email, push: p.notify_push, sms: p.notify_sms }));
      setPreferences({
        alertSensitivity: p.alert_sensitivity,
        dataRetention: String(p.data_retention_days),
        audioDetection: p.audio_detection,
        blurFaces: p.blur_faces,
        consentRequired: p.consent_required,
      });
      setInstantLevels(p.instant_notification_levels?.length ? p.instant_notification_levels : ["critical", "severe", "moderate"]);
    }
  }, [p, user]);

  useEffect(() => {
    if (tenantNotificationSettings) {
      setTenantNotificationChannel({
        emailEnabled: Boolean(tenantNotificationSettings.email_enabled),
        pushEnabled: Boolean(tenantNotificationSettings.push_enabled),
        severityThreshold: tenantNotificationSettings.severity_threshold || "high",
        emailRecipients: Array.isArray(tenantNotificationSettings.email_recipients)
          ? tenantNotificationSettings.email_recipients.join(", ")
          : "",
      });
      if (tenantNotificationSettings.instant_notification_levels?.length) {
        setInstantLevels(tenantNotificationSettings.instant_notification_levels);
      }
    }
  }, [tenantNotificationSettings]);

  const levelOptions = tenantNotificationSettings?.available_instant_notification_levels?.length
    ? tenantNotificationSettings.available_instant_notification_levels
    : DEFAULT_LEVEL_OPTIONS;

  const handleSaveProfile = () => {
    if (!canManage) return;
    saveProfileMut.mutate({ bio: profile.bio });
  };

  const handleSaveNotifications = () => {
    localStorage.setItem(VZ_SETTINGS_KEYS.NOTIFY_SOUND, String(notifications.sound));
    if (!canManage) {
      toast({ title: "Sound preference saved locally" });
      return;
    }
    saveProfileMut.mutate({
      notify_email: notifications.email,
      notify_push: notifications.push,
      notify_sms: notifications.sms,
    });
  };

  const handleSaveInstantNotifications = () => {
    saveNotificationSettingsMut.mutate({
      instant_notification_levels: instantLevels,
      ...(canManage
        ? {
            email_enabled: tenantNotificationChannel.emailEnabled,
            push_enabled: tenantNotificationChannel.pushEnabled,
            severity_threshold: tenantNotificationChannel.severityThreshold,
            email_recipients: tenantNotificationChannel.emailRecipients
              .split(",")
              .map((entry) => entry.trim())
              .filter(Boolean),
          }
        : {}),
    });
  };

  const handleSavePreferences = () => {
    if (!canManage) return;
    saveProfileMut.mutate({
      alert_sensitivity: preferences.alertSensitivity,
      data_retention_days: parseInt(preferences.dataRetention, 10),
      audio_detection: preferences.audioDetection,
      blur_faces: preferences.blurFaces,
      consent_required: preferences.consentRequired,
    });
  };

  const toggleInstantLevel = (value: string, checked: boolean) => {
    setInstantLevels((prev) => {
      const next = checked ? [...prev, value] : prev.filter((item) => item !== value);
      return Array.from(new Set(next));
    });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Personalize your account, incident alerts, and community preferences.
        </p>
      </div>
      {(profileQ.isLoading || notificationSettingsQ.isLoading) && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      <Tabs defaultValue="profile" className="w-full">
        <TabsList className={`grid w-full ${canManage ? "grid-cols-4" : "grid-cols-2"}`}>
          <TabsTrigger value="profile" data-testid="tab-profile">Profile</TabsTrigger>
          <TabsTrigger value="notifications" data-testid="tab-notifications">Notifications</TabsTrigger>
          {canManage && <TabsTrigger value="privacy" data-testid="tab-privacy">Privacy & Retention</TabsTrigger>}
          {canManage && <TabsTrigger value="system" data-testid="tab-system">System Preferences</TabsTrigger>}
        </TabsList>

        <TabsContent value="profile" className="mt-6 space-y-4">
          <Card className="p-6">
            <h2 className="mb-4 text-lg font-semibold">Profile Information</h2>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="fullName">Username</Label>
                <Input id="fullName" value={profile.fullName} disabled data-testid="input-full-name" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" value={profile.email} disabled data-testid="input-email" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="bio">Bio</Label>
                <Input
                  id="bio"
                  value={profile.bio}
                  disabled={!canManage}
                  onChange={(e) => setProfile({ ...profile, bio: e.target.value })}
                  placeholder="A short bio…"
                />
              </div>
              <Button onClick={handleSaveProfile} disabled={saveProfileMut.isPending || !canManage} data-testid="button-save-profile">
                {saveProfileMut.isPending ? "Saving…" : "Save Changes"}
              </Button>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="notifications" className="mt-6 space-y-4">
          <Card className="p-6">
            <div className="mb-4 flex items-center gap-2">
              <BellRing className="h-5 w-5 text-primary" />
              <div>
                <h2 className="text-lg font-semibold">Notification Preferences</h2>
                <p className="text-sm text-muted-foreground">
                  Choose which incident severities should appear immediately in the top-bar notification bell.
                </p>
              </div>
            </div>
            <div className="space-y-6">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border border-border bg-background/60 p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <div>
                      <Label htmlFor="email-alerts" className="text-base">Email Alerts</Label>
                      <p className="text-sm text-muted-foreground">Receive alerts via email</p>
                    </div>
                    <Switch
                      id="email-alerts"
                      checked={notifications.email}
                      disabled={!canManage}
                      onCheckedChange={(checked) => setNotifications({ ...notifications, email: checked })}
                      data-testid="toggle-email"
                    />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <Label htmlFor="push-alerts" className="text-base">Push Notifications</Label>
                      <p className="text-sm text-muted-foreground">Enable device notifications for incident updates</p>
                    </div>
                    <Switch
                      id="push-alerts"
                      checked={notifications.push}
                      disabled={!canManage}
                      onCheckedChange={(checked) => setNotifications({ ...notifications, push: checked })}
                      data-testid="toggle-push"
                    />
                  </div>
                  <div className="mt-4 flex items-center justify-between">
                    <div>
                      <Label htmlFor="sms-alerts" className="text-base">SMS Alerts</Label>
                      <p className="text-sm text-muted-foreground">Receive alerts via text message</p>
                    </div>
                    <Switch
                      id="sms-alerts"
                      checked={notifications.sms}
                      disabled={!canManage}
                      onCheckedChange={(checked) => setNotifications({ ...notifications, sms: checked })}
                      data-testid="toggle-sms"
                    />
                  </div>
                  <div className="mt-4 flex items-center justify-between border-t border-border pt-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <Volume2 className="h-4 w-4 text-primary" />
                        <Label htmlFor="sound-alerts" className="text-base">Sound Alerts</Label>
                      </div>
                      <p className="text-sm text-muted-foreground">Play a chime when new notifications arrive</p>
                    </div>
                    <Switch
                      id="sound-alerts"
                      checked={notifications.sound}
                      onCheckedChange={(checked) => setNotifications({ ...notifications, sound: checked })}
                      data-testid="toggle-sound"
                    />
                  </div>
                  <Button className="mt-4 w-full" onClick={handleSaveNotifications} disabled={saveProfileMut.isPending}>
                    Save notification preferences
                  </Button>
                </div>

                <div className="rounded-xl border border-border bg-background/60 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">Immediate incident severities</h3>
                      <p className="text-sm text-muted-foreground">
                        Controls which incident severities appear instantly in the notification bell for your account.
                      </p>
                    </div>
                    <Badge variant="secondary">{instantLevels.length} selected</Badge>
                  </div>
                  <ScrollArea className="h-48 rounded-lg border border-dashed border-border px-3 py-2">
                    <div className="space-y-3">
                      {levelOptions.map((option) => {
                        const checked = instantLevels.includes(option.value);
                        return (
                          <label key={option.value} className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-accent/60">
                            <Checkbox
                              checked={checked}
                              onCheckedChange={(value) => toggleInstantLevel(option.value, Boolean(value))}
                            />
                            <div className="space-y-1">
                              <div className="text-sm font-medium">{option.label}</div>
                              <p className="text-xs text-muted-foreground">
                                {option.value === "critical" && "Highest-priority incidents that require immediate action."}
                                {option.value === "severe" && "High-risk incidents that should still notify instantly."}
                                {option.value === "moderate" && "Meaningful incidents that matter but are less urgent."}
                                {option.value === "low" && "Lower-risk incidents and routine detections."}
                                {option.value === "info" && "Informational detections and passive updates."}
                              </p>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </ScrollArea>
                  {!canManage && (
                    <p className="mt-3 text-xs text-muted-foreground">
                      These severity filters are personal to your account and do not change other members’ alerts.
                    </p>
                  )}
                </div>
              </div>

              {canManage && (
                <div className="rounded-xl border border-border bg-background/60 p-4">
                  <h3 className="mb-4 text-sm font-semibold">Community alert routing</h3>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div>
                          <Label className="text-base">Tenant email broadcast</Label>
                          <p className="text-sm text-muted-foreground">Send qualifying incidents to shared email recipients</p>
                        </div>
                        <Switch
                          checked={tenantNotificationChannel.emailEnabled}
                          onCheckedChange={(checked) => setTenantNotificationChannel((prev) => ({ ...prev, emailEnabled: checked }))}
                        />
                      </div>
                      <div className="flex items-center justify-between">
                        <div>
                          <Label className="text-base">Tenant push broadcast</Label>
                          <p className="text-sm text-muted-foreground">Enable push notifications for registered devices</p>
                        </div>
                        <Switch
                          checked={tenantNotificationChannel.pushEnabled}
                          onCheckedChange={(checked) => setTenantNotificationChannel((prev) => ({ ...prev, pushEnabled: checked }))}
                        />
                      </div>
                    </div>
                    <div className="space-y-3">
                      <div className="space-y-2">
                        <Label htmlFor="email-recipients">Email Recipients</Label>
                        <Input
                          id="email-recipients"
                          value={tenantNotificationChannel.emailRecipients}
                          onChange={(e) => setTenantNotificationChannel((prev) => ({ ...prev, emailRecipients: e.target.value }))}
                          placeholder="ops@example.com, admin@example.com"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="severity-threshold">Delivery threshold</Label>
                        <Select
                          value={tenantNotificationChannel.severityThreshold}
                          onValueChange={(value: "high" | "medium" | "all") =>
                            setTenantNotificationChannel((prev) => ({ ...prev, severityThreshold: value }))
                          }
                        >
                          <SelectTrigger id="severity-threshold">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="high">High only</SelectItem>
                            <SelectItem value="medium">Moderate and above</SelectItem>
                            <SelectItem value="all">All severities</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <Button onClick={handleSaveInstantNotifications} disabled={saveNotificationSettingsMut.isPending}>
                {saveNotificationSettingsMut.isPending ? "Saving…" : "Save notification settings"}
              </Button>
            </div>
          </Card>
        </TabsContent>

        {canManage && (
          <TabsContent value="privacy" className="mt-6 space-y-4">
            <Card className="p-6">
              <h2 className="mb-4 text-lg font-semibold">Privacy & Retention</h2>
              <div className="space-y-6">
                <div className="space-y-2">
                  <Label htmlFor="retention-period">Data Retention Period</Label>
                  <Select
                    value={preferences.dataRetention}
                    disabled={!canManage}
                    onValueChange={(v) => setPreferences({ ...preferences, dataRetention: v })}
                  >
                    <SelectTrigger id="retention-period" data-testid="select-retention-period">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="30">30 days</SelectItem>
                      <SelectItem value="60">60 days</SelectItem>
                      <SelectItem value="90">90 days</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-sm text-muted-foreground">Footage and incidents older than this will be automatically deleted.</p>
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <Label className="text-base">Blur Faces for Shared Video</Label>
                    <p className="text-sm text-muted-foreground">Automatically blur faces in community-shared feeds</p>
                  </div>
                  <Switch
                    checked={preferences.blurFaces}
                    disabled={!canManage}
                    onCheckedChange={(c) => setPreferences({ ...preferences, blurFaces: c })}
                    data-testid="toggle-blur-faces"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <Label className="text-base">Entity Consent Management</Label>
                    <p className="text-sm text-muted-foreground">Require consent before storing entity images</p>
                  </div>
                  <Switch
                    checked={preferences.consentRequired}
                    disabled={!canManage}
                    onCheckedChange={(c) => setPreferences({ ...preferences, consentRequired: c })}
                    data-testid="toggle-consent"
                  />
                </div>
                <Button onClick={handleSavePreferences} disabled={saveProfileMut.isPending || !canManage}>Save Privacy Settings</Button>
              </div>
            </Card>
          </TabsContent>
        )}

        {canManage && (
          <TabsContent value="system" className="mt-6 space-y-4">
            <Card className="p-6">
              <h2 className="mb-4 text-lg font-semibold">System Preferences</h2>
              <div className="space-y-6">
                <div className="space-y-2">
                  <Label htmlFor="sensitivity">Alert Sensitivity</Label>
                  <Select
                    value={preferences.alertSensitivity}
                    disabled={!canManage}
                    onValueChange={(v) => setPreferences({ ...preferences, alertSensitivity: v })}
                  >
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
                <div className="flex items-center justify-between">
                  <div>
                    <Label htmlFor="audio-detection" className="text-base">Audio anomaly alerts</Label>
                    <p className="text-sm text-muted-foreground">Receive alerts for screams, gunshots, glass break, alarms, and other suspicious sounds.</p>
                  </div>
                  <Switch
                    id="audio-detection"
                    checked={preferences.audioDetection}
                    disabled={!canManage}
                    onCheckedChange={(c) => setPreferences({ ...preferences, audioDetection: c })}
                    data-testid="toggle-audio"
                  />
                </div>
                <Button onClick={handleSavePreferences} disabled={saveProfileMut.isPending || !canManage}>Save System Preferences</Button>
              </div>
            </Card>
          </TabsContent>
        )}
      </Tabs>
      {!canManage && (
        <p className="text-sm text-muted-foreground">Viewer role has read-only access to settings.</p>
      )}
    </div>
  );
}
