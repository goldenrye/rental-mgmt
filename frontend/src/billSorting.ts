export function sortBillsByDueDate<T extends { id: number; due_date: string }>(
  bills: readonly T[],
  order: "asc" | "desc" = "asc",
): T[] {
  // API dates use YYYY-MM-DD; equal dates use IDs for consistent pagination.
  return [...bills].sort((first, second) =>
    (order === "desc" ? second.due_date.localeCompare(first.due_date) : first.due_date.localeCompare(second.due_date))
    || first.id - second.id,
  );
}
