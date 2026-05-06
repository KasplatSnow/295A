import type { ReactNode } from "react";
import { Redirect } from "wouter";
import { useAuth } from "@/auth/AuthProvider";

export default function AdminOnlyRoute({ children }: { children: ReactNode }) {
  const { isLoading, isAuthenticated, tenantId, atLeast } = useAuth();

  if (isLoading) return null;
  if (!isAuthenticated) return <Redirect to="/login" />;
  if (!tenantId) return <Redirect to="/select-community" />;
  if (!atLeast("admin")) return <Redirect to="/dashboard" />;
  return <>{children}</>;
}
