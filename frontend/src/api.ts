export type UserRole = "landlord" | "tenant";

export type User = {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
};

export type RentalProperty = {
  id: number;
  landlord_id: number;
  name: string;
  address: string;
  description?: string | null;
};

export type RentalUnit = {
  id: number;
  property_id: number;
  name: string;
  description?: string | null;
};

export type Tenant = {
  id: number;
  user_id?: number | null;
  landlord_id?: number | null;
  full_name: string;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
};

export type Lease = {
  id: number;
  landlord_id: number;
  property_id: number;
  unit_id?: number | null;
  start_date: string;
  end_date: string;
  monthly_rent: string;
  tenant_ids: number[];
  notes?: string | null;
};

export type Bill = {
  id: number;
  landlord_id: number;
  lease_id: number;
  tenant_id: number;
  bill_year: number;
  bill_month: number;
  due_date: string;
  amount_due: string;
  amount_paid: string;
  balance: string;
  status: string;
};

export type Payment = {
  id: number;
  bill_id: number;
  landlord_id: number;
  tenant_id: number;
  amount: string;
  paid_at: string;
  note?: string | null;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "rental_mgmt_token";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = await response.json();
      message = Array.isArray(payload.detail)
        ? payload.detail.map((item: { msg?: string }) => item.msg).join(", ")
        : payload.detail ?? message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  register: (payload: { email: string; full_name: string; password: string; role: UserRole }) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify(payload) }),
  login: (payload: { email: string; password: string }) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  me: () => request<User>("/me"),
  listProperties: () => request<RentalProperty[]>("/properties"),
  createProperty: (payload: { name: string; address: string; description?: string }) =>
    request<RentalProperty>("/properties", { method: "POST", body: JSON.stringify(payload) }),
  updateProperty: (id: number, payload: Partial<Pick<RentalProperty, "name" | "address" | "description">>) =>
    request<RentalProperty>(`/properties/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteProperty: (id: number) => request<void>(`/properties/${id}`, { method: "DELETE" }),
  listUnits: () => request<RentalUnit[]>("/units"),
  listPropertyUnits: (propertyId: number) => request<RentalUnit[]>(`/properties/${propertyId}/units`),
  createUnit: (propertyId: number, payload: { name: string; description?: string }) =>
    request<RentalUnit>(`/properties/${propertyId}/units`, { method: "POST", body: JSON.stringify(payload) }),
  updateUnit: (id: number, payload: Partial<Pick<RentalUnit, "name" | "description">>) =>
    request<RentalUnit>(`/units/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteUnit: (id: number) => request<void>(`/units/${id}`, { method: "DELETE" }),
  listTenants: () => request<Tenant[]>("/tenants"),
  createTenant: (payload: { full_name: string; email?: string; phone?: string; notes?: string }) =>
    request<Tenant>("/tenants", { method: "POST", body: JSON.stringify(payload) }),
  updateTenant: (id: number, payload: Partial<Pick<Tenant, "full_name" | "email" | "phone" | "notes">>) =>
    request<Tenant>(`/tenants/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteTenant: (id: number) => request<void>(`/tenants/${id}`, { method: "DELETE" }),
  listLeases: () => request<Lease[]>("/leases"),
  createLease: (payload: {
    property_id: number;
    unit_id?: number | null;
    tenant_ids: number[];
    start_date: string;
    end_date: string;
    monthly_rent: string;
    notes?: string;
  }) => request<Lease>("/leases", { method: "POST", body: JSON.stringify(payload) }),
  updateLease: (id: number, payload: Partial<Lease>) =>
    request<Lease>(`/leases/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteLease: (id: number) => request<void>(`/leases/${id}`, { method: "DELETE" }),
  generateCurrentBills: () => request<Bill[]>("/bills/generate-current-month", { method: "POST" }),
  listBills: () => request<Bill[]>("/bills"),
  recordPayment: (billId: number, payload: { amount: string; paid_at?: string; note?: string }) =>
    request<{ payment: Payment; bill: Bill }>(`/bills/${billId}/payments`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
