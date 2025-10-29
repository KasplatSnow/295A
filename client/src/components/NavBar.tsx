import { Link, useLocation } from "wouter";
import { Shield, LayoutDashboard, Video, FileText, Settings as SettingsIcon } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export default function NavBar() {
  const [location] = useLocation();

  const navItems = [
    { path: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { path: "/cameras", label: "Cameras", icon: Video },
    { path: "/reports", label: "Reports", icon: FileText },
    { path: "/settings", label: "Settings", icon: SettingsIcon },
  ];

  return (
    <nav className="border-b bg-card">
      <div className="flex items-center justify-between px-6 h-16">
        <div className="flex items-center gap-8">
          <Link href="/dashboard" className="flex items-center gap-2 hover-elevate px-2 py-1 rounded">
            <Shield className="w-6 h-6 text-primary" />
            <span className="font-semibold text-lg">Secure Cloud Intelligence</span>
          </Link>
          
          <div className="hidden md:flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = location === item.path || location.startsWith(item.path + "/");
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

        <div className="flex items-center gap-3">
          <div className="text-right hidden sm:block">
            <p className="text-sm font-medium">John Doe</p>
            <p className="text-xs text-muted-foreground">Administrator</p>
          </div>
          <Avatar data-testid="avatar-user">
            <AvatarFallback className="bg-primary text-primary-foreground">JD</AvatarFallback>
          </Avatar>
        </div>
      </div>
    </nav>
  );
}
