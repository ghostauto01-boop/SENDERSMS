/**
 * UI simulation: the list editor — WhatsApp buttons on member rows and the
 * add-contacts picker's "Select all N matching" bulk add.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ListsPage from "./ListsPage";

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
    first_name: `Member${id}`,
    last_name: null,
    business_name: null,
    phone_number: `+23481${String(id).padStart(8, "0")}`,
    lead_status: "new",
    ...over,
  };
}

const MEMBERS = Array.from({ length: 5 }, (_, i) => makeContact(i + 1));
const AVAILABLE = Array.from({ length: 50 }, (_, i) => makeContact(100 + i));

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  openSpy.mockClear();
  apiGet.mockImplementation((url: string, config: any) => {
    if (url === "/lists/") {
      return Promise.resolve({
        data: { items: [{ id: 3, name: "VIP Customers", description: null, contact_count: 5 }] },
      });
    }
    if (url === "/lists/3/contacts") {
      return Promise.resolve({ data: { total: 5, items: MEMBERS } });
    }
    if (url === "/contacts/") {
      // Picker calls with exclude_list_id=3
      return Promise.resolve({ data: { total: 58, items: AVAILABLE } });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });
  apiPost.mockRejectedValue(new Error("unmocked POST"));
});

describe("ListsPage — list editor", () => {
  it("shows WhatsApp call buttons on member rows", async () => {
    render(<ListsPage />);
    await waitFor(() => expect(screen.getByText("VIP Customers")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Edit contacts"));
    await waitFor(() => expect(screen.getByText("Edit VIP Customers")).toBeInTheDocument());

    const waButtons = await screen.findAllByTitle(/on WhatsApp/i);
    expect(waButtons.length).toBeGreaterThanOrEqual(5);

    // Clicking one opens a wa.me deep link for that member.
    fireEvent.click(waButtons[0]);
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringMatching(/^https:\/\/wa\.me\/23481\d{8}$/),
      "_blank",
      "noopener"
    );
  });

  it("adds ALL picker matches to the list in one server call", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === "/lists/3/contacts/add-all") {
        return Promise.resolve({ data: { success: true, added: 58, matched: 58, contact_count: 63 } });
      }
      return Promise.reject(new Error("unmocked POST " + url));
    });

    render(<ListsPage />);
    await waitFor(() => expect(screen.getByText("VIP Customers")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Edit contacts"));
    await waitFor(() => expect(screen.getByText("Edit VIP Customers")).toBeInTheDocument());

    // Open the add-contacts picker.
    fireEvent.click(screen.getByText("Add Contacts"));
    await waitFor(() => expect(screen.getByText("Add contacts to VIP Customers")).toBeInTheDocument());
    // Wait until picker rows have loaded (the "58 available" header + pagination both mention it).
    await screen.findAllByText(/58 available/);

    // Three-state select: "Select all" (50 on this page) -> "Select all 58 matching".
    fireEvent.click(screen.getAllByText("Select all", { selector: "button" })[0]);
    const upgrade = await screen.findByText("Select all 58 matching");
    fireEvent.click(upgrade);
    fireEvent.click(screen.getByText("Add all 58 matching contacts"));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/lists/3/contacts/add-all", { search: undefined })
    );
    // jsdom mounts no react-hot-toast <Toaster>, so the toast is not asserted.
  });

  it("still adds a normal ticked selection as contact ids", async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === "/lists/3/contacts") {
        return Promise.resolve({ data: { success: true, added: 2, contact_count: 7 } });
      }
      return Promise.reject(new Error("unmocked POST " + url));
    });

    render(<ListsPage />);
    await waitFor(() => expect(screen.getByText("VIP Customers")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Edit contacts"));
    await waitFor(() => expect(screen.getByText("Edit VIP Customers")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Add Contacts"));
    await screen.findAllByText(/58 available/); // picker rows loaded

    // Picker rows render above the member rows: the first two checkboxes are picker rows.
    const checks = await screen.findAllByRole("checkbox");
    fireEvent.click(checks[0]);
    fireEvent.click(checks[1]);

    fireEvent.click(screen.getByText(/Add 2 selected contacts/));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/lists/3/contacts", [100, 101])
    );
  });
});
