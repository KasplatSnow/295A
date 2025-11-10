// src/pages/Dashboard.tsx
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Container, Typography, CircularProgress, Button, Stack } from "@mui/material";
import { useNavigate } from "react-router-dom";

export default function Dashboard() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/tenants/")
      .then((res) => setTenants(res.data))
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <CircularProgress sx={{ mt: 10 }} />;

  return (
    <Container sx={{ mt: 6 }}>
      <Typography variant="h4" gutterBottom>
        Welcome to your Dashboard
      </Typography>

      {tenants.length > 0 ? (
        <>
          <Typography variant="body1" sx={{ mb: 2 }}>
            You are part of the following tenants:
          </Typography>
          <ul>
            {tenants.map((t) => (
              <li key={t.id}>
                <Typography variant="body1">{t.name}</Typography>
              </li>
            ))}
          </ul>
          <Button
            variant="contained"
            color="primary"
            onClick={() => navigate("/profile")}
          >
            Go to Profile
          </Button>
        </>
      ) : (
        <Stack spacing={2}>
          <Typography variant="body1" color="text.secondary">
            You are not part of any tenant yet.
          </Typography>
          <Button
            variant="contained"
            onClick={() => navigate("/create-tenant")}
          >
            Create Tenant
          </Button>
        </Stack>
      )}
    </Container>
  );
}
