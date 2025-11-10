// src/pages/ProfilePage.tsx
import { useState, useEffect } from "react";
import { api } from "../lib/api";
import {
  Container,
  Typography,
  Card,
  CardContent,
  Avatar,
  Button,
  Stack,
  CircularProgress,
} from "@mui/material";

export default function ProfilePage() {
  const [profile, setProfile] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get("/profile/").then((res) => setProfile(res.data[0]));
  }, []);

  const handleUpload = async () => {
    if (!file || !profile) return;
    setLoading(true);
    const formData = new FormData();
    formData.append("profile_pic", file);
    await api.patch(`/profile/${profile.id}/`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    setLoading(false);
    alert("Profile picture updated!");
  };

  if (!profile) return <CircularProgress />;

  return (
    <Container maxWidth="sm" sx={{ mt: 6 }}>
      <Card>
        <CardContent>
          <Stack alignItems="center" spacing={2}>
            <Typography variant="h5" gutterBottom>
              My Profile
            </Typography>

            <Avatar
              alt={profile.user}
              src={profile.profile_pic}
              sx={{ width: 120, height: 120 }}
            />

            <Typography variant="body1">Username: {profile.user}</Typography>
            <Typography variant="body2" color="text.secondary">
              Tenant: {profile.tenant || "Not assigned"}
            </Typography>

            <Button variant="outlined" component="label" sx={{ mt: 2 }}>
              Choose Picture
              <input
                type="file"
                hidden
                accept="image/*"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </Button>

            <Button
              variant="contained"
              onClick={handleUpload}
              disabled={loading || !file}
            >
              {loading ? "Uploading..." : "Upload"}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Container>
  );
}
