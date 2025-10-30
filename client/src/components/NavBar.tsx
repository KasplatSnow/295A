import { useState } from "react";
import { Link, useLocation } from "wouter";
import { Shield, LayoutDashboard, Video, FileText, Settings as SettingsIcon, AlertTriangle, Users, User, Menu } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { ThemeToggle } from "./ThemeToggle";

export default function NavBar() {
  const [location] = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const navItems = [
    { path: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { path: "/cameras", label: "Cameras", icon: Video },
    { path: "/incidents", label: "Incidents", icon: AlertTriangle },
    { path: "/entities", label: "Entities", icon: User },
    { path: "/community", label: "Community", icon: Users },
    { path: "/reports", label: "Reports", icon: FileText },
    { path: "/settings", label: "Settings", icon: SettingsIcon },
  ];

  return (
    <nav className="border-b bg-card">
      <div className="flex items-center justify-between px-4 sm:px-6 h-16">
        <div className="flex items-center gap-4 sm:gap-8">
          <Link href="/dashboard" className="flex items-center gap-2 hover-elevate px-2 py-1 rounded">
            <Shield className="w-6 h-6 text-primary" />
            <span className="font-semibold text-base sm:text-lg hidden xs:inline">VigilZone</span>
            <span className="font-semibold text-base sm:text-lg xs:hidden">VZ</span>
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

        <div className="flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          <div className="text-right hidden sm:block">
            <p className="text-sm font-medium">John Doe</p>
            <p className="text-xs text-muted-foreground">Administrator</p>
          </div>
          <Avatar data-testid="avatar-user" className="hidden xs:flex">
            <AvatarFallback className="bg-primary text-primary-foreground">JD</AvatarFallback>
          </Avatar>
          
          <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden" data-testid="button-mobile-menu">
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
                  const isActive = location === item.path || location.startsWith(item.path + "/");
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
                      <AvatarFallback className="bg-primary text-primary-foreground">JD</AvatarFallback>
                    </Avatar>
                    <div>
                      <p className="text-sm font-medium">John Doe</p>
                      <p className="text-xs text-muted-foreground">Administrator</p>
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
