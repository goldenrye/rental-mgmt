import { runtimeConfig, sessionStorage } from "./platform";

export type UserRole = "landlord" | "tenant" | "super_admin";

export type GoogleCalendarStatus = {
  available: boolean;
  enabled: boolean;
  connected: boolean;
  calendar_name: string | null;
  pending_exports: number;
  last_synced_at: string | null;
  last_error: string | null;
};

export type WebPushStatus = {
  available: boolean;
  public_key: string | null;
  devices: number;
  lease_expiry: boolean;
  bill_due: boolean;
  lease_notice_days: number;
  bill_notice_days: number;
};

export type McpApiKey = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  status: "active" | "expired" | "revoked";
  scope: string;
};

export type User = {
  id: number;
  email: string;
  full_name: string;
  display_name?: string | null;
  role: UserRole;
  password_change_required: boolean;
  is_active: boolean;
  encryption_enabled: boolean;
};

export type LandlordAccount = {
  user_id: number;
  landlord_id: number | null;
  full_name: string;
  email: string;
  is_active: boolean;
  last_login_at: string | null;
  last_seen_at: string | null;
  active_sessions: number;
  login_status: "signed_in" | "signed_out" | "never_logged_in";
  created_at: string;
};

export type LandlordInvite = {
  id: number;
  email: string | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by_email: string | null;
  revoked_at: string | null;
  status: "active" | "used" | "expired" | "revoked";
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
  tenants: LeaseTenantTerms[];
  notes?: string | null;
};

export type LeaseTenantTerms = {
  tenant_id: number;
  monthly_rent: string;
  deposit: string;
};

export type Bill = {
  id: number;
  landlord_id: number;
  lease_id: number;
  tenant_id: number;
  tenant_name: string;
  is_tenant_charge: boolean;
  source_property_bill_id?: number | null;
  title: string;
  bill_type: string;
  recurrence?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  bill_year: number;
  bill_month: number;
  due_date: string;
  amount_due: string;
  amount_paid: string;
  balance: string;
  status: string;
};

export type PropertyBill = {
  id: number;
  landlord_id: number;
  property_id: number;
  bill_type: "electricity" | "water" | "mortgage" | "insurance" | "property_tax" | "maintenance" | "credit_card" | "communication" | "garbage" | "hoa" | "management" | "other";
  title: string;
  amount_due: string;
  recurrence: "one_time" | "monthly" | "bi_monthly" | "quarterly" | "semi_annually" | "annually" | "custom_period";
  due_date: string;
  period_start?: string | null;
  period_end?: string | null;
  status: "unpaid" | "paid";
  paid_at?: string | null;
  generated_at?: string | null;
  notes?: string | null;
};

