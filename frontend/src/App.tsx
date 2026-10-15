import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Bill,
  Lease,
  Payment,
  PropertyBill,
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
import { PropertyBillFilterDialog } from "./PropertyBillFilterDialog";
import { TenantChargeDialog } from "./TenantChargeDialog";
import { ExtendBillDialog } from "./ExtendBillDialog";
import { PaymentDialog } from "./PaymentDialog";
import { DataTable } from "./PaginatedEntries";
import { LeaseDetailsDialog } from "./LeaseDetailsDialog";
import { billCategoryLabels, billRecurrenceLabels, emptyPropertyBillFilters, filterPropertyBills } from "./propertyBillFilters";
import type { PropertyBillFilters } from "./propertyBillFilters";
import "./styles.css";

function BillActionIcon({ name }: { name: "details" | "edit" | "paid" | "unpaid" | "extend" | "generate" | "delete" }) {
  const paths: Record<typeof name, ReactNode> = {
    details: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>,
    edit: <><path d="m16 3 5 5-12 12-6 1 1-6Z" /><path d="m14 5 5 5" /></>,
    paid: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
    unpaid: <><path d="M3 4v6h6" /><path d="M3 10a9 9 0 1 1 2 8" /></>,
    extend: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M7 3v4m10-4v4M3 11h18m-9 3v4m-2-2h4" /></>,
    generate: <><path d="M14 2H5v20h14V7Z" /><path d="M14 2v5h5m-7 5v6m-3-3h6" /></>,
    delete: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7" /></>,
  };
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {paths[name]}
    </svg>
  );
}

