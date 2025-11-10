// src/App.tsx
import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider, createTheme, CssBaseline } from "@mui/material";
import LoginPage from "./pages/LoginPage"
import SignupPage from "./pages/SignupPage";
import ProfilePage from "./pages/ProfilePage";
import Navbar from "./components/Navbar";
import Dashboard from "./pages/Dashboard";
import CreateTenantPage from "./pages/CreateTenant";

const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#1976d2" },
    secondary: { main: "#9c27b0" },
  },
});

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Router>
        <Navbar/>
       <Routes>
  <Route path="/" element={<Navigate to="/login" />} />
  <Route path="/login" element={<LoginPage />} />
  <Route path="/signup" element={<SignupPage />} />
  <Route path="/dashboard" element={<Dashboard />} />
  <Route path="/profile" element={<ProfilePage />} />
  <Route path="/create-tenant" element={<CreateTenantPage />} />
  <Route path="*" element={<h2>404 - Not Found</h2>} />
</Routes>
      </Router>
    </ThemeProvider>
  );
}

export default App;
