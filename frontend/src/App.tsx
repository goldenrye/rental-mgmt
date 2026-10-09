import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Bill,
  Lease,
  Payment,
  RentalProperty,
  RentalUnit,
  Tenant,
  User,
  UserRole,
  api,
  clearToken,
  getStoredToken,
  storeToken,
} from "./api";
import "./styles.css";

type Tab = "properties" | "units" | "tenants" | "leases" | "bills";
const tabLabels: Record<Tab, string> = {
  properties: "properties",
  units: "units",
  tenants: "tenants",
  leases: "leases",
  bills: "rental",
};

type PropertyForm = {
  name: string;
  address: string;
  description: string;
};

type TenantForm = {
  full_name: string;
  email: string;
  phone: string;
  notes: string;
};

type UnitForm = {
  property_id: string;
  name: string;
  description: string;
};

type LeaseForm = {
  property_id: string;
  unit_id: string;
  tenants: { tenant_id: string; monthly_rent: string; deposit: string }[];
  start_date: string;
  end_date: string;
  monthly_rent: string;
  notes: string;
};

const emptyProperty: PropertyForm = { name: "", address: "", description: "" };
const emptyTenant: TenantForm = { full_name: "", email: "", phone: "", notes: "" };
const emptyUnit: UnitForm = { property_id: "", name: "", description: "" };
const emptyLease: LeaseForm = {
  property_id: "",
  unit_id: "",
  tenants: [],
  start_date: "",
  end_date: "",
  monthly_rent: "",
  notes: "",
};

