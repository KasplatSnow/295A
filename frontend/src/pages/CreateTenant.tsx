// src/pages/CreateTenant.tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { getUserTenants } from "../lib/user";
import {
  Container, Paper, Typography, TextField, Button, Box, Stack, CircularProgress,
} from "@mui/material";
import useSnackbar from "../components/CustomSnackbar";

export default function CreateTenantPage() {
  const [tenantName, setTenantName] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const navigate = useNavigate();
  const { SnackbarComponent, showSnackbar } = useSnackbar();

  // 👇 prevents the auto-check from showing its own snackbar/redirect
  const suppressAutoCheckRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = localStorage.getItem("accessToken");
        if (!token) {
          if (!cancelled) {
            showSnackbar("Please log in to continue.", "info");
            navigate("/login");
          }
          return;
        }
        const tenants = await getUserTenants();
        if (cancelled) return;

        if (tenants.length > 0) {
          // Only show if we didn't just create one ourselves
          if (!suppressAutoCheckRef.current) {
            showSnackbar("You already have a community. Redirecting…", "info");
            setTimeout(() => navigate("/dashboard"), 900);
          }
          return;
        }
      } catch (e: any) {
        if (cancelled) return;
        if (e?.response?.status === 401) {
          showSnackbar("Session expired. Please log in again.", "warning");
          setTimeout(() => navigate("/login"), 900);
          return;
        }
        console.error(e);
        showSnackbar("Could not check your communities.", "error");
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => { cancelled = true; };
  }, [navigate, showSnackbar]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tenantName.trim()) {
      showSnackbar("Please enter a community name.", "warning");
      return;
    }
    try {
      setLoading(true);
      // tell the auto-check to keep quiet if it returns after this
      suppressAutoCheckRef.current = true;

      await api.post("/tenants/", { name: tenantName.trim() });

      // If you use a global Snackbar Provider, you can navigate immediately:
      // navigate("/dashboard");
      // Otherwise, give the local snackbar a moment to display:
      showSnackbar("Community created! You’re the owner.", "success");
      setTimeout(() => navigate("/dashboard"), 800);
    } catch (err: any) {
      console.error(err);
      suppressAutoCheckRef.current = false; // allow auto-check again on errors
      if (err?.response?.status === 401) {
        showSnackbar("Please log in to create a community.", "warning");
        setTimeout(() => navigate("/login"), 900);
      } else {
        const msg =
          err?.response?.data?.detail ||
          err?.response?.data?.name ||
          "Failed to create community.";
        showSnackbar(String(msg), "error");
      }
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <Container maxWidth="sm" sx={{ mt: 12, textAlign: "center" }}>
        <CircularProgress />
        {SnackbarComponent}
      </Container>
    );
  }

  return (
    <Container maxWidth="sm" sx={{ mt: 10 }}>
      <Paper elevation={3} sx={{ p: 4 }}>
        <Typography variant="h5" textAlign="center" gutterBottom>
          Create Your Community / Home
        </Typography>
        <Box component="form" onSubmit={handleCreate}>
          <Stack spacing={2}>
            <TextField
              label="Community Name"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              fullWidth
              required
            />
            <Button type="submit" variant="contained" fullWidth disabled={loading}>
              {loading ? "Creating..." : "Create Community"}
            </Button>
          </Stack>
        </Box>
        <Typography variant="body2" align="center" sx={{ mt: 2 }}>
          You’ll automatically become the <strong>Owner</strong>.
        </Typography>
      </Paper>
      {SnackbarComponent}
    </Container>
  );
}
