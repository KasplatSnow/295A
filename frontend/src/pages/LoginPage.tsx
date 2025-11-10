// src/pages/Login.tsx
import { useEffect, useState } from "react";
import { login } from "../lib/auth";
import {
  Container,
  TextField,
  Button,
  Typography,
  Box,
  Paper,
  Stack,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import useSnackbar from "../components/CustomSnackbar";
import { getUserTenants } from "../lib/user";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { SnackbarComponent, showSnackbar } = useSnackbar();

  // Autoredirect to homepage if token in storage
  useEffect(() => {
    const token = localStorage.getItem("accessToken");
    if (token) navigate("/dashboard");
  }, [navigate]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setLoading(true);
      await login(username, password);
      const tenants = await getUserTenants();

      if (tenants.length > 0) {
        showSnackbar("Welcome back!", "success");
        setTimeout(() => navigate("/dashboard"), 1200);
      } else {
        showSnackbar("Please create your community.", "info");
        setTimeout(() => navigate("/create-tenant"), 1200);
      }
    } catch (err) {
      console.error(err);
      showSnackbar("Invalid username or password", "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="sm" sx={{ mt: 10 }}>
      <Paper elevation={3} sx={{ p: 4 }}>
        <Typography variant="h5" textAlign="center" gutterBottom>
          Login
        </Typography>
        <Box component="form" onSubmit={handleLogin}>
          <Stack spacing={2}>
            <TextField
              label="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              fullWidth
              required
            />
            <TextField
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              fullWidth
              required
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              disabled={loading}
            >
              {loading ? "Signing in..." : "Sign In"}
            </Button>
          </Stack>
        </Box>
        <Typography variant="body2" align="center" sx={{ mt: 2 }}>
          Don’t have an account?{" "}
          <a
            href="/signup"
            style={{ textDecoration: "none", color: "#1976d2" }}
          >
            Sign up
          </a>
        </Typography>
      </Paper>
      {SnackbarComponent}
    </Container>
  );
}
