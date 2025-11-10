import { api } from "./api";

// Fetch the list of tenants the current user belongs to
export async function getUserTenants() {
  const { data } = await api.get("/tenants/");
  return data; // expected: an array of tenant objects
}
