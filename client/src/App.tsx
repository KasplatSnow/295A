import { Switch, Route } from "wouter";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Incidents from "@/pages/Incidents";
import IncidentDetails from "@/pages/IncidentDetails";
import Entities from "@/pages/Entities";
import Community from "@/pages/Community";
import Reports from "@/pages/Reports";
import Cameras from "@/pages/Cameras";
import Settings from "@/pages/Settings";
import NavBar from "@/components/NavBar";

function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <NavBar />
      <main>{children}</main>
    </div>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={Login} />
      <Route path="/dashboard">
        <AuthenticatedLayout>
          <Dashboard />
        </AuthenticatedLayout>
      </Route>
      <Route path="/incidents">
        <AuthenticatedLayout>
          <Incidents />
        </AuthenticatedLayout>
      </Route>
      <Route path="/incidents/:id">
        <AuthenticatedLayout>
          <IncidentDetails />
        </AuthenticatedLayout>
      </Route>
      <Route path="/entities">
        <AuthenticatedLayout>
          <Entities />
        </AuthenticatedLayout>
      </Route>
      <Route path="/community">
        <AuthenticatedLayout>
          <Community />
        </AuthenticatedLayout>
      </Route>
      <Route path="/reports">
        <AuthenticatedLayout>
          <Reports />
        </AuthenticatedLayout>
      </Route>
      <Route path="/cameras">
        <AuthenticatedLayout>
          <Cameras />
        </AuthenticatedLayout>
      </Route>
      <Route path="/settings">
        <AuthenticatedLayout>
          <Settings />
        </AuthenticatedLayout>
      </Route>
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Toaster />
        <Router />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
