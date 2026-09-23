/**
 * UI simulation: WhatsApp call buttons + bulk "select all matching -> add to
 * list" on the Contacts screen.
 *
 * These render the REAL page component in jsdom and click through the same
 * taps an operator makes, with the API mocked at the axios boundary.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import ContactsPage from "./ContactsPage";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

function makeContact(id: number, over: Partial<any> = {}) {
  return {
    id,
    first_name: `First${id}`,
    last_name: "Tester",
    business_name: null,
    phone_number: `+23480${String(id).padStart(8, "0")}`,
    email: null,
    city: null,
    state: null,
    country: "Nigeria",
    website: null,
    industry: null,
    source: null,
    lead_status: "new",
    consent_status: "unknown",
    has_consented: false,
    is_opted_out: false,
    is_undeliverable: false,
    delivery_fail_count: 0,
    notes: null,
    custom_fields: null,
    tags: [],
    messages_sent: 0,
    messages_received: 0,
    last_contacted_at: null,
    last_reply_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const PAGE = Array.from({ length: 25 }, (_, i) => makeContact(i + 1));

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  openSpy.mockClear();
  apiGet.mockImplementation((url: string) => {
    if (url === "/contacts/") {
      return Promise.resolve({ data: { total: 60, items: PAGE } });
    }
    if (url === "/lists/") {
      return Promise.resolve({
        data: { items: [{ id: 7, name: "Bulk Target", description: null, contact_count: 0 }] },
      });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });
  apiPost.mockRejectedValue(new Error("unmocked POST"));
});

async function renderContacts() {
  render(<ContactsPage />);
  await waitFor(() => expect(screen.getAllByText("First1 Tester").length).toBeGreaterThan(0));
}

describe("ContactsPage — WhatsApp call buttons everywhere", () => {
  it("renders a WhatsApp call button for every contact on both mobile cards and desktop table", async () => {
    await renderContacts();

    // Mobile cards + desktop table both render their own action sets in jsdom.
    const waButtons = screen.getAllByTitle(/on WhatsApp/i);
    expect(waButtons.length).toBeGreaterThanOrEqual(25 * 2);

    // The WhatsApp call button opens a wa.me deep link with the right digits.
    fireEvent.click(waButtons[0]);
    expect(openSpy).toHaveBeenCalledTimes(1);
    const url = openSpy.mock.calls[0][0] as string;
    expect(url).toMatch(/^https:\/\/wa\.me\/23480\d{8}$/);
  });
});

describe("ContactsPage — select all matching -> bulk add to list", () => {
  it("adds ALL matching contacts on the server when 'all matching' is active", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === "/lists/7/contacts/add-all") {
        return Promise.resolve({ data: { success: true, added: 60, matched: 60, contact_count: 60 } });
      }
      return Promise.reject(new Error("unmocked POST " + url));
    });

    await renderContacts();

    // Select the visible page via the desktop table's header checkbox.
    const headerCheckbox = document.querySelector("table thead input[type=checkbox]");
    expect(headerCheckbox).not.toBeNull();
    fireEvent.click(headerCheckbox as HTMLElement);

    // The selected bar upgrades to "select all 60 matching" (the button exists
    // in both the mobile section and the selected bar; either does the same).
    const upgrades = await screen.findAllByText("Select all 60 matching");
    fireEvent.click(upgrades[0]);
    await waitFor(() => expect(screen.getByText("All 60 matching selected")).toBeInTheDocument());

    // "Add to list" is available in this mode.
    fireEvent.click(screen.getByText("Add to list"));
    await waitFor(() => expect(screen.getByText("All 60 matching contacts")).toBeInTheDocument());

    // Pick the list in the ListPicker dropdown.
    fireEvent.click(screen.getByRole("button", { name: /select list/i }));
    const option = await screen.findByText("Bulk Target");
    fireEvent.click(option);

    // Confirm: the server-scoped add-all endpoint is used, NOT per-id adds.
    fireEvent.click(screen.getByText("Add all 60 to list"));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/lists/7/contacts/add-all",
        expect.objectContaining({ search: undefined, lead_status: undefined })
      )
    );
    // jsdom mounts no react-hot-toast <Toaster>, so the success toast itself
    // is not asserted — the server call above IS the observable behavior.
  });

  it("adds the ticked contacts as ids for a normal page selection", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === "/lists/7/contacts") {
        return Promise.resolve({ data: { success: true, added: 2, contact_count: 2 } });
      }
      return Promise.reject(new Error("unmocked POST " + url));
    });

    await renderContacts();

    // Tick two contacts on the desktop table.
    const rowChecks = within(document.querySelector("tbody") as HTMLElement).getAllByRole("checkbox");
    fireEvent.click(rowChecks[0]);
    fireEvent.click(rowChecks[1]);

    fireEvent.click(screen.getByText("Add to list"));
    fireEvent.click(await screen.findByRole("button", { name: /select list/i }));
    fireEvent.click(await screen.findByText("Bulk Target"));
    fireEvent.click(screen.getByText("Add to List", { selector: "button" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/lists/7/contacts", expect.arrayContaining([1, 2]))
    );
  });
});
