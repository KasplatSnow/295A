import { api, setAccessToken } from "./api";

type TokenResponse = { access: string; refresh: string };

export async function login(username: string, password: string) {
  const { data } = await api.post<TokenResponse>("/auth/token/", { username, password });
  setAccessToken(data.access);
  localStorage.setItem("refreshToken", data.refresh);
}

export async function refresh() {
  const rt = localStorage.getItem("refreshToken");
  if (!rt) throw new Error("No refresh token");
  const { data } = await api.post<TokenResponse>("/auth/refresh/", { refresh: rt });
  setAccessToken(data.access);
}
