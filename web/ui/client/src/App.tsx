import { Switch, Route, Redirect, useLocation } from "wouter";
import React, { Suspense, useEffect } from "react";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider } from "@/components/ThemeProvider";
import { AuthProvider } from "@/auth/AuthProvider";
import { unlockAudio } from "@/lib/audio";

import NavBar from "@/components/NavBar";
import LoadingDisplay from "@/components/LoadingDisplay";
import AuthOnlyRoute from "./components/routing/AuthOnlyRoute";
import TenantOnlyRoute from "./components/routing/TenantOnlyRoute";
import PublicOnlyRoute from "./components/routing/PublicOnlyRoute";
import AdminOnlyRoute from "./components/routing/AdminOnlyRoute";

// Lazy-loaded pages
const NotFound = React.lazy(() => import("@/pages/not-found"));
const Login = React.lazy(() => import("@/pages/Login"));
const Register = React.lazy(() => import("@/pages/Register"));
const ForgotPassword = React.lazy(() => import("@/pages/ForgotPassword"));
const Dashboard = React.lazy(() => import("@/pages/Dashboard"));
const Incidents = React.lazy(() => import("@/pages/Incidents"));
const IncidentDetails = React.lazy(() => import("@/pages/IncidentDetails"));
const Entities = React.lazy(() => import("@/pages/Entities"));
const Community = React.lazy(() => import("@/pages/Community"));
const Reports = React.lazy(() => import("@/pages/Reports"));
const Cameras = React.lazy(() => import("@/pages/Cameras"));
const Settings = React.lazy(() => import("@/pages/Settings"));
const LiveAI = React.lazy(() => import("@/pages/LiveAI"));
const Debug = React.lazy(() => import("@/pages/Debug"));
const SelectCommunity = React.lazy(() => import("./pages/SelectCommunity"));

function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <NavBar />
      <main>{children}</main>
    </div>
  );
}

const APP_SHELL_PREFIXES = [
  "/dashboard",
  "/incidents",
  "/entities",
  "/community",
  "/select-community",
  "/reports",
  "/cameras",
  "/live-ai",
  "/settings",
  "/debug",
] as const;

function AppShellRoutes() {
  return (
    <Switch>
      <Route path="/dashboard">
        <TenantOnlyRoute>
          <Dashboard />
        </TenantOnlyRoute>
      </Route>
      <Route path="/incidents">
        <TenantOnlyRoute>
          <Incidents />
        </TenantOnlyRoute>
      </Route>
      <Route path="/incidents/:id">
        <TenantOnlyRoute>
          <IncidentDetails />
        </TenantOnlyRoute>
      </Route>
      <Route path="/entities">
        <TenantOnlyRoute>
          <Entities />
        </TenantOnlyRoute>
      </Route>
      <Route path="/community">
        <TenantOnlyRoute>
          <Community />
        </TenantOnlyRoute>
      </Route>
      <Route path="/select-community">
        <AuthOnlyRoute>
          <SelectCommunity />
        </AuthOnlyRoute>
      </Route>
      <Route path="/reports">
        <TenantOnlyRoute>
          <Reports />
        </TenantOnlyRoute>
      </Route>
      <Route path="/cameras">
        <TenantOnlyRoute>
          <Cameras />
        </TenantOnlyRoute>
      </Route>
      <Route path="/live-ai">
        <TenantOnlyRoute>
          <LiveAI />
        </TenantOnlyRoute>
      </Route>
      <Route path="/settings">
        <TenantOnlyRoute>
          <Settings />
        </TenantOnlyRoute>
      </Route>
      <Route path="/debug">
        <AdminOnlyRoute>
          <Debug />
        </AdminOnlyRoute>
      </Route>
      <Route component={NotFound} />
    </Switch>
  );
}

function Router() {
  const [location] = useLocation();
  const usesAppShell = APP_SHELL_PREFIXES.some(
    (prefix) => location === prefix || location.startsWith(`${prefix}/`),
  );

  return (
    <Suspense fallback={<LoadingDisplay />}>
      {location === "/" ? (
        <Redirect to="/dashboard" />
      ) : usesAppShell ? (
        <AuthenticatedLayout>
          <AppShellRoutes />
        </AuthenticatedLayout>
      ) : (
        <Switch>
          <PublicOnlyRoute path="/login" component={Login} />
          <PublicOnlyRoute path="/register" component={Register} />
          <PublicOnlyRoute path="/forgot-password" component={ForgotPassword} />
          <Route component={NotFound} />
        </Switch>
      )}
    </Suspense>
  );
}
function App() {
  useEffect(() => {
    // High-performance, one-time listener to unlock the Web Audio API on first interaction.
    // This allows background notification sounds for the rest of the session.
    const handleFirstInteraction = () => {
      unlockAudio();
      document.removeEventListener('click', handleFirstInteraction);
      document.removeEventListener('keydown', handleFirstInteraction);
    };

    document.addEventListener('click', handleFirstInteraction);
    document.addEventListener('keydown', handleFirstInteraction);

    return () => {
      document.removeEventListener('click', handleFirstInteraction);
      document.removeEventListener('keydown', handleFirstInteraction);
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider>
          <Toaster />
          <AuthProvider>
            <Router />
          </AuthProvider>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export default App;
