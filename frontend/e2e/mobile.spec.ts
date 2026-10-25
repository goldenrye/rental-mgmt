import { test, expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

async function mockAPI(page: Page, admin = false) {
  const now = new Date();
  const dueDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-15`;
  const landlord = { id: 1, email: "owner@example.test", full_name: "Test Owner", role: "landlord", is_active: true, password_change_required: false, encryption_enabled: false };
  const user = admin ? { ...landlord, role: "super_admin", full_name: "Admin", email: "admin" } : landlord;
  const properties = Array.from({ length: 11 }, (_, i) => ({ id: i + 1, landlord_id: 1, name: `Property ${i + 1}`, address: "123 Long Example Street", description: "Test property" }));
  const tenant = { id: 1, landlord_id: 1, full_name: "Test Tenant", email: "tenant@example.test", phone: "5551234567", notes: "" };
  const lease = { id: 1, landlord_id: 1, property_id: 1, unit_id: 1, start_date: "2026-01-01", end_date: "2030-12-31", monthly_rent: "1000.00", tenant_ids: [1], tenants: [{ tenant_id: 1, monthly_rent: "1000.00", deposit: "1000.00" }], notes: "" };
  const propertyBill = { id: 1, landlord_id: 1, property_id: 1, bill_type: "water", title: "Water bill", amount_due: "120.00", recurrence: "monthly", due_date: dueDate, period_start: "2026-01-01", period_end: "2030-12-31", status: "unpaid", generated_at: null, notes: "" };
  const rental = { id: 1, landlord_id: 1, lease_id: 1, tenant_id: 1, tenant_name: "Test Tenant", is_tenant_charge: false, title: "Monthly rent", bill_type: "rent", bill_year: now.getFullYear(), bill_month: now.getMonth() + 1, due_date: dueDate, amount_due: "1000.00", amount_paid: "0.00", balance: "1000.00", status: "open" };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    const method = route.request().method();
    let body: unknown;
    if (path === "/auth/login") body = { access_token: "test-session", token_type: "bearer" };
    else if (path === "/auth/logout") return route.fulfill({ status: 204 });
    else if (path === "/me") body = user;
    else if (path === "/properties") body = method === "POST" ? { ...properties[0], ...route.request().postDataJSON() } : properties;
    else if (path === "/units") body = [{ id: 1, property_id: 1, name: "Suite One", description: "Upstairs" }];
    else if (path === "/tenants") body = [tenant];
    else if (path === "/leases") body = [lease];
    else if (path === "/bills") body = [rental];
    else if (path === "/payments") body = [];
    else if (path === "/property-bills") body = [propertyBill];
    else if (path.includes("tenant-bills-preview")) body = {
      property_bill_id: 1, property_name: "Property 1", title: "Water bill", amount_due: "120.00",
      due_date: dueDate, bill_type: "water", recurrence: "monthly", period_start: "2026-01-01",
      period_end: "2030-12-31", preview_token: "preview",
      tenants: [{ tenant_id: 1, lease_id: 1, tenant_name: "Test Tenant", amount_due: "120.00" }],
    };
    else if (path.endsWith("/generate-tenant-bills")) body = [{ ...rental, id: 2, is_tenant_charge: true, title: "Water charge" }];
    else if (path.endsWith("/extension-preview")) body = {
      source_bill_id: 1, title: propertyBill.title, property_name: "Property 1", recurrence: "monthly",
      current_end: dueDate, through: "2031-01-31", due_day: 15, existing_future_bills: [propertyBill],
      new_bills: [{ due_date: "2031-01-15", amount_due: "120.00" }], total_new_amount: "120.00",
      warning: null, preview_token: "extension-preview",
    };
    else if (path.endsWith("/extend")) body = [{ ...propertyBill, id: 2, due_date: "2031-01-15" }];
    else if (path === "/bills/1/payments") body = { id: 1, bill_id: 1, tenant_id: 1, landlord_id: 1, amount: "50.00", paid_at: new Date().toISOString() };
    else if (path === "/admin/landlords") body = [{
      user_id: 1, landlord_id: 1, full_name: "Test Owner", email: "owner@example.test",
      is_active: true, active_sessions: 1, login_status: "signed_in",
      created_at: "2026-01-01T00:00:00Z", last_login_at: null, last_seen_at: null,
    }];
    else if (path === "/admin/invites") body = method === "POST" ? {
      id: 1, email: "new@example.test", code: "test-invitation-1234567890", status: "active",
      created_at: "2026-01-01T00:00:00Z", expires_at: "2030-01-01T00:00:00Z",
    } : [];
    else return route.fulfill({ status: 404, json: { detail: `Unmocked endpoint ${method} ${path}` } });
    await route.fulfill({ json: body });
  });
}

async function login(page: Page, admin = false) {
  await mockAPI(page, admin);
  await page.goto("/");
  await page.getByLabel("Email or admin username").fill(admin ? "admin" : "owner@example.test");
  await page.getByLabel("Password", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "Login", exact: true }).click();
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

async function actionStyle(button: Locator) {
  return button.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor, color: style.color, border: style.borderColor,
      radius: style.borderRadius, size: style.fontSize, weight: style.fontWeight,
      minHeight: style.minHeight,
    };
  });
}

test("compact mobile spacing preserves readable fields and touch targets", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Mobile density checks");
  await login(page);
  const card = page.locator(".property-unit-page > section.card");
  await expect(card).toHaveCSS("padding", "12px");
  await expect(card.locator(".card-header")).toHaveCSS("flex-direction", "row");
  const table = page.getByRole("table", { name: "Properties", exact: true });
  await expect(table.locator("tbody > tr").first()).toHaveCSS("padding", "8px 10px");
  await expect(table.locator("tbody > tr > td").first()).toHaveCSS("padding", "4px 0px");
  await expect(page.locator(".app-navigation button").first()).toHaveCSS("min-height", "48px");
  await noOverflow(page);

  await page.getByRole("button", { name: "Add Property", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".form")).toHaveCSS("gap", "10px");
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveCSS("font-size", "16px");
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveCSS("min-height", "44px");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toHaveCSS("min-height", "44px");
  await noOverflow(page);
});

test("property expand controls align with mobile values and keep desktop styling", async ({ page, isMobile }) => {
  await login(page);
  const trigger = page.getByRole("button", { name: "Show units for Property 1", exact: true });
  if (!isMobile) {
    await expect(trigger).toHaveCSS("justify-content", "center");
    return;
  }
  for (const width of [320, 375, 390, 844]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(trigger).toHaveCSS("justify-content", "flex-start");
    const row = page.locator(".property-row").first();
    const addressLeft = await row.locator("td[data-label='Address']").evaluate((cell) => {
      const range = document.createRange();
      range.selectNodeContents(cell);
      return range.getBoundingClientRect().left;
    });
    const chevron = await trigger.locator(".property-chevron").boundingBox();
    expect(Math.abs(chevron!.x - addressLeft)).toBeLessThan(4);
    expect((await trigger.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await noOverflow(page);
  }
  await trigger.click();
  const expanded = page.getByRole("button", { name: "Hide units for Property 1", exact: true });
  await expect(expanded).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("Suite One", { exact: true })).toBeVisible();
  await expect(expanded).toHaveCSS("justify-content", "flex-start");
  await expanded.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
});

test("mobile stylesheet rules have no effect above the desktop breakpoint", async ({ page, isMobile }) => {
  test.skip(isMobile, "Desktop breakpoint isolation");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await login(page);
  for (const width of [701, 980, 1000, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    const unchanged = await page.evaluate(() => {
      const rules = Array.from(document.styleSheets).flatMap((sheet) =>
        Array.from(sheet.cssRules).filter((rule): rule is CSSMediaRule =>
          rule instanceof CSSMediaRule && rule.conditionText.includes("max-width: 700px")),
      );
      const elements = Array.from(document.querySelectorAll("*"));
      const snapshot = () => elements.map((element) => {
        const style = getComputedStyle(element);
        return Array.from(style).map((property) => style.getPropertyValue(property));
      });
      const before = snapshot();
      const conditions = rules.map((rule) => rule.media.mediaText);
      try {
        rules.forEach((rule) => { rule.media.mediaText = "not all"; });
        return JSON.stringify(snapshot()) === JSON.stringify(before);
      } finally {
        rules.forEach((rule, index) => { rule.media.mediaText = conditions[index]; });
      }
    });
    expect(unchanged, `Mobile rules must not change desktop styles at ${width}px`).toBe(true);
  }
});

test("lease tenant names use available mobile width without unnecessary line breaks", async ({ page, isMobile }) => {
  await login(page);
  let name = "Alexandra Johnson";
  await page.route("**/api/tenants", (route) => route.fulfill({ json: [
    { id: 1, landlord_id: 1, full_name: name, email: "tenant@example.test", phone: "", notes: "" },
  ] }));
  await page.locator(".refresh-data").click();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Leases", exact: true }).click();
  const tenants = page.locator(".lease-table td[data-label='Tenants']");
  await expect(tenants).toContainText("1: Alexandra Johnson");
  for (const width of isMobile ? [320, 375, 390] : [1280]) {
    await page.setViewportSize({ width, height: 844 });
    const lineTops = await tenants.evaluate((cell) => {
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      const tops = new Set<number>();
      while (walker.nextNode()) {
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        for (const rect of Array.from(range.getClientRects())) tops.add(Math.round(rect.top));
      }
      return [...tops];
    });
    expect(lineTops, `Tenant name should fit on one line at ${width}px`).toHaveLength(1);
    await noOverflow(page);
  }
  if (isMobile) {
    name = "Alexandra Elizabeth Johnson-Smith and Christopher Montgomery";
    await page.locator(".refresh-data").click();
    await expect(tenants).toContainText(name);
    await expect(tenants).toHaveCSS("white-space", "normal");
    expect(await tenants.evaluate((cell) => cell.scrollWidth <= cell.clientWidth)).toBe(true);
    await noOverflow(page);
  }
});

test("amounts align with mobile fields and stay right-aligned on desktop", async ({ page, isMobile }) => {
  await login(page);
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  for (const section of ["Leases", "Rental & Payments", "Property Bills"]) {
    await navigation.getByRole("button", { name: section, exact: true }).click();
    const amounts = page.locator(".mobile-table td.numeric");
    expect(await amounts.count()).toBeGreaterThan(0);
    for (const amount of await amounts.all()) {
      await expect(amount).toHaveCSS("text-align", isMobile ? "left" : "right");
      if (isMobile) {
        expect(await amount.evaluate((cell) => getComputedStyle(cell, "::before").textAlign)).toBe("left");
      }
    }
    if (section === "Leases" && isMobile) {
      const valueLeft = (cell: Element) => {
        const range = document.createRange();
        range.selectNodeContents(cell);
        return range.getBoundingClientRect().left;
      };
      const rentLeft = await amounts.first().evaluate(valueLeft);
      const dateLeft = await page.locator(".lease-table td[data-label='Start Date']").evaluate(valueLeft);
      expect(Math.abs(rentLeft - dateLeft)).toBeLessThan(1);
    }
    await noOverflow(page);
  }
});

for (const [section, table] of [
  ["Rental & Payments", ".rental-table"],
  ["Property Bills", ".property-bill-table"],
]) {
  test(`${section} fields share a consistent mobile value column without changing desktop`, async ({ page, isMobile }) => {
    await login(page);
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: section, exact: true }).click();
    if (!isMobile) {
      await expect(page.locator(table)).toHaveCSS("display", "table");
      await expect(page.locator(`${table} .bill-description`)).toHaveCSS("max-width", "240px");
      await expect(page.locator(`${table} .bill-description`)).toHaveCSS("text-overflow", "ellipsis");
      if (section === "Property Bills") {
        for (const field of [".property-name", ".bill-notes"]) {
          await expect(page.locator(`${table} ${field}`)).toHaveCSS("max-width", "160px");
          await expect(page.locator(`${table} ${field}`)).toHaveCSS("text-overflow", "ellipsis");
        }
      }
      return;
    }
    for (const width of [320, 375, 390, 844]) {
      await page.setViewportSize({ width, height: 844 });
      const fields = await page.locator(`${table} > tbody > tr > td:not([data-full-width='true'])`).evaluateAll((cells) =>
        cells.map((cell) => {
          const range = document.createRange();
          range.selectNodeContents(cell);
          return {
            label: cell.getAttribute("data-label"),
            left: range.getBoundingClientRect().left,
            columns: getComputedStyle(cell).gridTemplateColumns,
          };
        }),
      );
      for (const field of fields) {
        expect(field.columns, `${field.label} columns at ${width}px`).toBe(fields[0].columns);
        expect(Math.abs(field.left - fields[0].left), `${field.label} value at ${width}px`).toBeLessThan(1);
      }
      await noOverflow(page);
    }
    if (section === "Rental & Payments") {
      const tenant = page.locator(".rental-table .link-button");
      expect((await tenant.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await tenant.click();
      await expect(page.getByRole("heading", { name: "Test Tenant payment history" })).toBeVisible();
    }
  });
}

test("action buttons share styles across pages and retain focus and touch targets", async ({ page, isMobile }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await login(page);
  const addProperty = page.getByRole("button", { name: "Add Property", exact: true });
  const primary = await actionStyle(addProperty);
  const secondary = await actionStyle(page.getByRole("button", { name: "Add unit to Property 1", exact: true }));
  const danger = await actionStyle(page.getByRole("table", { name: "Properties", exact: true }).getByRole("button", { name: "Delete", exact: true }).first());
  expect(primary.radius).toBe("8px");
  expect(primary.size).toBe("14px");
  expect(primary.minHeight).toBe(isMobile ? "44px" : "36px");
  expect(primary.background).not.toBe(secondary.background);
  expect(danger.color).toBe("rgb(180, 35, 24)");
  await addProperty.focus();
  await expect(addProperty).toBeFocused();
  if (!isMobile) {
    // Desktop Tab navigation differs from iOS hardware-keyboard settings.
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(addProperty).toBeFocused();
    expect(await addProperty.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("solid");
    await addProperty.hover();
    expect((await actionStyle(addProperty)).background).toBe("rgb(32, 69, 181)");
    await page.mouse.move(0, 0);
  }
  await addProperty.click();
  const dialog = page.getByRole("dialog");
  await page.mouse.move(0, 0);
  expect(await actionStyle(dialog.getByRole("button", { name: "Cancel", exact: true }))).toEqual(secondary);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  const nav = page.getByRole("navigation", { name: "Main navigation" });
  for (const [section, action] of [
    ["Tenants", "Add Tenant"], ["Leases", "Add Lease"], ["Property Bills", "Create Property Bill"],
  ]) {
    await nav.getByRole("button", { name: section, exact: true }).click();
    expect(await actionStyle(page.getByRole("button", { name: action, exact: true }))).toEqual(primary);
    await noOverflow(page);
  }
  const icon = page.locator(".bill-icon-button:not(.bill-icon-delete)").first();
  expect(await actionStyle(icon)).toEqual(secondary);
  expect(await actionStyle(page.locator(".bill-icon-delete"))).toEqual(danger);
  const box = await icon.boundingBox();
  expect(box!.width).toBe(isMobile ? 44 : 36);
  expect(box!.height).toBe(isMobile ? 44 : 36);
  await nav.getByRole("button", { name: "Rental & Payments", exact: true }).click();
  expect(await actionStyle(page.getByRole("button", { name: "Record payment for bill #1", exact: true }))).toEqual(secondary);
  expect(await actionStyle(page.getByRole("button", { name: "Delete bill #1", exact: true }))).toEqual(danger);
  await page.getByRole("button", { name: "Record payment for bill #1", exact: true }).click();
  const save = page.getByRole("dialog").getByRole("button", { name: "Record Payment", exact: true });
  await expect(save).toBeDisabled();
  expect(await save.evaluate((el) => getComputedStyle(el).opacity)).toBe("0.5");
  expect(await actionStyle(save)).toEqual(primary);
});

test("all landlord sections, nested units, pagination and forms remain accessible", async ({ page, isMobile }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: "Welcome, Test Owner" })).toBeVisible();
  await noOverflow(page);
  await page.getByRole("button", { name: "Show units for Property 1", exact: true }).click();
  await expect(page.getByText("Suite One", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.getByRole("button", { name: "Properties page 2", exact: true }).click();
  await expect(page.getByText("Property 11", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add Property", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Name", { exact: true }).fill("Phone property");
  await dialog.getByLabel("Address", { exact: true }).fill("456 Example Road");
  await noOverflow(page);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  for (const name of ["Tenants", "Leases", "Rental & Payments", "Property Bills"]) {
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name, exact: true }).click();
    if (name === "Rental & Payments") {
      await expect(page.getByRole("button", { name: "Generate Current Rental" })).toHaveCount(0);
      await expect(page.getByText("Current-month rent is generated automatically for eligible leases.")).toBeVisible();
    }
    await noOverflow(page);
  }
  await expect(page.getByText("Water bill", { exact: true })).toBeVisible();
  await expect(page.locator(".bill-filter-summary")).toContainText("Status: unpaid");
  if (isMobile) await page.screenshot({ path: "/tmp/rental-mgmt-iphone-bills.png", fullPage: true });
  if (isMobile) {
    expect(await page.locator(".app-navigation").evaluate((el) => getComputedStyle(el).position)).toBe("fixed");
    const action = page.locator(".bill-icon-button").first();
    const box = await action.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
  } else {
    expect(await page.locator(".property-bill-table").evaluate((el) => getComputedStyle(el).display)).toBe("table");
  }
  await page.getByRole("button", { name: /Filters/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await noOverflow(page);
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Edit Profile", exact: true }).click();
  await expect(page.getByText("Data Encryption", { exact: false }).first()).toBeVisible();
  await noOverflow(page);
});

test("bill previews and payment confirmation retain their request contracts", async ({ page }) => {
  await login(page);
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await navigation.getByRole("button", { name: "Property Bills", exact: true }).click();
  await page.getByRole("button", { name: 'Generate tenant bills for "Water bill"' }).click();
  let dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("table", { name: "Tenant charge preview" })).toBeVisible();
  await expect(dialog.getByText("Test Tenant", { exact: true })).toBeVisible();
  await noOverflow(page);
  const generation = page.waitForRequest((request) => request.url().endsWith("/generate-tenant-bills") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Confirm & Generate" }).click();
  expect((await generation).postDataJSON()).toEqual({ preview_token: "preview" });
  await expect(dialog).toHaveCount(0);

  await page.getByRole("button", { name: 'Extend repeated bill "Water bill"' }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Extend through").fill("2031-01-31");
  await dialog.getByRole("button", { name: "Preview Bills" }).click();
  await expect(dialog.getByRole("table", { name: "Existing future bills" })).toBeVisible();
  await expect(dialog.getByRole("table", { name: "New bills to generate" })).toBeVisible();
  await noOverflow(page);
  const extension = page.waitForRequest((request) => request.url().endsWith("/extend") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Confirm & Generate" }).click();
  expect((await extension).postDataJSON()).toEqual({ through: "2031-01-31", due_day: 15, preview_token: "extension-preview" });
  await expect(dialog).toHaveCount(0);

  await navigation.getByRole("button", { name: "Rental & Payments", exact: true }).click();
  await page.getByRole("button", { name: "Record payment for bill #1", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Payment amount ($)").fill("50");
  await dialog.getByLabel("Note (optional)").fill("Phone payment");
  await noOverflow(page);
  const payment = page.waitForRequest((request) => request.url().endsWith("/bills/1/payments") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Record Payment", exact: true }).click();
  expect((await payment).postDataJSON()).toEqual({ amount: "50", note: "Phone payment" });
  await expect(dialog).toHaveCount(0);
});

test("dates support typing, calendar changes, validation and resets across forms", async ({ page }) => {
  await login(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await nav.getByRole("button", { name: "Property Bills", exact: true }).click();
  await page.getByRole("button", { name: 'Extend repeated bill "Water bill"' }).click();
  let dialog = page.getByRole("dialog");
  const through = dialog.getByLabel("Extend through", { exact: true });
  await expect(through).toHaveAttribute("type", "text");
  await through.pressSequentially("2032-02-");
  await expect(through).toHaveValue("2032-02-");
  await through.pressSequentially("29");
  const previewRequest = page.waitForRequest((r) => r.url().endsWith("/extension-preview"));
  await dialog.getByRole("button", { name: "Preview Bills", exact: true }).click();
  expect((await previewRequest).postDataJSON()).toEqual({ through: "2032-02-29" });
  await expect(dialog.getByRole("button", { name: "Confirm & Generate" })).toBeEnabled();
  let invalidRequests = 0;
  page.on("request", (r) => { if (r.url().endsWith("/extension-preview")) invalidRequests++; });
  await through.fill("2031-02-29");
  await expect(dialog.getByRole("button", { name: "Confirm & Generate" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Preview Bills", exact: true }).click();
  expect(await through.evaluate((el: HTMLInputElement) => el.validity.valid)).toBe(false);
  expect(invalidRequests).toBe(0);
  const calendar = dialog.getByLabel("Choose date with calendar", { exact: true });
  await calendar.fill("2032-03-31");
  await expect(through).toHaveValue("2032-03-31");
  expect(await through.evaluate((el: HTMLInputElement) => el.validity.valid)).toBe(true);
  await noOverflow(page);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.getByRole("button", { name: 'Edit property bill "Water bill"' }).click();
  dialog = page.getByRole("dialog");
  const due = dialog.getByLabel("Due date", { exact: true });
  await due.fill("2032-04-30");
  await expect(dialog.getByLabel("Choose date with calendar")).toHaveValue("2032-04-30");
  const edited = page.waitForRequest((r) => r.url().endsWith("/property-bills/1") && r.method() === "PATCH");
  await page.route("**/api/property-bills/1", (route) => route.fulfill({ json: {
    id: 1, landlord_id: 1, property_id: 1, title: "Water bill", bill_type: "water",
    recurrence: "monthly", status: "unpaid", ...route.request().postDataJSON(),
  } }));
  await dialog.getByRole("button", { name: "Save Bill", exact: true }).click();
  expect((await edited).postDataJSON().due_date).toBe("2032-04-30");

  await page.getByRole("button", { name: "Create Property Bill", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox", { name: "Repeat", exact: true }).selectOption("monthly");
  for (const [label, value] of [["Due date", "2032-05-15"], ["Period start", "2032-05-01"], ["Period end", "2032-12-31"]]) {
    const field = dialog.getByLabel(label, { exact: true });
    await expect(field).toHaveAttribute("type", "text");
    await field.fill(value);
    await expect(field).toHaveValue(value);
  }
  await noOverflow(page);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  await page.getByRole("button", { name: /Filters/ }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Reset Filters" }).click();
  await dialog.getByLabel("Start date (due from)", { exact: true }).fill("2032-05-01");
  await dialog.getByLabel("End date (due through)", { exact: true }).fill("2032-05-31");
  await dialog.getByLabel("Exact due date", { exact: true }).fill("2032-05-15");
  await dialog.getByRole("button", { name: "Apply Filters" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".bill-filter-summary")).toContainText("2032-05-15");
  await page.getByRole("button", { name: /Filters/ }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Exact due date", { exact: true }).fill("invalid");
  await dialog.getByRole("button", { name: "Reset Filters" }).click();
  for (const field of await dialog.locator(".date-field > input").all()) {
    await expect(field).toHaveValue("");
    expect(await field.evaluate((el: HTMLInputElement) => el.validity.valid)).toBe(true);
  }
  await dialog.getByRole("button", { name: "Apply Filters" }).click();

  await nav.getByRole("button", { name: "Leases", exact: true }).click();
  await page.getByRole("button", { name: "Add Lease", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Start date", { exact: true }).fill("2032-01-01");
  await dialog.getByLabel("End date", { exact: true }).fill("2032-12-31");
  await expect(dialog.getByLabel("Start date", { exact: true })).toHaveAttribute("type", "text");
  await expect(dialog.getByLabel("End date", { exact: true })).toHaveValue("2032-12-31");
  await noOverflow(page);
});

test("super admin management and invitation links work on mobile and desktop", async ({ page }) => {
  await login(page, true);
  await expect(page.getByRole("heading", { name: "Landlord Management" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Landlord accounts" })).toBeVisible();
  await noOverflow(page);
  await page.getByLabel("Landlord email (optional)").fill("new@example.test");
  await page.getByRole("button", { name: "Create Invite Link" }).click();
  await expect(page.getByLabel("Invitation link", { exact: true })).toHaveValue("http://127.0.0.1:5179/#invite=test-invitation-1234567890");
  await page.getByRole("button", { name: "Reset password for owner@example.test" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await noOverflow(page);
});

test("offline notice explains that writes are not queued", async ({ page, context }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: "Welcome, Test Owner" })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByText(/Changes require a connection and are not queued/)).toBeVisible();
  await context.setOffline(false);
  await expect(page.getByText(/Changes require a connection and are not queued/)).toHaveCount(0);
});

test("landlord creates a one-time MCP key and revokes it from the profile", async ({ page }) => {
  await login(page);
  let created = false;
  let revoked = false;
  const secret = `rmcp_${"a".repeat(43)}`;
  const metadata = () => ({
    id: "test-key", name: "My agent", prefix: secret.slice(0, 13), status: revoked ? "revoked" : "active",
    created_at: "2026-10-24T00:00:00Z", expires_at: null, last_used_at: null, revoked_at: null, scope: "rental:read",
  });
  await page.route("**/api/me/mcp-api-keys**", async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        name: "My agent", current_password: "test-password", expires_in_days: null,
      });
      created = true;
      return route.fulfill({ status: 201, json: { ...metadata(), api_key: secret } });
    }
    if (route.request().method() === "DELETE") {
      revoked = true;
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({ json: created ? [metadata()] : [] });
  });
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "MCP API Keys", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "MCP API Keys" });
  await dialog.getByLabel("Key name", { exact: true }).fill("My agent");
  await dialog.getByLabel("Current password", { exact: true }).fill("test-password");
  await dialog.getByRole("button", { name: "Create API Key", exact: true }).click();
  await expect(dialog.getByLabel("New MCP API key")).toHaveValue(secret);
  await expect(dialog.getByLabel("Current password", { exact: true })).toHaveValue("");
  await noOverflow(page);
  expect(await page.evaluate((key) => Object.values(localStorage).some((value) => value.includes(key)), secret)).toBe(false);
  page.on("dialog", (confirmation) => confirmation.accept());
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "MCP API Keys", exact: true }).click();
  await expect(dialog.getByLabel("New MCP API key")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Revoke API key My agent" }).click();
  await expect(dialog.getByRole("cell", { name: /^(Status )?revoked$/ })).toBeVisible();
  expect(revoked).toBe(true);
});
