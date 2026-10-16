import type { PropertyBill } from "./api";

export const billCategoryLabels: Record<PropertyBill["bill_type"], string> = {
  electricity: "Electricity",
  water: "Water",
  mortgage: "Mortgage",
  insurance: "Insurance",
  property_tax: "Property Tax",
  maintenance: "Maintenance",
  credit_card: "Credit Card",
  communication: "Communication",
  garbage: "Garbage",
  hoa: "HOA",
  management: "Management",
  other: "Other",
};

export const billRecurrenceLabels: Record<PropertyBill["recurrence"], string> = {
  one_time: "One-time",
  monthly: "Monthly",
  bi_monthly: "Every two months",
  quarterly: "Quarterly (every three months)",
  semi_annually: "Every six months",
  annually: "Annually",
  custom_period: "Specified period",
};

export type PropertyBillFilters = {
  start: string;
  end: string;
  due_date: string;
  bill_type: PropertyBill["bill_type"] | "";
  property_id: string;
  status: PropertyBill["status"] | "";
  recurrence: PropertyBill["recurrence"] | "";
  tenant_bills: "" | "generated" | "not_generated";
};

export const emptyPropertyBillFilters: PropertyBillFilters = {
  start: "",
  end: "",
  due_date: "",
  bill_type: "",
  property_id: "",
  status: "",
  recurrence: "",
  tenant_bills: "",
};

export function billFilterError(filters: PropertyBillFilters): string {
  if (filters.start && filters.end && filters.start > filters.end) {
    return "End date must be on or after start date.";
  }
  if (
    filters.due_date &&
    ((filters.start && filters.due_date < filters.start) || (filters.end && filters.due_date > filters.end))
  ) {
    return "Exact due date must be within the selected date range, or clear the start and end dates.";
  }
  return "";
}

export function filterPropertyBills(bills: PropertyBill[], filters: PropertyBillFilters): PropertyBill[] {
  return bills.filter(
    (bill) =>
      (!filters.start || bill.due_date >= filters.start) &&
      (!filters.end || bill.due_date <= filters.end) &&
      (!filters.due_date || bill.due_date === filters.due_date) &&
      (!filters.bill_type || bill.bill_type === filters.bill_type) &&
      (!filters.property_id || String(bill.property_id) === filters.property_id) &&
      (!filters.status || bill.status === filters.status) &&
      (!filters.recurrence || bill.recurrence === filters.recurrence) &&
      (!filters.tenant_bills || Boolean(bill.generated_at) === (filters.tenant_bills === "generated")),
  );
}