type Tab = "properties" | "tenants" | "leases" | "bills" | "bill";
const tabLabels: Record<Tab, string> = {
  properties: "Properties & Units",
  tenants: "tenants",
  leases: "leases",
  bills: "rental",
  bill: "bill",
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

type ManualBillForm = {
  property_id: string;
  bill_type: "electricity" | "water" | "mortgage" | "insurance" | "property_tax" | "maintenance" | "other";
  title: string;
  amount_due: string;
  recurrence: "one_time" | "monthly" | "bi_monthly" | "quarterly" | "semi_annually" | "annually" | "custom_period";
  due_date: string;
  period_start: string;
  period_end: string;
  notes: string;
};

type ProfileForm = {
  full_name: string;
  display_name: string;
  current_password: string;
  new_password: string;
  confirm_password: string;
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
const emptyManualBill: ManualBillForm = {
  property_id: "",
  bill_type: "electricity",
  title: "",
  amount_due: "",
  recurrence: "one_time",
  due_date: "",
  period_start: "",
  period_end: "",
  notes: "",
};
const emptyProfileForm: ProfileForm = {
  full_name: "",
  display_name: "",
  current_password: "",
  new_password: "",
  confirm_password: "",
};

function formatDateInput(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentMonthRange() {
  const today = new Date();
  return {
    start: formatDateInput(new Date(today.getFullYear(), today.getMonth(), 1)),
    end: formatDateInput(new Date(today.getFullYear(), today.getMonth() + 1, 0)),
  };
}

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
  const [propertyBills, setPropertyBills] = useState<PropertyBill[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [showHistoricPaidBills, setShowHistoricPaidBills] = useState(false);
  const [billFilter, setBillFilter] = useState<PropertyBillFilters>(() => ({ ...emptyPropertyBillFilters, ...currentMonthRange() }));
  const [billFilterOpen, setBillFilterOpen] = useState(false);
  const billFilterButtonRef = useRef<HTMLButtonElement>(null);
  const [propertyForm, setPropertyForm] = useState<PropertyForm>(emptyProperty);
  const [unitForm, setUnitForm] = useState<UnitForm>(emptyUnit);
  const [tenantForm, setTenantForm] = useState<TenantForm>(emptyTenant);
  const [leaseForm, setLeaseForm] = useState<LeaseForm>(emptyLease);
  const [manualBillForm, setManualBillForm] = useState<ManualBillForm>(emptyManualBill);
  const [profileForm, setProfileForm] = useState<ProfileForm>(emptyProfileForm);
  const [editingPropertyId, setEditingPropertyId] = useState<number | null>(null);
  const [propertyModalOpen, setPropertyModalOpen] = useState(false);
  const propertyDialogRef = useRef<HTMLDialogElement>(null);
  const [editingUnitId, setEditingUnitId] = useState<number | null>(null);
  const [unitModalOpen, setUnitModalOpen] = useState(false);
  const unitDialogRef = useRef<HTMLDialogElement>(null);
  const [editingTenantId, setEditingTenantId] = useState<number | null>(null);
  const [tenantModalOpen, setTenantModalOpen] = useState(false);
  const tenantDialogRef = useRef<HTMLDialogElement>(null);
  const [editingLeaseId, setEditingLeaseId] = useState<number | null>(null);
  const [leaseModalOpen, setLeaseModalOpen] = useState(false);
  const [billModalOpen, setBillModalOpen] = useState(false);
  const [extensionBill, setExtensionBill] = useState<PropertyBill | null>(null);
  const [billEdit, setBillEdit] = useState<Pick<PropertyBill, "id" | "title" | "amount_due" | "due_date"> | null>(null);
  const [tenantChargeBillId, setTenantChargeBillId] = useState<number | null>(null);
  const tenantChargeTrigger = useRef<HTMLButtonElement | null>(null);
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const [leaseDetailsId, setLeaseDetailsId] = useState<number | null>(null);
  const [expandedPropertyUnits, setExpandedPropertyUnits] = useState<Record<number, boolean>>({});
  const [selectedPaymentTenantId, setSelectedPaymentTenantId] = useState<number | null>(null);
  const [selectedPaymentBillId, setSelectedPaymentBillId] = useState<number | null>(null);
  const [tenantDropdownOpen, setTenantDropdownOpen] = useState(false);
  const [paymentByBill, setPaymentByBill] = useState<Record<number, { amount: string; paid_at: string; note: string }>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!profileMenuOpen) return;
    function closeOnOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !profileMenuRef.current?.contains(event.target)) {
        setProfileMenuOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
  }, [profileMenuOpen]);

  useEffect(() => {
    if (!propertyModalOpen) return;
    const dialog = propertyDialogRef.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, [propertyModalOpen]);

  useEffect(() => {
    if (!unitModalOpen) return;
    const dialog = unitDialogRef.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    dialog?.querySelector<HTMLInputElement>("input:not([readonly])")?.focus();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, [unitModalOpen]);

  useEffect(() => {
    if (!tenantModalOpen) return;
    const dialog = tenantDialogRef.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, [tenantModalOpen]);

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
  const leaseDetails = leases.find((lease) => lease.id === leaseDetailsId);
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
  const visiblePropertyBills = useMemo(
    () => filterPropertyBills(propertyBills, billFilter),
    [billFilter, propertyBills],
  );
  const activeBillFilters = [
    billFilter.start && `Due from: ${billFilter.start}`,
    billFilter.end && `Due through: ${billFilter.end}`,
    billFilter.due_date && `Due on: ${billFilter.due_date}`,
    billFilter.bill_type && `Category: ${billCategoryLabels[billFilter.bill_type]}`,
    billFilter.property_id && `Property: ${propertyNameById.get(Number(billFilter.property_id)) ?? `#${billFilter.property_id}`}`,
    billFilter.status && `Status: ${billFilter.status}`,
    billFilter.recurrence && `Repeat: ${billRecurrenceLabels[billFilter.recurrence]}`,
    billFilter.tenant_bills && `Tenant bills: ${billFilter.tenant_bills === "generated" ? "Generated" : "Not generated"}`,
  ].filter(Boolean);

  function closeBillFilters() {
    setBillFilterOpen(false);
    billFilterButtonRef.current?.focus();
  }

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

  function openBillModal() {
    setManualBillForm(emptyManualBill);
    setBillModalOpen(true);
  }

  function closeBillModal() {
    setBillModalOpen(false);
    setManualBillForm(emptyManualBill);
  }

  async function loadLandlordData() {
    const [nextProperties, nextUnits, nextTenants, nextLeases, nextBills, nextPropertyBills, nextPayments] = await Promise.all([
      api.listProperties(),
      api.listUnits(),
      api.listTenants(),
      api.listLeases(),
      api.listBills(),
      api.listPropertyBills(),
      api.listPayments(),
    ]);
    setProperties(nextProperties);
    setUnits(nextUnits);
    setTenants(nextTenants);
    setLeases(nextLeases);
    setBills(nextBills);
    setPropertyBills(nextPropertyBills);
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
    setPropertyBills([]);
    setPayments([]);
    setManualBillForm(emptyManualBill);
    setSelectedPaymentTenantId(null);
    setSelectedPaymentBillId(null);
    setLeaseDetailsId(null);
    setExpandedPropertyUnits({});
    setLeaseModalOpen(false);
    setBillModalOpen(false);
    setPropertyModalOpen(false);
    setUnitModalOpen(false);
    setTenantModalOpen(false);
    setProfileModalOpen(false);
    setBillEdit(null);
    setExtensionBill(null);
    setTenantChargeBillId(null);
    setBillFilterOpen(false);
    setBillFilter({ ...emptyPropertyBillFilters, ...currentMonthRange() });
    setProfileMenuOpen(false);
    setTenantDropdownOpen(false);
    setMessage("Signed out.");
  }

  function openProfileModal() {
    setProfileForm({
      full_name: user?.full_name ?? "",
      display_name: user?.display_name ?? "",
      current_password: "",
      new_password: "",
      confirm_password: "",
    });
    setProfileMenuOpen(false);
    setProfileModalOpen(true);
  }

  function closeProfileModal() {
    setProfileModalOpen(false);
    setProfileForm(emptyProfileForm);
  }

  async function submitProfileUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (profileForm.new_password || profileForm.confirm_password || profileForm.current_password) {
      if (profileForm.new_password !== profileForm.confirm_password) {
        setError("New password and confirmation do not match.");
        setMessage("");
        return;
      }
      if (profileForm.new_password.length < 8) {
        setError("New password must be at least 8 characters.");
        setMessage("");
        return;
      }
      if (!profileForm.current_password) {
        setError("Current password is required to change password.");
        setMessage("");
        return;
      }
    }
    if (!profileForm.full_name.trim()) {
      setError("Full name is required.");
      setMessage("");
      return;
    }
    await withStatus(async () => {
      const updatedUser = await api.updateProfile({
        full_name: profileForm.full_name.trim(),
        display_name: profileForm.display_name.trim() || null,
        current_password: profileForm.new_password ? profileForm.current_password : undefined,
        new_password: profileForm.new_password || undefined,
      });
      setUser(updatedUser);
      closeProfileModal();
    }, "Profile updated.");
  }

  async function submitProperty(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
    await withStatus(async () => {
      if (editingPropertyId) {
        await api.updateProperty(editingPropertyId, propertyForm);
      } else {
        await api.createProperty(propertyForm);
      }
      setPropertyForm(emptyProperty);
      setEditingPropertyId(null);
      setPropertyModalOpen(false);
      await loadLandlordData();
    }, editingPropertyId ? "Property updated." : "Property added.");
  }

  async function submitUnit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
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
      setUnitModalOpen(false);
      await loadLandlordData();
    }, editingUnitId ? "Unit updated." : "Unit added.");
  }

  async function submitTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
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
      setTenantModalOpen(false);
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

  async function submitManualBill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await withStatus(async () => {
      const recurring = manualBillForm.recurrence !== "one_time";
      const createdBills = await api.createPropertyBills({
        property_id: Number(manualBillForm.property_id),
        bill_type: manualBillForm.bill_type,
        title: manualBillForm.title,
        amount_due: manualBillForm.amount_due,
        recurrence: manualBillForm.recurrence,
        due_date: manualBillForm.due_date,
        period_start: recurring ? manualBillForm.period_start : undefined,
        period_end: recurring ? manualBillForm.period_end : undefined,
        notes: manualBillForm.notes || undefined,
      });
      setManualBillForm(emptyManualBill);
      setBillModalOpen(false);
      setPropertyBills(await api.listPropertyBills());
      setMessage(`${createdBills.length} property bill${createdBills.length === 1 ? "" : "s"} created.`);
    });
  }

  async function updatePropertyBillStatus(bill: PropertyBill, status: "unpaid" | "paid") {
    await withStatus(async () => {
      const updatedBill = await api.updatePropertyBill(bill.id, { status });
      setPropertyBills((current) => current.map((item) => (item.id === bill.id ? updatedBill : item)));
    }, status === "paid" ? "Bill marked as paid." : "Bill marked as unpaid.");
  }

  async function submitBillEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!billEdit || loading) {
      return;
    }
    const { id, amount_due, due_date } = billEdit;
    await withStatus(async () => {
      const updatedBill = await api.updatePropertyBill(id, { amount_due, due_date });
      setPropertyBills((current) => current.map((bill) => (bill.id === id ? updatedBill : bill)));
      setBillEdit(null);
    }, "Bill updated. Bills that do not match the active filters are hidden.");
  }

  function closeTenantChargeDialog() {
    setTenantChargeBillId(null);
    tenantChargeTrigger.current?.focus();
  }

  function tenantChargesGenerated(generatedBills: Bill[]) {
    setBills((current) => [...current.filter((bill) => !generatedBills.some((created) => created.id === bill.id)), ...generatedBills]);
    setPropertyBills((current) => current.map((bill) =>
      bill.id === tenantChargeBillId ? { ...bill, generated_at: new Date().toISOString() } : bill,
    ));
    setError("");
    setMessage(`${generatedBills.length} tenant charge${generatedBills.length === 1 ? "" : "s"} generated. View them in Rental & Payments.`);
    closeTenantChargeDialog();
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
      setSelectedPaymentBillId(null);
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

  const profileDisplayName = user.display_name || user.full_name;
  const profileInitial = profileDisplayName.charAt(0).toUpperCase();

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="header-greeting">
          <p className="eyebrow">Rental Management</p>
          <h1>Welcome, {profileDisplayName}</h1>
          <p className="muted">{user.role === "landlord" ? "Landlord dashboard" : "Tenant account"}</p>
        </div>
        <div className="profile-menu" ref={profileMenuRef}>
          <button
            type="button"
            className="profile-trigger"
            aria-label="Open profile menu"
            aria-expanded={profileMenuOpen}
            aria-controls={profileMenuOpen ? "profile-panel" : undefined}
            title="Profile"
            onClick={() => setProfileMenuOpen((isOpen) => !isOpen)}
          >
            <span className="profile-icon" aria-hidden="true">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
                <circle cx="12" cy="8" r="4" />
                <path d="M4 21v-2a8 8 0 0 1 16 0v2" />
              </svg>
            </span>
          </button>

          {profileMenuOpen && (
            <section id="profile-panel" className="profile-panel card">
              <div className="profile-panel-header">
                <span className="profile-icon large" aria-hidden="true">
                  {profileInitial}
                </span>
                <div>
                  <h2>{profileDisplayName}</h2>
                  <p className="muted">{user.email}</p>
                </div>
              </div>

              <div className="detail-grid compact">
                <div>
                  <span>Full name</span>
                  <strong>{user.full_name}</strong>
                </div>
                <div>
                  <span>Display name</span>
                  <strong>{user.display_name || "Not set"}</strong>
                </div>
                <div>
                  <span>Role</span>
                  <strong>{user.role}</strong>
                </div>
              </div>

              <button type="button" onClick={openProfileModal} className="primary logout-button">
                Edit Profile
              </button>
              <button onClick={logout} className="secondary logout-button">
                Logout
              </button>
            </section>
          )}
        </div>
      </header>

      {profileModalOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title">
          <form onSubmit={submitProfileUpdate} className="card form modal-card">
            <div className="card-header">
              <h2 id="profile-modal-title">Edit Profile</h2>
              <button type="button" className="secondary" onClick={closeProfileModal}>
                Close
              </button>
            </div>
            <label>
              Full name
              <input
                required
                maxLength={255}
                value={profileForm.full_name}
                onChange={(event) => setProfileForm({ ...profileForm, full_name: event.target.value })}
              />
            </label>
            <label>
              Display name
              <input
                maxLength={255}
                placeholder="Name shown in the app"
                value={profileForm.display_name}
                onChange={(event) => setProfileForm({ ...profileForm, display_name: event.target.value })}
              />
            </label>
            <div className="form-section">
              <h3>Change password</h3>
              <p className="muted">Leave password fields blank if you only want to update your names.</p>
              <label>
                Current password
                <input
                  type="password"
                  value={profileForm.current_password}
                  onChange={(event) => setProfileForm({ ...profileForm, current_password: event.target.value })}
                />
              </label>
              <label>
                New password
                <input
                  minLength={8}
                  maxLength={72}
                  type="password"
                  value={profileForm.new_password}
                  onChange={(event) => setProfileForm({ ...profileForm, new_password: event.target.value })}
                />
              </label>
              <label>
                Confirm new password
                <input
                  minLength={8}
                  maxLength={72}
                  type="password"
                  value={profileForm.confirm_password}
                  onChange={(event) => setProfileForm({ ...profileForm, confirm_password: event.target.value })}
                />
              </label>
            </div>
            <div className="modal-actions">
              <button disabled={loading} className="primary">
                Save Profile
              </button>
              <button type="button" className="secondary" onClick={closeProfileModal}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <Status error={error} message={message} />

      {user.role !== "landlord" ? (
        <section className="card">
          <h2>Tenant Portal</h2>
          <p className="muted">Tenant rental viewing is available through the backend API at /tenant/bills.</p>
        </section>
      ) : (
        <>
          <nav className="tabs">
            {(["properties", "tenants", "leases", "bills", "bill"] as Tab[]).map((tab) => (
              <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
                {tabLabels[tab]}
              </button>
            ))}
          </nav>

          {activeTab === "properties" && (
            <div className="property-unit-page">
            {propertyModalOpen && (
              <dialog
                ref={propertyDialogRef}
                className="card property-dialog"
                aria-labelledby="property-dialog-title"
                onCancel={(event) => {
                  event.preventDefault();
                  if (!loading) setPropertyModalOpen(false);
                }}
              >
              <form onSubmit={submitProperty} className="form" aria-busy={loading}>
                <h2 id="property-dialog-title">{editingPropertyId ? "Edit Property" : "Add Property"}</h2>
                {error && <p className="alert error" role="alert">{error}</p>}
                <label>
                  Name
                  <input
                    autoFocus
                    disabled={loading}
                    required
                    value={propertyForm.name}
                    onChange={(event) => setPropertyForm({ ...propertyForm, name: event.target.value })}
                  />
                </label>
                <label>
                  Address
                  <input
                    disabled={loading}
                    required
                    value={propertyForm.address}
                    onChange={(event) => setPropertyForm({ ...propertyForm, address: event.target.value })}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    disabled={loading}
                    value={propertyForm.description}
                    onChange={(event) => setPropertyForm({ ...propertyForm, description: event.target.value })}
                  />
                </label>
                <div className="modal-actions">
                  <button type="button" className="secondary" disabled={loading} onClick={() => setPropertyModalOpen(false)}>
                    Cancel
                  </button>
                  <button disabled={loading} className="primary">
                    {loading ? "Saving..." : editingPropertyId ? "Save Property" : "Add Property"}
                  </button>
                </div>
              </form>
              </dialog>
            )}
              <section className="card">
                <div className="card-header">
                  <h2>Properties</h2>
                  <button
                    className="primary"
                    disabled={loading}
                    aria-haspopup="dialog"
                    onClick={() => {
                      setEditingPropertyId(null);
                      setPropertyForm(emptyProperty);
                      setError("");
                      setPropertyModalOpen(true);
                    }}
                  >
                    Add Property
                  </button>
                </div>
                <DataTable label="Properties" empty="No properties yet." headers={["Property", "Address", "Description", "Actions"]}>
                  {properties.map((property) => {
                    const propertyUnits = units.filter((unit) => unit.property_id === property.id);
                    const expanded = Boolean(expandedPropertyUnits[property.id]);
                    const toggleUnits = () => setExpandedPropertyUnits((current) => ({
                      ...current,
                      [property.id]: !current[property.id],
                    }));
                    return (
                    <Fragment key={property.id}>
                    <tr
                      className={propertyUnits.length > 0 ? `property-row expandable${expanded ? " expanded" : ""}` : "property-row"}
                      onClick={propertyUnits.length > 0 ? toggleUnits : undefined}
                    >
                      <td>
                          {propertyUnits.length > 0 ? (
                            <button
                              type="button"
                              className="property-expand-trigger"
                              aria-expanded={expanded}
                              aria-controls={`property-units-${property.id}`}
                              aria-label={`${expanded ? "Hide" : "Show"} units for ${property.name}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                toggleUnits();
                              }}
                            >
                              <span className="property-chevron" aria-hidden="true" />
                              <span>{property.name}</span>
                              <span className="property-unit-count">{propertyUnits.length} {propertyUnits.length === 1 ? "unit" : "units"}</span>
                            </button>
                          ) : <span>{property.name}</span>}
                      </td>
                      <td>{property.address}</td>
                      <td>{property.description || "-"}</td>
                      <td className="actions" onClick={(event) => event.stopPropagation()}>
                        <button
                          disabled={loading}
                          aria-haspopup="dialog"
                          aria-label={`Add unit to ${property.name}`}
                          onClick={() => {
                            setEditingUnitId(null);
                            setUnitForm({ ...emptyUnit, property_id: String(property.id) });
                            setError("");
                            setUnitModalOpen(true);
                          }}
                        >
                          Add Unit
                        </button>
                        <button
                          disabled={loading}
                          aria-haspopup="dialog"
                          onClick={() => {
                            setEditingPropertyId(property.id);
                            setPropertyForm({
                              name: property.name,
                              address: property.address,
                              description: property.description ?? "",
                            });
                            setError("");
                            setPropertyModalOpen(true);
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
                    {propertyUnits.length > 0 && (
                    <tr className="property-units-row" id={`property-units-${property.id}`} hidden={!expanded}>
                      <td colSpan={4}>
                        <section className="property-units" aria-label={`Units in ${property.name}`}>
                          <DataTable
                            label={`Units in ${property.name}`}
                            empty="No units in this property yet. Use Add Unit to create one."
                            headers={["Unit", "Description", "Actions"]}
                            className="compact-table"
                          >
                            {propertyUnits.map((unit) => (
                              <tr key={unit.id}>
                                <td>{unit.name}</td>
                                <td>{unit.description || "-"}</td>
                                <td>
                                  <div className="compact-row-actions">
                                    <button
                                      disabled={loading}
                                      aria-haspopup="dialog"
                                      aria-label={`Edit unit ${unit.name} in ${property.name}`}
                                      onClick={() => {
                                        setEditingUnitId(unit.id);
                                        setUnitForm({
                                          property_id: String(unit.property_id),
                                          name: unit.name,
                                          description: unit.description ?? "",
                                        });
                                        setError("");
                                        setUnitModalOpen(true);
                                      }}
                                    >
                                      Edit
                                    </button>
                                    <button
                                      disabled={loading}
                                      aria-label={`Delete unit ${unit.name} in ${property.name}`}
                                      onClick={() =>
                                        confirmDelete(`unit "${unit.name}"`) &&
                                        withStatus(() => api.deleteUnit(unit.id).then(loadLandlordData), "Unit deleted.")
                                      }
                                    >
                                      Delete
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </DataTable>
                        </section>
                      </td>
                    </tr>
                    )}
                    </Fragment>
                    );
                  })}
                </DataTable>
              </section>

            {unitModalOpen && (
              <dialog
                ref={unitDialogRef}
                className="card property-dialog"
                aria-labelledby="unit-dialog-title"
                onCancel={(event) => {
                  event.preventDefault();
                  if (!loading) setUnitModalOpen(false);
                }}
              >
              <form onSubmit={submitUnit} className="form" aria-busy={loading}>
                <h2 id="unit-dialog-title">{editingUnitId ? "Edit Unit" : "Add Unit"}</h2>
                {error && <p className="alert error" role="alert">{error}</p>}
                <label>
                  Property
                  <input readOnly value={propertyNameById.get(Number(unitForm.property_id)) ?? ""} />
                </label>
                <label>
                  Unit name
                  <input
                    autoFocus
                    disabled={loading}
                    required
                    placeholder="Example: Unit 101"
                    value={unitForm.name}
                    onChange={(event) => setUnitForm({ ...unitForm, name: event.target.value })}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    disabled={loading}
                    value={unitForm.description}
                    onChange={(event) => setUnitForm({ ...unitForm, description: event.target.value })}
                  />
                </label>
                <div className="modal-actions">
                  <button type="button" className="secondary" disabled={loading} onClick={() => setUnitModalOpen(false)}>
                    Cancel
                  </button>
                  <button disabled={loading} className="primary">
                    {loading ? "Saving..." : editingUnitId ? "Save Unit" : "Add Unit"}
                  </button>
                </div>
              </form>
              </dialog>
            )}
            </div>
          )}

          {activeTab === "tenants" && (
            <>
              {tenantModalOpen && (
              <dialog
                ref={tenantDialogRef}
                className="card tenant-dialog"
                aria-labelledby="tenant-dialog-title"
                onCancel={(event) => {
                  event.preventDefault();
                  if (!loading) setTenantModalOpen(false);
                }}
              >
              <form onSubmit={submitTenant} className="form" aria-busy={loading}>
                <h2 id="tenant-dialog-title">{editingTenantId ? "Edit Tenant" : "Add Tenant"}</h2>
                {error && <p className="alert error" role="alert">{error}</p>}
                <label>
                  Full name
                  <input
                    autoFocus
                    disabled={loading}
                    required
                    value={tenantForm.full_name}
                    onChange={(event) => setTenantForm({ ...tenantForm, full_name: event.target.value })}
                  />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    disabled={loading}
                    value={tenantForm.email}
                    onChange={(event) => setTenantForm({ ...tenantForm, email: event.target.value })}
                  />
                </label>
                <label>
                  Phone
                  <input disabled={loading} value={tenantForm.phone} onChange={(event) => setTenantForm({ ...tenantForm, phone: event.target.value })} />
                </label>
                <label>
                  Notes
                  <textarea disabled={loading} value={tenantForm.notes} onChange={(event) => setTenantForm({ ...tenantForm, notes: event.target.value })} />
                </label>
                <div className="modal-actions">
                  <button type="button" className="secondary" disabled={loading} onClick={() => setTenantModalOpen(false)}>
                    Cancel
                  </button>
                  <button disabled={loading} className="primary">
                    {loading ? "Saving..." : editingTenantId ? "Save Tenant" : "Add Tenant"}
                  </button>
                </div>
              </form>
              </dialog>
              )}
              <section className="card">
                <div className="card-header">
                  <h2>Tenants</h2>
                  <button
                    className="primary"
                    disabled={loading}
                    aria-haspopup="dialog"
                    onClick={() => {
                      setEditingTenantId(null);
                      setTenantForm(emptyTenant);
                      setError("");
                      setTenantModalOpen(true);
                    }}
                  >
                    Add Tenant
                  </button>
                </div>
                <DataTable label="Tenants" empty="No tenants yet." headers={["Tenant ID", "Full Name", "Email", "Phone", "Deposit Paid", "Actions"]} className="compact-table">
                  {tenants.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>#{tenant.id}</td>
                      <td>{tenant.full_name}</td>
                      <td>{tenant.email || "-"}</td>
                      <td>{tenant.phone || "-"}</td>
                      <td>${(tenantDepositPaidById.get(tenant.id) ?? 0).toFixed(2)}</td>
                      <td className="actions">
                        <button
                          disabled={loading}
                          aria-haspopup="dialog"
                          onClick={() => {
                            setEditingTenantId(tenant.id);
                            setTenantForm({
                              full_name: tenant.full_name,
                              email: tenant.email ?? "",
                              phone: tenant.phone ?? "",
                              notes: tenant.notes ?? "",
                            });
                            setError("");
                            setTenantModalOpen(true);
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
            </>
          )}

          {activeTab === "leases" && (
            <>
              <section className="card leases-section">
                <div className="card-header leases-header">
                  <div className="leases-heading">
                    <h2>Leases</h2>
                    <span className="muted">{leases.length} leases</span>
                  </div>
                  <button className="primary" onClick={openAddLeaseModal}>
                    Add Lease
                  </button>
                </div>
                <DataTable
                  label="Leases"
                  empty="No leases yet."
                  headers={["Lease ID", "Property", "Unit", "Start Date", "End Date", "Monthly Rent", "Tenants", "Notes", "Actions"]}
                  className="compact-table lease-table"
                >
                  {leases.map((lease) => {
                    const propertyName = propertyNameById.get(lease.property_id) ?? `Property #${lease.property_id}`;
                    const unitName = lease.unit_id ? (unitNameById.get(lease.unit_id) ?? `Unit #${lease.unit_id}`) : "Whole property";
                    const tenantNames = lease.tenants.map((tenant) => tenantNameById.get(tenant.tenant_id) ?? `Tenant #${tenant.tenant_id}`).join(", ");
                    return (
                      <tr key={lease.id}>
                        <td>#{lease.id}</td>
                        <td className="lease-text" title={propertyName}><strong>{propertyName}</strong></td>
                        <td className="lease-text" title={unitName}>{unitName}</td>
                        <td>{lease.start_date}</td>
                        <td>{lease.end_date}</td>
                        <td className="numeric">${lease.monthly_rent}</td>
                        <td className="lease-text" title={tenantNames || "No tenants"}>{lease.tenants.length}{tenantNames ? `: ${tenantNames}` : ""}</td>
                        <td className="lease-text" title={lease.notes || undefined}>{lease.notes || "-"}</td>
                        <td>
                          <div className="compact-row-actions">
                            <button type="button" className="bill-icon-button" title="View lease details" aria-label={`View details for lease #${lease.id}`} aria-haspopup="dialog" onClick={() => setLeaseDetailsId(lease.id)}>
                              <BillActionIcon name="details" />
                            </button>
                            <button type="button" className="bill-icon-button" disabled={loading} title="Edit lease" aria-label={`Edit lease #${lease.id}`} aria-haspopup="dialog" onClick={() => openEditLeaseModal(lease)}>
                              <BillActionIcon name="edit" />
                            </button>
                            <button
                              type="button"
                              className="bill-icon-button bill-icon-delete"
                              disabled={loading}
                              title="Delete lease"
                              aria-label={`Delete lease #${lease.id}`}
                              onClick={() => {
                                if (confirmDelete(`lease #${lease.id}`)) {
                                  void withStatus(() => api.deleteLease(lease.id).then(loadLandlordData), "Lease deleted.");
                                }
                              }}
                            >
                              <BillActionIcon name="delete" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </DataTable>
              </section>

              {leaseDetails && (
                <LeaseDetailsDialog
                  key={leaseDetails.id}
                  lease={leaseDetails}
                  propertyName={propertyNameById.get(leaseDetails.property_id) ?? `Property #${leaseDetails.property_id}`}
                  unitName={leaseDetails.unit_id ? (unitNameById.get(leaseDetails.unit_id) ?? `Unit #${leaseDetails.unit_id}`) : "Whole property"}
                  tenantNames={tenantNameById}
                  onClose={() => setLeaseDetailsId(null)}
                />
              )}

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

          {activeTab === "bill" && (
            <>
              <section className="card property-bills-section">
                <div className="card-header property-bills-header">
                  <div className="property-bills-heading">
                    <h2>Property Bills</h2>
                    <span className="bill-result-count" role="status">
                      {visiblePropertyBills.length} of {propertyBills.length} bills
                    </span>
                  </div>
                  <div className="header-actions">
                    <button className="primary" onClick={openBillModal}>
                      Create Property Bill
                    </button>
                  </div>
                </div>
                <div className="property-bills-toolbar" role="group" aria-label="Property bill filters">
                  <button
                    ref={billFilterButtonRef}
                    className="secondary bill-filter-trigger"
                    aria-haspopup="dialog"
                    title="Edit property bill filters"
                    onClick={() => setBillFilterOpen(true)}
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                      <path d="M3 4h18l-7 8v7l-4 2v-9Z" />
                    </svg>
                    Filters{activeBillFilters.length > 0 ? ` (${activeBillFilters.length})` : ""}
                  </button>
                  <button className="secondary" onClick={() => setBillFilter({ ...emptyPropertyBillFilters, ...currentMonthRange() })}>
                    Current Month
                  </button>
                  <button
                    className="secondary"
                    disabled={activeBillFilters.length === 0}
                    aria-label="Clear bill filters"
                    onClick={() => setBillFilter({ ...emptyPropertyBillFilters })}
                  >
                    Clear
                  </button>
                  <span
                    className="bill-filter-summary"
                    title={activeBillFilters.length > 0 ? activeBillFilters.join(" | ") : "All property bills"}
                  >
                    {activeBillFilters.length > 0 ? activeBillFilters.join(" | ") : "All property bills"}
                  </span>
                </div>
                <DataTable
                  key={JSON.stringify(billFilter)}
                  label="Property bills"
                  empty="No property bills match the selected filters."
                  headers={["Description", "Property", "Category", "Amount Due", "Due Date", "Repeat", "Status", "Tenant Bills", "Notes", "Actions"]}
                  className="compact-table property-bill-table"
                >
                  {visiblePropertyBills.map((bill) => (
                    <tr key={bill.id}>
                      <td className="bill-description" title={bill.title}><strong>{bill.title}</strong></td>
                      <td className="property-name" title={propertyNameById.get(bill.property_id) ?? `Property #${bill.property_id}`}>
                        {propertyNameById.get(bill.property_id) ?? `Property #${bill.property_id}`}
                      </td>
                      <td>{billCategoryLabels[bill.bill_type]}</td>
                      <td className="numeric">${bill.amount_due}</td>
                      <td>{bill.due_date}</td>
                      <td>{billRecurrenceLabels[bill.recurrence]}</td>
                      <td>
                        <span className={`status ${bill.status === "paid" ? "paid" : "open"}`}>{bill.status}</span>
                      </td>
                      <td>{bill.generated_at ? "Generated" : "Not generated"}</td>
                      <td className="bill-notes" title={bill.notes || undefined}>{bill.notes || "-"}</td>
                      <td>
                        <div className="compact-row-actions">
                          <button
                            className="bill-icon-button"
                            disabled={loading}
                            aria-label={`Edit property bill "${bill.title}"`}
                            title="Edit bill"
                            onClick={() =>
                              setBillEdit({ id: bill.id, title: bill.title, amount_due: bill.amount_due, due_date: bill.due_date })
                            }
                          >
                            <BillActionIcon name="edit" />
                          </button>
                          <button
                            className="bill-icon-button"
                            disabled={loading}
                            title={bill.status === "paid" ? "Mark unpaid" : "Mark paid"}
                            aria-label={`Mark property bill "${bill.title}" ${bill.status === "paid" ? "unpaid" : "paid"}`}
                            onClick={() => updatePropertyBillStatus(bill, bill.status === "paid" ? "unpaid" : "paid")}
                          >
                            <BillActionIcon name={bill.status === "paid" ? "unpaid" : "paid"} />
                          </button>
                          {["monthly", "bi_monthly", "quarterly", "semi_annually", "annually"].includes(bill.recurrence) && (
                            <button
                              className="bill-icon-button"
                              disabled={loading}
                              aria-haspopup="dialog"
                              aria-label={`Extend repeated bill "${bill.title}"`}
                              title="Extend repeated bill"
                              onClick={() => setExtensionBill(bill)}
                            >
                              <BillActionIcon name="extend" />
                            </button>
                          )}
                          <button
                            className="bill-icon-button"
                            disabled={loading || Boolean(bill.generated_at)}
                            aria-label={`Generate tenant bills for "${bill.title}"`}
                            title={bill.generated_at ? "Tenant bills already generated" : "Generate tenant bills"}
                            onClick={(event) => {
                              tenantChargeTrigger.current = event.currentTarget;
                              setTenantChargeBillId(bill.id);
                            }}
                          >
                            <BillActionIcon name="generate" />
                          </button>
                          <button
                            className="bill-icon-button bill-icon-delete"
                            disabled={loading}
                            aria-label={`Delete property bill "${bill.title}"`}
                            title="Delete bill"
                            onClick={() =>
                              confirmDelete(`property bill "${bill.title}"`) &&
                              withStatus(() => api.deletePropertyBill(bill.id).then(loadLandlordData), "Property bill deleted.")
                            }
                          >
                            <BillActionIcon name="delete" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </DataTable>
              </section>

              {extensionBill && (
                <ExtendBillDialog
                  bill={extensionBill}
                  onClose={() => setExtensionBill(null)}
                  onGenerated={(created) => {
                    setPropertyBills((current) => [...current, ...created]);
                    setExtensionBill(null);
                    setError("");
                    setMessage(`${created.length} property bills generated. Active filters may hide new bills.`);
                  }}
                />
              )}

              {tenantChargeBillId !== null && (
                <TenantChargeDialog
                  billId={tenantChargeBillId}
                  onClose={closeTenantChargeDialog}
                  onGenerated={tenantChargesGenerated}
                />
              )}

              {billFilterOpen && (
                <PropertyBillFilterDialog
                  filters={billFilter}
                  properties={properties}
                  onApply={(filters) => {
                    setBillFilter(filters);
                    closeBillFilters();
                  }}
                  onClose={closeBillFilters}
                />
              )}

              {billEdit && (
                <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="bill-edit-title">
                  <form onSubmit={submitBillEdit} className="card form modal-card">
                    <h2 id="bill-edit-title">Edit Bill</h2>
                    <p>{billEdit.title}</p>
                    <p className="muted">Updates only this property bill, not other recurring bills or existing tenant bills.</p>
                    <label>
                      Amount due
                      <input
                        autoFocus
                        required
                        type="number"
                        min="0.01"
                        step="0.01"
                        disabled={loading}
                        value={billEdit.amount_due}
                        onChange={(event) => setBillEdit({ ...billEdit, amount_due: event.target.value })}
                      />
                    </label>
                    <label>
                      Due date
                      <input
                        required
                        type="date"
                        max="9999-12-31"
                        disabled={loading}
                        value={billEdit.due_date}
                        onChange={(event) => setBillEdit({ ...billEdit, due_date: event.target.value })}
                      />
                    </label>
                    <div className="modal-actions">
                      <button className="primary" disabled={loading || !billEdit.amount_due || !billEdit.due_date}>
                        Save Bill
                      </button>
                      <button type="button" className="secondary" disabled={loading} onClick={() => setBillEdit(null)}>
                        Cancel
                      </button>
                    </div>
                  </form>
                </div>
              )}

              {billModalOpen && (
                <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="bill-modal-title">
                  <form onSubmit={submitManualBill} className="card form modal-card">
                    <div className="card-header">
                      <h2 id="bill-modal-title">Create Property Bill</h2>
                      <button type="button" className="secondary" onClick={closeBillModal}>
                        Close
                      </button>
                    </div>
                    <label>
                      Property
                      <select
                        required
                        value={manualBillForm.property_id}
                        onChange={(event) =>
                          setManualBillForm({
                            ...manualBillForm,
                            property_id: event.target.value,
                          })
                        }
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
                      Bill category
                      <select
                        value={manualBillForm.bill_type}
                        onChange={(event) =>
                          setManualBillForm({
                            ...manualBillForm,
                            bill_type: event.target.value as ManualBillForm["bill_type"],
                          })
                        }
                      >
                        <option value="electricity">Electricity</option>
                        <option value="water">Water</option>
                        <option value="mortgage">Mortgage</option>
                        <option value="insurance">Insurance</option>
                        <option value="property_tax">Property Tax</option>
                        <option value="maintenance">Maintenance</option>
                        <option value="other">Other</option>
                      </select>
                    </label>
                    <label>
                      Bill title
                      <input
                        required
                        placeholder="Example: Utility charge"
                        value={manualBillForm.title}
                        onChange={(event) => setManualBillForm({ ...manualBillForm, title: event.target.value })}
                      />
                    </label>
                    <label>
                      Amount due
                      <input
                        required
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={manualBillForm.amount_due}
                        onChange={(event) => setManualBillForm({ ...manualBillForm, amount_due: event.target.value })}
                      />
                    </label>
                    <label>
                      Repeat
                      <select
                        value={manualBillForm.recurrence}
                        onChange={(event) =>
                          setManualBillForm({
                            ...manualBillForm,
                            recurrence: event.target.value as ManualBillForm["recurrence"],
                          })
                        }
                      >
                        <option value="one_time">One-time bill</option>
                        <option value="monthly">Repeated monthly bill</option>
                        <option value="bi_monthly">Repeated bi-monthly bill (every two months)</option>
                        <option value="quarterly">Repeated quarterly bill (every three months)</option>
                        <option value="semi_annually">Repeated bill every six months</option>
                        <option value="annually">Repeated annual bill</option>
                        <option value="custom_period">Bill for a specified period</option>
                      </select>
                    </label>
                    <label>
                      Due date
                      <input
                        required
                        type="date"
                        value={manualBillForm.due_date}
                        onChange={(event) => setManualBillForm({ ...manualBillForm, due_date: event.target.value })}
                      />
                    </label>
                    {manualBillForm.recurrence !== "one_time" && (
                      <div className="two-columns">
                        <label>
                          Period start
                          <input
                            required
                            type="date"
                            value={manualBillForm.period_start}
                            onChange={(event) => setManualBillForm({ ...manualBillForm, period_start: event.target.value })}
                          />
                        </label>
                        <label>
                          Period end
                          <input
                            required
                            type="date"
                            value={manualBillForm.period_end}
                            onChange={(event) => setManualBillForm({ ...manualBillForm, period_end: event.target.value })}
                          />
                        </label>
                      </div>
                    )}
                    <label>
                      Notes
                      <textarea
                        value={manualBillForm.notes}
                        onChange={(event) => setManualBillForm({ ...manualBillForm, notes: event.target.value })}
                      />
                    </label>
                    <div className="modal-actions">
                      <button disabled={loading} className="primary">
                        Create Property Bill
                      </button>
                      <button type="button" className="secondary" onClick={closeBillModal}>
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
                  <DataTable
                    key={selectedPaymentTenantId}
                    label={`${selectedPaymentTenant.full_name} payment history`}
                    empty="No payment history for this tenant yet."
                    headers={["Payment ID", "Paid At", "Amount Paid", "Billing Month / Bill ID", "Note"]}
                    className="compact-table"
                  >
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
              <DataTable
                key={String(showHistoricPaidBills)}
                label="Rental records"
                empty="No rental records to show. Generate rental after creating an active lease or enable historic paid rental."
                headers={["Bill ID", "Description", "Tenant", "Billing Month", "Amount Due", "Amount Paid", "Balance", "Status", "Actions"]}
                className="compact-table rental-table"
              >
                {visibleBills.map((bill) => {
                  const isTenantCharge = bill.is_tenant_charge || bill.source_property_bill_id != null;
                  return (
                    <tr key={bill.id}>
                      <td>#{bill.id}</td>
                      <td className="bill-description" title={bill.title}>
                        <strong>{bill.title}</strong>
                      </td>
                      <td>
                        <button className="link-button" onClick={() => setSelectedPaymentTenantId(bill.tenant_id)}>
                          {bill.tenant_name || tenantNameById.get(bill.tenant_id) || `Tenant #${bill.tenant_id}`}
                        </button>
                      </td>
                      <td>
                        {bill.bill_year}-{String(bill.bill_month).padStart(2, "0")}
                      </td>
                      <td className="numeric">${bill.amount_due}</td>
                      <td className="numeric">${bill.amount_paid}</td>
                      <td className="numeric">${bill.balance}</td>
                      <td>
                        <span className={`status ${bill.status}`}>{bill.status}</span>
                      </td>
                      <td>
                        <div className="rental-row-actions">
                          <button
                            disabled={loading || Number(bill.balance) <= 0}
                            aria-label={`Record payment for bill #${bill.id}`}
                            onClick={() => {
                              setError("");
                              setSelectedPaymentBillId(bill.id);
                            }}
                          >
                            Pay
                          </button>
                          <button
                            disabled={loading}
                            aria-label={`Delete bill #${bill.id}`}
                            onClick={() =>
                              confirmDelete(`${isTenantCharge ? "tenant charge" : "rental record"} #${bill.id}`) &&
                              withStatus(
                                () => api.deleteBill(bill.id).then(loadLandlordData),
                                isTenantCharge ? "Tenant charge deleted." : "Rental record deleted.",
                              )
                            }
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </DataTable>
              {selectedPaymentBillId !== null && bills.some((bill) => bill.id === selectedPaymentBillId) && (
                <PaymentDialog
                  bill={bills.find((bill) => bill.id === selectedPaymentBillId)!}
                  payment={paymentByBill[selectedPaymentBillId] ?? { amount: "", paid_at: "", note: "" }}
                  saving={loading}
                  error={error}
                  onChange={(payment) => setPaymentByBill((current) => ({ ...current, [selectedPaymentBillId]: payment }))}
                  onClose={() => setSelectedPaymentBillId(null)}
                  onSubmit={() => recordPayment(selectedPaymentBillId)}
                />
              )}
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

export default App;
