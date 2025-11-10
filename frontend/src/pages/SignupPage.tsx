// src/pages/Signup.tsx
import { useState } from "react";
import { register, login } from "../lib/auth";
import {
  Container,
  Paper,
  Typography,
  TextField,
  Button,
  Box,
  Stack,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import useSnackbar from "../components/CustomSnackbar";
import { getUserTenants } from "../lib/user";

export default function SignupPage() {
  const [form, setForm] = useState({
    username: "",
    email: "",
    password: "",
    confirmPassword: "",
  });
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { SnackbarComponent, showSnackbar } = useSnackbar();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (form.password !== form.confirmPassword) {
      showSnackbar("Passwords do not match", "warning");
      return;
    }

    try {
      setLoading(true);
      await register(form.username, form.email, form.password);
      await login(form.username, form.password);

      showSnackbar("Account created and logged in!", "success");

      const tenants = await getUserTenants();
      if (tenants.length > 0) {
        showSnackbar("Welcome back!", "success");
        setTimeout(() => navigate("/dashboard"), 1200);
      } else {
        showSnackbar("Let's set up your home/community!", "info");
        setTimeout(() => navigate("/create-tenant"), 1200);
      }
    } catch (err: any) {
      showSnackbar(
        err.response?.data?.detail || "Registration failed",
        "error"
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="sm" sx={{ mt: 10 }}>
      <Paper elevation={3} sx={{ p: 4 }}>
        <Typography variant="h5" textAlign="center" gutterBottom>
          Create an Account
        </Typography>
        <Box component="form" onSubmit={handleSubmit}>
          <Stack spacing={2}>
            <TextField
              label="Username"
              name="username"
              value={form.username}
              onChange={handleChange}
              fullWidth
              required
            />
            <TextField
              label="Email"
              name="email"
              type="email"
              value={form.email}
              onChange={handleChange}
              fullWidth
              required
            />
            <TextField
              label="Password"
              name="password"
              type="password"
              value={form.password}
              onChange={handleChange}
              fullWidth
              required
            />
            <TextField
              label="Confirm Password"
              name="confirmPassword"
              type="password"
              value={form.confirmPassword}
              onChange={handleChange}
              fullWidth
              required
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              disabled={loading}
            >
              {loading ? "Creating Account..." : "Sign Up"}
            </Button>
          </Stack>
        </Box>
      </Paper>

      {SnackbarComponent}
    </Container>
  );
}
