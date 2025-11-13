import React from "react";

export default function ProfilePage() {
  // Minimal placeholder - will be expanded later
  // Currently displays basic user info if available
  const username = localStorage.getItem("username") || "Guest";

  return (
    <div style={{ maxWidth: "600px", margin: "50px auto", padding: "20px" }}>
      <h1>Profile</h1>
      <div style={{ marginTop: "20px" }}>
        <p>
          <strong>Username:</strong> {username}
        </p>
        <p style={{ marginTop: "10px", color: "#666" }}>
          Profile details will be expanded in future updates.
        </p>
      </div>
    </div>
  );
}
