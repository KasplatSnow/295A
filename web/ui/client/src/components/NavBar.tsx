import { useState, useEffect } from "react";
import { Link, useLocation } from "wouter";
import {
  Shield,
  LayoutDashboard,
  Video,
  FileText,
  Settings as SettingsIcon,
  AlertTriangle,
  Users,
  User,
  Menu,
  Brain,
  Bug,
} from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { ThemeToggle } from "./ThemeToggle";
import { LogOut } from "lucide-react";
import { logout } from "@/lib/auth";
import { useAuth } from "@/auth/AuthProvider";
import { NotificationBell } from "./NotificationBell";
import { useNotifications } from "@/hooks/useNotifications";

export default function NavBar() {
  const [location, setLocation] = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { user, role, tenantId } = useAuth();
  const canTestNotifications = role === "owner" || role === "admin";
  const canAccessDebug = role === "owner" || role === "admin";
  
  const tenantIdNum = tenantId ? parseInt(String(tenantId), 10) : null;
  
  const {
    notifications,
    unreadCount,
    isConnected,
    isSubscribed,
    redisReachable,
    connect,
    disconnect,
    markAsRead,
    markAllAsRead,
    testWebSocket,
  } = useNotifications();
  
  // Connect WebSocket when authenticated
  useEffect(() => {
    if (tenantIdNum) {
      connect(tenantIdNum);
    }
    return () => disconnect();
  }, [tenantIdNum, connect, disconnect]);
  
  // Format role for display (capitalize first letter)
  const displayRole = role ? role.charAt(0).toUpperCase() + role.slice(1) : "Member";

  const navItems = [
    { path: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { path: "/cameras", label: "Cameras", icon: Video },
    { path: "/incidents", label: "Incidents", icon: AlertTriangle },
    { path: "/entities", label: "Entities", icon: User },
    { path: "/community", label: "Community", icon: Users },
    { path: "/live-ai", label: "Live AI", icon: Brain },
    { path: "/reports", label: "Reports", icon: FileText },
    { path: "/settings", label: "Settings", icon: SettingsIcon },
    ...(canAccessDebug ? [{ path: "/debug", label: "Debug", icon: Bug }] : []),
  ];

  return (
    <nav className="border-b bg-card">
      <div className="flex items-center justify-between px-4 sm:px-6 h-16">
        <div className="flex items-center gap-4 sm:gap-8">
          <Link
            href="/dashboard"
            className="flex items-center gap-2 hover-elevate px-2 py-1 rounded"
          >
            <Shield className="w-6 h-6 text-primary" />
            <span className="font-semibold text-base sm:text-lg hidden xs:inline">
              VigilZone
            </span>
            <span className="font-semibold text-base sm:text-lg xs:hidden">
              VZ
            </span>
          </Link>

          <div className="hidden md:flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive =
                location === item.path || location.startsWith(item.path + "/");
              return (
                <Link
                  key={item.path}
                  href={item.path}
                  className={`flex items-center gap-2 px-4 py-2 rounded-md transition-colors ${
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-foreground hover-elevate"
                  }`}
                  data-testid={`link-${item.label.toLowerCase()}`}
                >
                  <Icon className="w-4 h-4" />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          
          {/* Notification Bell */}
          {tenantIdNum && (
            <NotificationBell
              notifications={notifications}
              unreadCount={unreadCount}
              onMarkAsRead={markAsRead}
              onMarkAllAsRead={markAllAsRead}
              isConnected={isConnected}
              isSubscribed={isSubscribed}
              transportHealthy={redisReachable && isConnected && isSubscribed}
              tenantId={tenantIdNum}
              onTestConnection={canTestNotifications ? () => testWebSocket(tenantIdNum) : undefined}
              onNavigate={(path) => setLocation(path)}
            />
          )}
          
          <div className="text-right hidden sm:block">
            <p className="text-sm font-medium">{user?.username || "User"}</p>
            <p className="text-xs text-muted-foreground">{displayRole}</p>
          </div>
          <Avatar data-testid="avatar-user" className="hidden xs:flex">
            <AvatarFallback className="bg-primary text-primary-foreground">
              {user?.username ? user.username.slice(0, 2).toUpperCase() : "U"}
            </AvatarFallback>
          </Avatar>
          <Button
            variant="ghost"
            className="hidden md:inline-flex"
            onClick={() => {
              logout();
              setLocation("/login");
            }}
            data-testid="button-logout"
          >
            <LogOut className="w-4 h-4 mr-2" />
            Logout
          </Button>

          <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                data-testid="button-mobile-menu"
              >
                <Menu className="w-5 h-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[280px] sm:w-[320px]">
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2">
                  <Shield className="w-5 h-5 text-primary" />
                  Menu
                </SheetTitle>
              </SheetHeader>
              <div className="flex flex-col gap-2 mt-6">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive =
                    location === item.path ||
                    location.startsWith(item.path + "/");
                  return (
                    <Link
                      key={item.path}
                      href={item.path}
                      onClick={() => setMobileMenuOpen(false)}
                      className={`flex items-center gap-3 px-4 py-3 rounded-md transition-colors ${
                        isActive
                          ? "bg-primary text-primary-foreground"
                          : "text-foreground hover-elevate"
                      }`}
                      data-testid={`link-mobile-${item.label.toLowerCase()}`}
                    >
                      <Icon className="w-5 h-5" />
                      <span className="font-medium">{item.label}</span>
                    </Link>
                  );
                })}
                <div className="border-t mt-4 pt-4">
                  <div className="flex items-center gap-3 px-4">
                    <Avatar>
                      <AvatarFallback className="bg-primary text-primary-foreground">
                        {user?.username ? user.username.slice(0, 2).toUpperCase() : "U"}
                      </AvatarFallback>
                    </Avatar>
                    <Button
                      variant="ghost"
                      className="w-full justify-start mt-3"
                      onClick={() => {
                        setMobileMenuOpen(false);
                        logout();
                        setLocation("/login");
                      }}
                      data-testid="button-mobile-logout"
                    >
                      <LogOut className="w-5 h-5 mr-3" />
                      Logout
                    </Button>
                    <div>
                      <p className="text-sm font-medium">{user?.username || "User"}</p>
                      <p className="text-xs text-muted-foreground">
                        {displayRole}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </nav>
  );
}