function splitMonthlyRent(monthlyRent: string, tenantCount: number): string[] {
  if (tenantCount <= 0) {
    return [];
  }
  const totalCents = Math.round(Number(monthlyRent || 0) * 100);
  const baseShare = Math.floor(totalCents / tenantCount);
  const remainder = totalCents % tenantCount;
  return Array.from({ length: tenantCount }, (_, index) => ((baseShare + (index < remainder ? 1 : 0)) / 100).toFixed(2));
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authForm, setAuthForm] = useState({
    email: "",
    full_name: "",
    password: "",
    role: "landlord" as UserRole,
  });
  const [activeTab, setActiveTab] = useState<Tab>("properties");
  const [properties, setProperties] = useState<RentalProperty[]>([]);
  const [units, setUnits] = useState<RentalUnit[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [leases, setLeases] = useState<Lease[]>([]);
  const [bills, setBills] = useState<Bill[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [showHistoricPaidBills, setShowHistoricPaidBills] = useState(false);
  const [propertyForm, setPropertyForm] = useState<PropertyForm>(emptyProperty);
  const [unitForm, setUnitForm] = useState<UnitForm>(emptyUnit);
  const [tenantForm, setTenantForm] = useState<TenantForm>(emptyTenant);
  const [leaseForm, setLeaseForm] = useState<LeaseForm>(emptyLease);
  const [editingPropertyId, setEditingPropertyId] = useState<number | null>(null);
  const [editingUnitId, setEditingUnitId] = useState<number | null>(null);
  const [editingTenantId, setEditingTenantId] = useState<number | null>(null);
  const [editingLeaseId, setEditingLeaseId] = useState<number | null>(null);
  const [leaseModalOpen, setLeaseModalOpen] = useState(false);
  const [expandedLeaseId, setExpandedLeaseId] = useState<number | null>(null);
  const [selectedPaymentTenantId, setSelectedPaymentTenantId] = useState<number | null>(null);
  const [tenantDropdownOpen, setTenantDropdownOpen] = useState(false);
  const [paymentByBill, setPaymentByBill] = useState<Record<number, { amount: string; paid_at: string; note: string }>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const propertyNameById = useMemo(
    () => new Map(properties.map((property) => [property.id, property.name])),
    [properties],
  );
  const unitNameById = useMemo(() => new Map(units.map((unit) => [unit.id, unit.name])), [units]);
  const unitsForLeaseProperty = useMemo(
    () => units.filter((unit) => String(unit.property_id) === leaseForm.property_id),
    [leaseForm.property_id, units],
  );
  const tenantNameById = useMemo(() => new Map(tenants.map((tenant) => [tenant.id, tenant.full_name])), [tenants]);
  const tenantDepositPaidById = useMemo(() => {
    const depositByTenant = new Map<number, number>();
    leases.forEach((lease) => {
      lease.tenants.forEach((tenantTerm) => {
        depositByTenant.set(
          tenantTerm.tenant_id,
          (depositByTenant.get(tenantTerm.tenant_id) ?? 0) + Number(tenantTerm.deposit || 0),
        );
      });
    });
    return depositByTenant;
  }, [leases]);
  const availableLeaseTenants = useMemo(() => {
    const assignedTenantIds = new Set(
      leases
        .filter((lease) => lease.id !== editingLeaseId)
        .flatMap((lease) => lease.tenant_ids),
    );
    const selectedTenantIds = new Set(leaseForm.tenants.map((tenant) => Number(tenant.tenant_id)));
    return tenants.filter((tenant) => !assignedTenantIds.has(tenant.id) || selectedTenantIds.has(tenant.id));
  }, [editingLeaseId, leaseForm.tenants, leases, tenants]);
  const selectedLeaseTenants = useMemo(
    () => tenants.filter((tenant) => leaseForm.tenants.some((term) => term.tenant_id === String(tenant.id))),
    [leaseForm.tenants, tenants],
  );
  const visibleBills = useMemo(
    () => bills.filter((bill) => showHistoricPaidBills || bill.status !== "paid"),
    [bills, showHistoricPaidBills],
  );
  const hiddenPaidBillCount = bills.length - visibleBills.length;
  const billById = useMemo(() => new Map(bills.map((bill) => [bill.id, bill])), [bills]);
  const selectedPaymentTenant = useMemo(
    () => tenants.find((tenant) => tenant.id === selectedPaymentTenantId) ?? null,
    [selectedPaymentTenantId, tenants],
  );
  const selectedTenantPayments = useMemo(
    () =>
      payments
        .filter((payment) => payment.tenant_id === selectedPaymentTenantId)
        .sort((first, second) => new Date(second.paid_at).getTime() - new Date(first.paid_at).getTime()),
    [payments, selectedPaymentTenantId],
  );

  useEffect(() => {
    if (!getStoredToken()) {
      return;
    }
    api
      .me()
      .then((currentUser) => {
        setUser(currentUser);
        if (currentUser.role === "landlord") {
          void loadLandlordData();
        }
      })
      .catch(() => clearToken());
  }, []);

  async function withStatus(action: () => Promise<void>, successMessage?: string) {
    setLoading(true);
    setError("");
    setMessage("");
    try {
      await action();
      if (successMessage) {
        setMessage(successMessage);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unexpected error";
      setError(errorMessage);
      window.alert(errorMessage);
    } finally {
      setLoading(false);
    }
  }

  function confirmDelete(itemName: string): boolean {
    return window.confirm(`Delete ${itemName}? This action cannot be undone.`);
  }

  function openAddLeaseModal() {
    setEditingLeaseId(null);
    setLeaseForm(emptyLease);
    setTenantDropdownOpen(false);
    setLeaseModalOpen(true);
  }

  function openEditLeaseModal(lease: Lease) {
    setEditingLeaseId(lease.id);
    setLeaseForm({
      property_id: String(lease.property_id),
      unit_id: lease.unit_id ? String(lease.unit_id) : "",
      tenants: lease.tenants.map((tenant) => ({
        tenant_id: String(tenant.tenant_id),
        monthly_rent: tenant.monthly_rent,
        deposit: tenant.deposit,
      })),
      start_date: lease.start_date,
      end_date: lease.end_date,
      monthly_rent: lease.monthly_rent,
      notes: lease.notes ?? "",
    });
    setTenantDropdownOpen(false);
    setLeaseModalOpen(true);
  }

  function closeLeaseModal() {
    setLeaseModalOpen(false);
    setEditingLeaseId(null);
    setLeaseForm(emptyLease);
    setTenantDropdownOpen(false);
  }

  async function loadLandlordData() {
    const [nextProperties, nextUnits, nextTenants, nextLeases, nextBills, nextPayments] = await Promise.all([
      api.listProperties(),
      api.listUnits(),
      api.listTenants(),
      api.listLeases(),
      api.listBills(),
      api.listPayments(),
    ]);
    setProperties(nextProperties);
    setUnits(nextUnits);
    setTenants(nextTenants);
    setLeases(nextLeases);
    setBills(nextBills);
    setPayments(nextPayments);
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await withStatus(async () => {
      if (authMode === "register") {
        await api.register(authForm);
      }
      const token = await api.login({ email: authForm.email, password: authForm.password });
      storeToken(token.access_token);
      const currentUser = await api.me();
      setUser(currentUser);
      if (currentUser.role === "landlord") {
        await loadLandlordData();
      }
    }, authMode === "register" ? "Account created and signed in." : "Signed in.");
  }

  function logout() {
    clearToken();
    setUser(null);
    setProperties([]);
    setUnits([]);
    setTenants([]);
    setLeases([]);
    setBills([]);
    setPayments([]);
    setSelectedPaymentTenantId(null);
    setExpandedLeaseId(null);
    setLeaseModalOpen(false);
    setTenantDropdownOpen(false);
    setMessage("Signed out.");
  }

  async function submitProperty(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await withStatus(async () => {
      if (editingPropertyId) {
        await api.updateProperty(editingPropertyId, propertyForm);
      } else {
        await api.createProperty(propertyForm);
      }
      setPropertyForm(emptyProperty);
      setEditingPropertyId(null);
      await loadLandlordData();
    }, editingPropertyId ? "Property updated." : "Property added.");
  }

  async function submitUnit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await withStatus(async () => {
      const payload = {
        name: unitForm.name,
        description: unitForm.description || undefined,
      };
      if (editingUnitId) {
        await api.updateUnit(editingUnitId, payload);
      } else {
        await api.createUnit(Number(unitForm.property_id), payload);
      }
      setUnitForm(emptyUnit);
      setEditingUnitId(null);
      await loadLandlordData();
    }, editingUnitId ? "Unit updated." : "Unit added.");
  }

  async function submitTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await withStatus(async () => {
      const payload = {
        full_name: tenantForm.full_name,
        email: tenantForm.email || undefined,
        phone: tenantForm.phone || undefined,
        notes: tenantForm.notes || undefined,
      };
      if (editingTenantId) {
        await api.updateTenant(editingTenantId, payload);
      } else {
        await api.createTenant(payload);
      }
      setTenantForm(emptyTenant);
      setEditingTenantId(null);
      await loadLandlordData();
    }, editingTenantId ? "Tenant updated." : "Tenant added.");
  }

  async function submitLease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (leaseForm.tenants.length === 0) {
      setError("Please choose at least one tenant for the lease.");
      setMessage("");
      return;
    }
    const monthlyRentCents = Math.round(Number(leaseForm.monthly_rent || 0) * 100);
    if (monthlyRentCents <= 0) {
      setError("Please enter a valid lease monthly rent.");
      setMessage("");
      return;
    }
    const tenantRentShares = splitMonthlyRent(leaseForm.monthly_rent, leaseForm.tenants.length);
    await withStatus(async () => {
      const payload = {
        property_id: Number(leaseForm.property_id),
        unit_id: leaseForm.unit_id ? Number(leaseForm.unit_id) : null,
        tenants: leaseForm.tenants.map((tenant, index) => ({
          tenant_id: Number(tenant.tenant_id),
          monthly_rent: tenantRentShares[index],
          deposit: tenant.deposit || "0",
        })),
        start_date: leaseForm.start_date,
        end_date: leaseForm.end_date,
        monthly_rent: leaseForm.monthly_rent,
        notes: leaseForm.notes || undefined,
      };
      if (editingLeaseId) {
        await api.updateLease(editingLeaseId, payload);
      } else {
        await api.createLease(payload);
      }
      setLeaseForm(emptyLease);
      setEditingLeaseId(null);
      setLeaseModalOpen(false);
      setTenantDropdownOpen(false);
      await loadLandlordData();
    }, editingLeaseId ? "Lease updated." : "Lease added.");
  }

  function toggleLeaseTenant(tenantId: number) {
    const value = String(tenantId);
    setLeaseForm((current) => ({
      ...current,
      tenants: current.tenants.some((tenant) => tenant.tenant_id === value)
        ? current.tenants.filter((tenant) => tenant.tenant_id !== value)
        : [...current.tenants, { tenant_id: value, monthly_rent: "", deposit: "0" }],
    }));
  }

  function updateLeaseTenantDeposit(tenantId: number, value: string) {
    setLeaseForm((current) => ({
      ...current,
      tenants: current.tenants.map((tenant) =>
        tenant.tenant_id === String(tenantId) ? { ...tenant, deposit: value } : tenant,
      ),
    }));
  }

  async function generateBills() {
    await withStatus(async () => {
      await api.generateCurrentBills();
      setBills(await api.listBills());
    }, "Rental generated for currently active leases.");
  }

  async function recordPayment(billId: number) {
    const payment = paymentByBill[billId] ?? { amount: "", paid_at: "", note: "" };
    await withStatus(async () => {
      await api.recordPayment(billId, {
        amount: payment.amount,
        paid_at: payment.paid_at || undefined,
        note: payment.note || undefined,
      });
      setPaymentByBill((current) => ({ ...current, [billId]: { amount: "", paid_at: "", note: "" } }));
      const [nextBills, nextPayments] = await Promise.all([api.listBills(), api.listPayments()]);
      setBills(nextBills);
      setPayments(nextPayments);
    }, "Payment recorded and balance updated.");
  }

  if (!user) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">Rental Management</p>
          <h1>Manage leases, rental, and payments</h1>
          <p className="muted">Sign in as a landlord to manage properties and tenants, or register a tenant account.</p>
          <div className="toggle">
            <button className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")}>
              Login
            </button>
            <button className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")}>
              Register
            </button>
          </div>
          <form onSubmit={submitAuth} className="form">
            {authMode === "register" && (
              <>
                <label>
                  Full name
                  <input
                    required
                    value={authForm.full_name}
                    onChange={(event) => setAuthForm({ ...authForm, full_name: event.target.value })}
                  />
                </label>
                <label>
                  Role
                  <select
                    value={authForm.role}
                    onChange={(event) => setAuthForm({ ...authForm, role: event.target.value as UserRole })}
                  >
                    <option value="landlord">Landlord</option>
                    <option value="tenant">Tenant</option>
                  </select>
                </label>
              </>
            )}
            <label>
              Email
              <input
                required
                type="email"
                value={authForm.email}
                onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
              />
            </label>
            <label>
              Password
              <input
                required
                type="password"
                minLength={8}
                maxLength={72}
                value={authForm.password}
                onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
              />
            </label>
            <button disabled={loading} className="primary">
              {loading ? "Working..." : authMode === "register" ? "Create Account" : "Login"}
            </button>
          </form>
          <Status error={error} message={message} />
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Rental Management</p>
          <h1>Welcome, {user.full_name}</h1>
          <p className="muted">{user.role === "landlord" ? "Landlord dashboard" : "Tenant account"}</p>
        </div>
        <button onClick={logout} className="secondary">
          Logout
        </button>
      </header>

      <Status error={error} message={message} />

      {user.role !== "landlord" ? (
        <section className="card">
          <h2>Tenant Portal</h2>
          <p className="muted">Tenant rental viewing is available through the backend API at /tenant/bills.</p>
        </section>
      ) : (
        <>
          <nav className="tabs">
            {(["properties", "units", "tenants", "leases", "bills"] as Tab[]).map((tab) => (
              <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
                {tabLabels[tab]}
              </button>
            ))}
          </nav>

          {activeTab === "properties" && (
            <section className="grid">
              <form onSubmit={submitProperty} className="card form">
                <h2>{editingPropertyId ? "Edit Property" : "Add Property"}</h2>
                <label>
                  Name
                  <input
                    required
                    value={propertyForm.name}
                    onChange={(event) => setPropertyForm({ ...propertyForm, name: event.target.value })}
                  />
                </label>
                <label>
                  Address
                  <input
                    required
                    value={propertyForm.address}
                    onChange={(event) => setPropertyForm({ ...propertyForm, address: event.target.value })}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    value={propertyForm.description}
                    onChange={(event) => setPropertyForm({ ...propertyForm, description: event.target.value })}
                  />
                </label>
                <button disabled={loading} className="primary">
                  {editingPropertyId ? "Save Property" : "Add Property"}
                </button>
              </form>
              <section className="card">
                <h2>Properties</h2>
                <DataTable empty="No properties yet.">
                  {properties.map((property) => (
                    <tr key={property.id}>
                      <td>{property.name}</td>
                      <td>{property.address}</td>
                      <td>{property.description || "-"}</td>
                      <td className="actions">
                        <button
                          onClick={() => {
                            setEditingPropertyId(property.id);
                            setPropertyForm({
                              name: property.name,
                              address: property.address,
                              description: property.description ?? "",
                            });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() =>
                            confirmDelete(`property "${property.name}"`) &&
                            withStatus(() => api.deleteProperty(property.id).then(loadLandlordData), "Property deleted.")
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              </section>
            </section>
          )}

          {activeTab === "units" && (
            <section className="grid">
              <form onSubmit={submitUnit} className="card form">
                <h2>{editingUnitId ? "Edit Unit" : "Add Unit"}</h2>
                <label>
                  Property
                  <select
                    required
                    disabled={Boolean(editingUnitId)}
                    value={unitForm.property_id}
                    onChange={(event) => setUnitForm({ ...unitForm, property_id: event.target.value })}
                  >
                    <option value="">Select property</option>
                    {properties.map((property) => (
                      <option key={property.id} value={property.id}>
                        {property.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Unit name
                  <input
                    required
                    placeholder="Example: Unit 101"
                    value={unitForm.name}
                    onChange={(event) => setUnitForm({ ...unitForm, name: event.target.value })}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    value={unitForm.description}
                    onChange={(event) => setUnitForm({ ...unitForm, description: event.target.value })}
                  />
                </label>
                <button disabled={loading} className="primary">
                  {editingUnitId ? "Save Unit" : "Add Unit"}
                </button>
              </form>
              <section className="card">
                <h2>Units</h2>
                <DataTable empty="No units yet. Add units under a property.">
                  {units.map((unit) => (
                    <tr key={unit.id}>
                      <td>{unit.name}</td>
                      <td>{propertyNameById.get(unit.property_id) ?? `Property #${unit.property_id}`}</td>
                      <td>{unit.description || "-"}</td>
                      <td className="actions">
                        <button
                          onClick={() => {
                            setEditingUnitId(unit.id);
                            setUnitForm({
                              property_id: String(unit.property_id),
                              name: unit.name,
                              description: unit.description ?? "",
                            });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() =>
                            confirmDelete(`unit "${unit.name}"`) &&
                            withStatus(() => api.deleteUnit(unit.id).then(loadLandlordData), "Unit deleted.")
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              </section>
            </section>
          )}

          {activeTab === "tenants" && (
            <section className="grid">
              <form onSubmit={submitTenant} className="card form">
                <h2>{editingTenantId ? "Edit Tenant" : "Add Tenant"}</h2>
                <label>
                  Full name
                  <input
                    required
                    value={tenantForm.full_name}
                    onChange={(event) => setTenantForm({ ...tenantForm, full_name: event.target.value })}
                  />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    value={tenantForm.email}
                    onChange={(event) => setTenantForm({ ...tenantForm, email: event.target.value })}
                  />
                </label>
                <label>
                  Phone
                  <input value={tenantForm.phone} onChange={(event) => setTenantForm({ ...tenantForm, phone: event.target.value })} />
                </label>
                <label>
                  Notes
                  <textarea value={tenantForm.notes} onChange={(event) => setTenantForm({ ...tenantForm, notes: event.target.value })} />
                </label>
                <button disabled={loading} className="primary">
                  {editingTenantId ? "Save Tenant" : "Add Tenant"}
                </button>
              </form>
              <section className="card">
                <h2>Tenants</h2>
                <DataTable empty="No tenants yet.">
                  {tenants.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>#{tenant.id}</td>
                      <td>{tenant.full_name}</td>
                      <td>{tenant.email || "-"}</td>
                      <td>{tenant.phone || "-"}</td>
                      <td>${(tenantDepositPaidById.get(tenant.id) ?? 0).toFixed(2)} deposit paid</td>
                      <td className="actions">
                        <button
                          onClick={() => {
                            setEditingTenantId(tenant.id);
                            setTenantForm({
                              full_name: tenant.full_name,
                              email: tenant.email ?? "",
                              phone: tenant.phone ?? "",
                              notes: tenant.notes ?? "",
                            });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() =>
                            confirmDelete(`tenant "${tenant.full_name}"`) &&
                            withStatus(() => api.deleteTenant(tenant.id).then(loadLandlordData), "Tenant deleted.")
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              </section>
            </section>
          )}

          {activeTab === "leases" && (
            <>
              <section className="card">
                <div className="card-header">
                  <div>
                    <h2>Leases</h2>
                    <p className="muted">Review lease summaries. Click a lease card to show or hide details.</p>
                  </div>
                  <button className="primary" onClick={openAddLeaseModal}>
                    Add Lease
                  </button>
                </div>
                {leases.length === 0 ? (
                  <p className="muted">No leases yet.</p>
                ) : (
                  <div className="lease-list">
                    {leases.map((lease) => {
                      const expanded = expandedLeaseId === lease.id;
                      return (
                        <article
                          className={`lease-card ${expanded ? "expanded" : ""}`}
                          key={lease.id}
                          onClick={() => setExpandedLeaseId(expanded ? null : lease.id)}
                        >
                          <div className="lease-card-header">
                            <div>
                              <p className="eyebrow">Lease #{lease.id}</p>
                              <h3>{propertyNameById.get(lease.property_id) ?? `Property #${lease.property_id}`}</h3>
                              <p className="muted">
                                {lease.unit_id ? (unitNameById.get(lease.unit_id) ?? `Unit #${lease.unit_id}`) : "Whole property"}
                              </p>
                            </div>
                            <div className="actions lease-actions">
                              <button
                                onClick={(event) => {
                                  event.stopPropagation();
                                  openEditLeaseModal(lease);
                                }}
                              >
                                Edit
                              </button>
                              <button
                                onClick={(event) => {
                                  event.stopPropagation();
                                  if (confirmDelete(`lease #${lease.id}`)) {
                                    void withStatus(() => api.deleteLease(lease.id).then(loadLandlordData), "Lease deleted.");
                                  }
                                }}
                              >
                                Delete
                              </button>
                            </div>
                          </div>

                          <div className="lease-summary">
                            <div>
                              <span>Period</span>
                              <strong>
                                {lease.start_date} to {lease.end_date}
                              </strong>
                            </div>
                            <div>
                              <span>Monthly Rent</span>
                              <strong>${lease.monthly_rent}</strong>
                            </div>
                            <div>
                              <span>Tenants</span>
                              <strong>{lease.tenants.length}</strong>
                            </div>
                            <button
                              className="link-button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setExpandedLeaseId(expanded ? null : lease.id);
                              }}
                            >
                              {expanded ? "Hide details" : "View details"}
                            </button>
                          </div>

                          {expanded && (
                            <div className="lease-expanded">
                              <div className="lease-details">
                                <div className="detail-item">
                                  <span>Property</span>
                                  <strong>{propertyNameById.get(lease.property_id) ?? `Property #${lease.property_id}`}</strong>
                                </div>
                                <div className="detail-item">
                                  <span>Unit</span>
                                  <strong>{lease.unit_id ? (unitNameById.get(lease.unit_id) ?? `Unit #${lease.unit_id}`) : "Whole property"}</strong>
                                </div>
                                <div className="detail-item">
                                  <span>Lease Period</span>
                                  <strong>
                                    {lease.start_date} to {lease.end_date}
                                  </strong>
                                </div>
                                <div className="detail-item">
                                  <span>Notes</span>
                                  <strong>{lease.notes || "-"}</strong>
                                </div>
                              </div>

                              <div className="lease-tenants">
                                <h4>Tenants</h4>
                                <div className="tenant-summary-list">
                                  {lease.tenants.map((tenant) => {
                                    const name = tenantNameById.get(tenant.tenant_id) ?? `Tenant #${tenant.tenant_id}`;
                                    return (
                                      <div className="tenant-summary" key={tenant.tenant_id}>
                                        <strong>{name}</strong>
                                        <span>Rent share: ${tenant.monthly_rent}</span>
                                        <span>Deposit: ${tenant.deposit}</span>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            </div>
                          )}
                        </article>
                      );
                    })}
                  </div>
                )}
              </section>

              {leaseModalOpen && (
                <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="lease-modal-title">
                  <form onSubmit={submitLease} className="card form modal-card">
                    <div className="card-header">
                      <h2 id="lease-modal-title">{editingLeaseId ? "Edit Lease" : "Add Lease"}</h2>
                      <button type="button" className="secondary" onClick={closeLeaseModal}>
                        Close
                      </button>
                    </div>
                    <label>
                      Property
                      <select
                        required
                        value={leaseForm.property_id}
                        onChange={(event) => setLeaseForm({ ...leaseForm, property_id: event.target.value, unit_id: "" })}
                      >
                        <option value="">Select property</option>
                        {properties.map((property) => (
                          <option key={property.id} value={property.id}>
                            {property.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Unit
                      <select
                        value={leaseForm.unit_id}
                        disabled={!leaseForm.property_id || unitsForLeaseProperty.length === 0}
                        onChange={(event) => setLeaseForm({ ...leaseForm, unit_id: event.target.value })}
                      >
                        <option value="">
                          {unitsForLeaseProperty.length === 0 ? "No units for selected property" : "Whole property / no unit"}
                        </option>
                        {unitsForLeaseProperty.map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {unit.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Tenants not bound to another lease
                      <div className="dropdown">
                        <button
                          type="button"
                          className="dropdown-trigger"
                          onClick={() => setTenantDropdownOpen((current) => !current)}
                        >
                          {selectedLeaseTenants.length
                            ? `${selectedLeaseTenants.length} tenant${selectedLeaseTenants.length > 1 ? "s" : ""} selected`
                            : "Choose tenants"}
                          <span aria-hidden="true">▾</span>
                        </button>
                        {tenantDropdownOpen && (
                          <div className="dropdown-menu">
                            {availableLeaseTenants.length === 0 ? (
                              <p className="dropdown-empty">No available tenants. Add a tenant or free one from another lease.</p>
                            ) : (
                              availableLeaseTenants.map((tenant) => {
                                const value = String(tenant.id);
                                return (
                                  <label key={tenant.id} className="dropdown-option">
                                    <input
                                      type="checkbox"
                                      checked={leaseForm.tenants.some((term) => term.tenant_id === value)}
                                      onChange={() => toggleLeaseTenant(tenant.id)}
                                    />
                                    <span>
                                      #{tenant.id} - {tenant.full_name}
                                      {tenant.email ? ` (${tenant.email})` : ""}
                                    </span>
                                  </label>
                                );
                              })
                            )}
                          </div>
                        )}
                        {selectedLeaseTenants.length > 0 && (
                          <div className="selected-chips">
                            {selectedLeaseTenants.map((tenant) => (
                              <button key={tenant.id} type="button" onClick={() => toggleLeaseTenant(tenant.id)}>
                                {tenant.full_name} ×
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <span className="help-text">
                        Tenants already used by another lease are hidden. Click a selected chip to remove it, then enter deposit.
                      </span>
                    </label>
                    {selectedLeaseTenants.length > 0 && (
                      <div className="tenant-terms">
                        <h3>Tenant deposit</h3>
                        {selectedLeaseTenants.map((tenant) => {
                          const terms = leaseForm.tenants.find((term) => term.tenant_id === String(tenant.id));
                          return (
                            <div className="tenant-term-row" key={tenant.id}>
                              <span>{tenant.full_name}</span>
                              <input
                                required
                                type="number"
                                min="0"
                                step="0.01"
                                placeholder="Deposit"
                                value={terms?.deposit ?? ""}
                                onChange={(event) => updateLeaseTenantDeposit(tenant.id, event.target.value)}
                              />
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div className="two-columns">
                      <label>
                        Start date
                        <input
                          required
                          type="date"
                          value={leaseForm.start_date}
                          onChange={(event) => setLeaseForm({ ...leaseForm, start_date: event.target.value })}
                        />
                      </label>
                      <label>
                        End date
                        <input
                          required
                          type="date"
                          value={leaseForm.end_date}
                          onChange={(event) => setLeaseForm({ ...leaseForm, end_date: event.target.value })}
                        />
                      </label>
                    </div>
                    <label>
                      Monthly rent
                      <input
                        required
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={leaseForm.monthly_rent}
                        onChange={(event) => setLeaseForm({ ...leaseForm, monthly_rent: event.target.value })}
                      />
                    </label>
                    <label>
                      Notes
                      <textarea value={leaseForm.notes} onChange={(event) => setLeaseForm({ ...leaseForm, notes: event.target.value })} />
                    </label>
                    <div className="modal-actions">
                      <button disabled={loading} className="primary">
                        {editingLeaseId ? "Save Lease" : "Add Lease"}
                      </button>
                      <button type="button" className="secondary" onClick={closeLeaseModal}>
                        Cancel
                      </button>
                    </div>
                  </form>
                </div>
              )}
            </>
          )}

          {activeTab === "bills" && (
            <section className="card">
              <div className="card-header">
                <h2>Rental & Payments</h2>
                <div className="header-actions">
                  <label className="inline-checkbox">
                    <input
                      type="checkbox"
                      checked={showHistoricPaidBills}
                      onChange={(event) => setShowHistoricPaidBills(event.target.checked)}
                    />
                    Show historic paid rental
                  </label>
                  <button className="primary" disabled={loading} onClick={generateBills}>
                    Generate Current Rental
                  </button>
                </div>
              </div>
              {!showHistoricPaidBills && hiddenPaidBillCount > 0 && (
                <p className="help-text">{hiddenPaidBillCount} paid rental record(s) hidden. Enable "Show historic paid rental" to view them.</p>
              )}
              {selectedPaymentTenant && (
                <section className="payment-history">
                  <div className="card-header">
                    <div>
                      <h3>{selectedPaymentTenant.full_name} payment history</h3>
                      <p className="help-text">All recorded payments for this tenant.</p>
                    </div>
                    <button className="secondary" onClick={() => setSelectedPaymentTenantId(null)}>
                      Close
                    </button>
                  </div>
                  <DataTable empty="No payment history for this tenant yet.">
                    {selectedTenantPayments.map((payment) => {
                      const paymentBill = billById.get(payment.bill_id);
                      return (
                        <tr key={payment.id}>
                          <td>#{payment.id}</td>
                          <td>{new Date(payment.paid_at).toLocaleString()}</td>
                          <td>${payment.amount}</td>
                          <td>
                            {paymentBill
                              ? `${paymentBill.bill_year}-${String(paymentBill.bill_month).padStart(2, "0")}`
                              : `Rental #${payment.bill_id}`}
                          </td>
                          <td>{payment.note || "-"}</td>
                        </tr>
                      );
                    })}
                  </DataTable>
                </section>
              )}
              <DataTable empty="No rental records to show. Generate rental after creating an active lease or enable historic paid rental.">
                {visibleBills.map((bill) => {
                  const payment = paymentByBill[bill.id] ?? { amount: "", paid_at: "", note: "" };
                  return (
                    <tr key={bill.id}>
                      <td>#{bill.id}</td>
                      <td>
                        <button className="link-button" onClick={() => setSelectedPaymentTenantId(bill.tenant_id)}>
                          {tenantNameById.get(bill.tenant_id) ?? `Tenant #${bill.tenant_id}`}
                        </button>
                      </td>
                      <td>
                        {bill.bill_year}-{String(bill.bill_month).padStart(2, "0")}
                      </td>
                      <td>${bill.amount_due}</td>
                      <td>${bill.amount_paid}</td>
                      <td>${bill.balance}</td>
                      <td>
                        <span className={`status ${bill.status}`}>{bill.status}</span>
                      </td>
                      <td className="payment-form">
                        <input
                          type="number"
                          min="0.01"
                          step="0.01"
                          placeholder="Amount"
                          value={payment.amount}
                          onChange={(event) =>
                            setPaymentByBill((current) => ({
                              ...current,
                              [bill.id]: { ...payment, amount: event.target.value },
                            }))
                          }
                        />
                        <input
                          type="datetime-local"
                          value={payment.paid_at}
                          onChange={(event) =>
                            setPaymentByBill((current) => ({
                              ...current,
                              [bill.id]: { ...payment, paid_at: event.target.value },
                            }))
                          }
                        />
                        <input
                          placeholder="Note"
                          value={payment.note}
                          onChange={(event) =>
                            setPaymentByBill((current) => ({
                              ...current,
                              [bill.id]: { ...payment, note: event.target.value },
                            }))
                          }
                        />
                        <button disabled={loading || !payment.amount} onClick={() => recordPayment(bill.id)}>
                          Pay
                        </button>
                        <button
                          onClick={() =>
                            confirmDelete(`rental record #${bill.id}`) &&
                            withStatus(() => api.deleteBill(bill.id).then(loadLandlordData), "Rental record deleted.")
                          }
                        >
                          Delete Rental
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </DataTable>
            </section>
          )}
        </>
      )}
    </main>
  );
}

function Status({ error, message }: { error: string; message: string }) {
  if (!error && !message) {
    return null;
  }
  return <div className={error ? "alert error" : "alert success"}>{error || message}</div>;
}

function DataTable({ children, empty }: { children: ReactNode; empty: string }) {
  const rows = Array.isArray(children) ? children.filter(Boolean) : children;
  if (Array.isArray(rows) && rows.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export default App;
