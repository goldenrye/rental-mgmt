import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Bill,
  Lease,
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
  tenant_ids: string[];
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
  tenant_ids: [],
  start_date: "",
  end_date: "",
  monthly_rent: "",
  notes: "",
};

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
  const [propertyForm, setPropertyForm] = useState<PropertyForm>(emptyProperty);
  const [unitForm, setUnitForm] = useState<UnitForm>(emptyUnit);
  const [tenantForm, setTenantForm] = useState<TenantForm>(emptyTenant);
  const [leaseForm, setLeaseForm] = useState<LeaseForm>(emptyLease);
  const [editingPropertyId, setEditingPropertyId] = useState<number | null>(null);
  const [editingUnitId, setEditingUnitId] = useState<number | null>(null);
  const [editingTenantId, setEditingTenantId] = useState<number | null>(null);
  const [editingLeaseId, setEditingLeaseId] = useState<number | null>(null);
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
  const availableLeaseTenants = useMemo(() => {
    const assignedTenantIds = new Set(
      leases
        .filter((lease) => lease.id !== editingLeaseId)
        .flatMap((lease) => lease.tenant_ids),
    );
    const selectedTenantIds = new Set(leaseForm.tenant_ids.map(Number));
    return tenants.filter((tenant) => !assignedTenantIds.has(tenant.id) || selectedTenantIds.has(tenant.id));
  }, [editingLeaseId, leaseForm.tenant_ids, leases, tenants]);
  const selectedLeaseTenants = useMemo(
    () => tenants.filter((tenant) => leaseForm.tenant_ids.includes(String(tenant.id))),
    [leaseForm.tenant_ids, tenants],
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

  async function loadLandlordData() {
    const [nextProperties, nextUnits, nextTenants, nextLeases, nextBills] = await Promise.all([
      api.listProperties(),
      api.listUnits(),
      api.listTenants(),
      api.listLeases(),
      api.listBills(),
    ]);
    setProperties(nextProperties);
    setUnits(nextUnits);
    setTenants(nextTenants);
    setLeases(nextLeases);
    setBills(nextBills);
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
    const tenantIds = leaseForm.tenant_ids.map(Number).filter(Boolean);
    if (tenantIds.length === 0) {
      setError("Please choose at least one tenant for the lease.");
      setMessage("");
      return;
    }
    await withStatus(async () => {
      const payload = {
        property_id: Number(leaseForm.property_id),
        unit_id: leaseForm.unit_id ? Number(leaseForm.unit_id) : null,
        tenant_ids: tenantIds,
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
      setTenantDropdownOpen(false);
      await loadLandlordData();
    }, editingLeaseId ? "Lease updated." : "Lease added.");
  }

  function toggleLeaseTenant(tenantId: number) {
    const value = String(tenantId);
    setLeaseForm((current) => ({
      ...current,
      tenant_ids: current.tenant_ids.includes(value)
        ? current.tenant_ids.filter((id) => id !== value)
        : [...current.tenant_ids, value],
    }));
  }

  async function generateBills() {
    await withStatus(async () => {
      await api.generateCurrentBills();
      setBills(await api.listBills());
    }, "Bills generated for currently active leases.");
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
      setBills(await api.listBills());
    }, "Payment recorded and balance updated.");
  }

  if (!user) {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">Rental Management</p>
          <h1>Manage leases, bills, and payments</h1>
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
          <p className="muted">Tenant bill viewing is available through the backend API at /tenant/bills.</p>
        </section>
      ) : (
        <>
          <nav className="tabs">
            {(["properties", "units", "tenants", "leases", "bills"] as Tab[]).map((tab) => (
              <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
                {tab}
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
            <section className="grid">
              <form onSubmit={submitLease} className="card form">
                <h2>{editingLeaseId ? "Edit Lease" : "Add Lease"}</h2>
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
                                  checked={leaseForm.tenant_ids.includes(value)}
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
                    Tenants already used by another lease are hidden. Click a selected chip to remove it.
                  </span>
                </label>
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
                <button disabled={loading} className="primary">
                  {editingLeaseId ? "Save Lease" : "Add Lease"}
                </button>
              </form>
              <section className="card">
                <h2>Leases</h2>
                <DataTable empty="No leases yet.">
                  {leases.map((lease) => (
                    <tr key={lease.id}>
                      <td>#{lease.id}</td>
                      <td>{propertyNameById.get(lease.property_id) ?? `Property #${lease.property_id}`}</td>
                      <td>{lease.unit_id ? (unitNameById.get(lease.unit_id) ?? `Unit #${lease.unit_id}`) : "Whole property"}</td>
                      <td>{lease.tenant_ids.map((id) => tenantNameById.get(id) ?? `#${id}`).join(", ")}</td>
                      <td>
                        {lease.start_date} to {lease.end_date}
                      </td>
                      <td>${lease.monthly_rent}</td>
                      <td className="actions">
                        <button
                          onClick={() => {
                            setEditingLeaseId(lease.id);
                            setLeaseForm({
                              property_id: String(lease.property_id),
                              unit_id: lease.unit_id ? String(lease.unit_id) : "",
                              tenant_ids: lease.tenant_ids.map(String),
                              start_date: lease.start_date,
                              end_date: lease.end_date,
                              monthly_rent: lease.monthly_rent,
                              notes: lease.notes ?? "",
                            });
                          }}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() =>
                            confirmDelete(`lease #${lease.id}`) &&
                            withStatus(() => api.deleteLease(lease.id).then(loadLandlordData), "Lease deleted.")
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

          {activeTab === "bills" && (
            <section className="card">
              <div className="card-header">
                <h2>Bills & Payments</h2>
                <button className="primary" disabled={loading} onClick={generateBills}>
                  Generate Current Bills
                </button>
              </div>
              <DataTable empty="No bills yet. Generate bills after creating an active lease.">
                {bills.map((bill) => {
                  const payment = paymentByBill[bill.id] ?? { amount: "", paid_at: "", note: "" };
                  return (
                    <tr key={bill.id}>
                      <td>#{bill.id}</td>
                      <td>{tenantNameById.get(bill.tenant_id) ?? `Tenant #${bill.tenant_id}`}</td>
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
                            confirmDelete(`bill #${bill.id}`) &&
                            withStatus(() => api.deleteBill(bill.id).then(loadLandlordData), "Bill deleted.")
                          }
                        >
                          Delete Bill
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
