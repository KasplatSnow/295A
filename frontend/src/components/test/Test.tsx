import { useState } from "react";
import { login } from "../../lib/auth";
import { api } from "../../lib/api";

export default function Test() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [cameras, setCameras] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  const doLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await login(username, password);
      alert("✅ Logged in successfully!");
    } catch (err: any) {
      console.error(err);
      alert("❌ Login failed!");
    }
  };

  const load = async () => {
    try {
      const { data } = await api.get("/cameras/");
      console.log("Camera data:", data);
      setCameras(data);
      setError(null);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail ?? "Error loading cameras");
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <h2>Test Component</h2>

      <form onSubmit={doLogin} style={{ marginBottom: 16 }}>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Username"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          type="password"
        />
        <button type="submit">Login</button>
      </form>

      <button onClick={load}>Load Cameras</button>

      {error && <p style={{ color: "red" }}>Error: {error}</p>}

      {cameras.length > 0 && (
        <ul>
          {cameras.map((cam) => (
            <li key={cam.id}>
              <strong>{cam.name}</strong> — {cam.status}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