export type PropertyBillExtensionPreview = {
  source_bill_id: number;
  title: string;
  property_name: string;
  recurrence: PropertyBill["recurrence"];
  current_end: string;
  through: string;
  due_day: number;
  existing_future_bills: PropertyBill[];
  new_bills: { due_date: string; amount_due: string }[];
  total_new_amount: string;
  warning: string | null;
  preview_token: string;
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

export type TenantChargePreview = {
  property_bill_id: number;
  property_name: string;
  title: string;
  bill_type: string;
  recurrence: string;
  amount_due: string;
  due_date: string;
  period_start: string;
  period_end: string;
  preview_token: string;
  tenants: { tenant_id: number; lease_id: number; tenant_name: string; amount_due: string }[];
};

const API_BASE_URL = runtimeConfig.apiBaseURL;

export function getStoredToken(): string | null {
  return sessionStorage.get();
}

export async function storeToken(token: string): Promise<void> {
  await sessionStorage.set(token);
}

export async function clearToken(): Promise<void> {
  await sessionStorage.clear();
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
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
    cache: "no-store",
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
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  register: (payload: { email: string; full_name: string; password: string; role: UserRole; invite_code?: string }) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify(payload) }),
  login: (payload: { email: string; password: string }) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  listLandlordAccounts: () => request<LandlordAccount[]>("/admin/landlords"),
  listInvites: () => request<LandlordInvite[]>("/admin/invites"),
  createInvite: (payload: { email?: string; expires_in_days: number }) =>
    request<LandlordInvite & { code: string }>("/admin/invites", { method: "POST", body: JSON.stringify(payload) }),
  revokeInvite: (inviteId: number) => request<void>(`/admin/invites/${inviteId}/revoke`, { method: "POST" }),
  setLandlordActive: (userId: number, is_active: boolean) =>
    request<void>(`/admin/landlords/${userId}/status`, { method: "PATCH", body: JSON.stringify({ is_active }) }),
  resetLandlordPassword: (userId: number, new_password: string) =>
    request<void>(`/admin/landlords/${userId}/password`, { method: "POST", body: JSON.stringify({ new_password }) }),
  me: () => request<User>("/me"),
  googleCalendarStatus: () => request<GoogleCalendarStatus>("/me/google-calendar"),
  setGoogleCalendarEnabled: (enabled: boolean) =>
    request<GoogleCalendarStatus>("/me/google-calendar", { method: "PUT", body: JSON.stringify({ enabled }) }),
  connectGoogleCalendar: () => request<{ authorization_url: string }>("/me/google-calendar/connect", {
    method: "POST", credentials: "include",
  }),
  disconnectGoogleCalendar: () => request<void>("/me/google-calendar", { method: "DELETE" }),
  webPushStatus: () => request<WebPushStatus>("/me/web-push"),
  setWebPushPreferences: (preferences: { lease_expiry: boolean; bill_due: boolean }) =>
    request<WebPushStatus>("/me/web-push", { method: "PUT", body: JSON.stringify(preferences) }),
  addWebPushSubscription: (subscription: PushSubscriptionJSON) =>
    request<WebPushStatus>("/me/web-push/subscriptions", { method: "POST", body: JSON.stringify(subscription) }),
  removeWebPushSubscription: (endpoint: string) =>
    request<void>("/me/web-push/subscriptions", { method: "DELETE", body: JSON.stringify({ endpoint }) }),
  testWebPush: () => request<WebPushStatus>("/me/web-push/test", { method: "POST" }),
  listMcpApiKeys: () => request<McpApiKey[]>("/me/mcp-api-keys"),
  createMcpApiKey: (payload: { name: string; current_password: string; expires_in_days: number | null }) =>
    request<McpApiKey & { api_key: string }>("/me/mcp-api-keys", { method: "POST", body: JSON.stringify(payload) }),
  revokeMcpApiKey: (id: string) => request<void>(`/me/mcp-api-keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  setEncryptionPreference: (enabled: boolean, current_password: string) =>
    request<User>("/me/encryption", { method: "PUT", body: JSON.stringify({ enabled, current_password }) }),
  updateProfile: (payload: {
    full_name: string;
    display_name?: string | null;
    current_password?: string;
    new_password?: string;
  }) => request<User>("/me", { method: "PATCH", body: JSON.stringify(payload) }),
  changePassword: (payload: { current_password: string; new_password: string }) =>
    request<void>("/me/password", { method: "POST", body: JSON.stringify(payload) }),
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
    tenants: LeaseTenantTerms[];
    start_date: string;
    end_date: string;
    monthly_rent: string;
    notes?: string;
  }) => request<Lease>("/leases", { method: "POST", body: JSON.stringify(payload) }),
  updateLease: (id: number, payload: Partial<Lease>) =>
    request<Lease>(`/leases/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteLease: (id: number) => request<void>(`/leases/${id}`, { method: "DELETE" }),
  generateCurrentBills: () => request<Bill[]>("/bills/generate-current-month", { method: "POST" }),
  listPropertyBills: () => request<PropertyBill[]>("/property-bills"),
  previewBillExtension: (id: number, through: string, due_day?: number) =>
    request<PropertyBillExtensionPreview>(`/property-bills/${id}/extension-preview`, {
      method: "POST", body: JSON.stringify({ through, due_day }),
    }),
  extendPropertyBill: (id: number, preview: PropertyBillExtensionPreview) =>
    request<PropertyBill[]>(`/property-bills/${id}/extend`, {
      method: "POST",
      body: JSON.stringify({ through: preview.through, due_day: preview.due_day, preview_token: preview.preview_token }),
    }),
  createPropertyBills: (payload: {
    property_id: number;
    bill_type: PropertyBill["bill_type"];
    title: string;
    amount_due: string;
    recurrence: "one_time" | "monthly" | "bi_monthly" | "quarterly" | "semi_annually" | "annually" | "custom_period";
    due_date: string;
    period_start?: string;
    period_end?: string;
    notes?: string;
  }) => request<PropertyBill[]>("/property-bills", { method: "POST", body: JSON.stringify(payload) }),
  updatePropertyBill: (id: number, payload: Partial<PropertyBill>) =>
    request<PropertyBill>(`/property-bills/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deletePropertyBill: (id: number) => request<void>(`/property-bills/${id}`, { method: "DELETE" }),
  previewTenantCharges: (id: number) =>
    request<TenantChargePreview>(`/property-bills/${id}/tenant-bills-preview`),
  generateTenantBillsFromPropertyBill: (id: number, previewToken: string) =>
    request<Bill[]>(`/property-bills/${id}/generate-tenant-bills`, {
      method: "POST",
      body: JSON.stringify({ preview_token: previewToken }),
    }),
  listBills: () => request<Bill[]>("/bills"),
  deleteBill: (id: number) => request<void>(`/bills/${id}`, { method: "DELETE" }),
  listPayments: () => request<Payment[]>("/payments"),
  recordPayment: (billId: number, payload: { amount: string; paid_at?: string; note?: string }) =>
    request<{ payment: Payment; bill: Bill }>(`/bills/${billId}/payments`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
