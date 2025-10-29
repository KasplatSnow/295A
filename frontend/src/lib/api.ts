import axios, { type AxiosRequestHeaders } from "axios";

const API_BASE = process.env.REACT_APP_API_BASE ?? "http://localhost:8000/api";
const TENANT_ID = process.env.REACT_APP_TENANT_ID ?? "";

console.log("[API] BASE =", API_BASE);
console.log("[API] TENANT_ID =", TENANT_ID || "(empty!)");

export const api = axios.create({ baseURL: API_BASE,  });

// set default tenant header safely (no casting issues)
if (TENANT_ID) {
  api.defaults.headers.common["X-Tenant-ID"] = TENANT_ID;
}

let accessToken: string | null = localStorage.getItem("accessToken");
export function setAccessToken(t: string | null) {
  accessToken = t;
  if (t) localStorage.setItem("accessToken", t);
  else localStorage.removeItem("accessToken");
}

// request: add headers
api.interceptors.request.use((config) => {
  const headers = (config.headers ?? {}) as AxiosRequestHeaders;
  if (TENANT_ID) headers["X-Tenant-ID"] = TENANT_ID;
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  config.headers = headers;
  return config;
});

// response: try refresh once on 401
api.interceptors.response.use(
  (r) => r,
  async (err) => {
    if (err.response?.status === 401 && !err.config._retry) {
      err.config._retry = true;
      try {
        const rt = localStorage.getItem("refreshToken");
        if (!rt) throw new Error("no refresh");
        const { data } = await axios.post(`${API_BASE}/auth/refresh/`, { refresh: rt });
        setAccessToken(data.access);
        return api(err.config);
      } catch {
        setAccessToken(null);
      }
    }
    throw err;
  }
);
