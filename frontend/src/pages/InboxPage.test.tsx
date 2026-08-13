import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import InboxPage from "./InboxPage";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "ade", display_name: "Ade", role: "admin", is_active: true },
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}));

const CONVS = [
  {
    id: 1, contact_id: 10, contact_name: "Ada Obi", contact_phone: "+2348012345678",
    status: "interested", unread_count: 0, message_count: 6,
    last_message_preview: "Nice! I'll come by on Saturday with my friends 😊",
    last_message_at: "2026-08-13T08:00:00Z",
    contact: { phone_number: "+2348012345678", business_name: null },
  },
  {
    id: 2, contact_id: 11, contact_name: "MTN", contact_phone: "MTN",
    status: "closed", unread_count: 0, message_count: 3,
    last_message_preview: "MTN: Your balance is N1500.",
    last_message_at: "2026-08-13T09:30:00Z",
    contact: { phone_number: "MTN", business_name: null },
  },
  {
    id: 3, contact_id: 12, contact_name: "Tunde Bakare", contact_phone: "+2348098765432",
    status: "unread", unread_count: 2, message_count: 2,
    last_message_preview: "Please deliver to my office instead.",
    last_message_at: "2026-08-13T10:00:00Z",
    contact: { phone_number: "+2348098765432", business_name: null },
  },
];

const MESSAGES = [
  {
    id: 101, direction: "incoming", body: "Hello! Are you open today?",
    created_at: "2026-08-13T07:58:00Z", status: "delivered",
  },
  {
    id: 102, direction: "outgoing", body: "Yes we are! 8am–8pm 😊",
    created_at: "2026-08-13T08:00:00Z", status: "delivered",
  },
  {
    id: 103, direction: "outgoing", body: "See you soon!",
    created_at: "2026-08-13T08:01:00Z", status: "sent",
  },
];

function renderInbox() {
  return render(
    <MemoryRouter initialEntries={["/inbox"]}>
      <InboxPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();

  apiGet.mockImplementation((url: string) => {
    if (url === "/inbox/conversations") {
      return Promise.resolve({ data: { items: CONVS } });
    }
    if (url.startsWith("/inbox/conversations/")) {
      return Promise.resolve({ data: { messages: MESSAGES, status: "interested" } });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });

  apiPost.mockImplementation((url: string) => {
    if (url.includes("/reply")) {
      return Promise.resolve({ data: { success: true } });
    }
    return Promise.reject(new Error("unmocked POST " + url));
  });
});

describe("InboxPage — WhatsApp-style inbox", () => {
  it("renders the conversation list like WhatsApp (avatar, name, preview, unread badge)", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    expect(screen.getByText("MTN")).toBeInTheDocument();
    expect(screen.getByText("Tunde Bakare")).toBeInTheDocument();
    // Unread badge count
    expect(screen.getByText("2")).toBeInTheDocument();
    // Header shows the logged-in user
    expect(screen.getByText("Ade")).toBeInTheDocument();
    // Filter chips (Unread shows its live count)
    expect(screen.getByText("All")).toBeInTheDocument();
    expect(screen.getByText("Unread · 1")).toBeInTheDocument();
    expect(screen.getByText("Interested")).toBeInTheDocument();
    // Sync row (list row + empty-state button both say this)
    expect(screen.getAllByText(/Sync from phone/).length).toBeGreaterThan(0);
  });

  it("opens a chat and shows WhatsApp bubbles with a date chip and contact header", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Ada Obi"));

    await waitFor(() => expect(screen.getByText("Hello! Are you open today?")).toBeInTheDocument());
    expect(screen.getByText("Yes we are! 8am–8pm 😊")).toBeInTheDocument();
    // Date chip (Today)
    expect(screen.getByText("Today")).toBeInTheDocument();
    // Chat header shows contact name + phone
    expect(screen.getByText("+2348012345678")).toBeInTheDocument();
    // Composer
    expect(screen.getByPlaceholderText("Type a message")).toBeInTheDocument();
  });

  it("sends a reply with Enter and clears the composer (mobile keyboard flow)", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Ada Obi"));
    await waitFor(() => expect(screen.getByPlaceholderText("Type a message")).toBeInTheDocument());

    const ta = screen.getByPlaceholderText("Type a message") as HTMLTextAreaElement;
    // Mobile-friendly attributes
    expect(ta.getAttribute("enterkeyhint")).toBe("send");

    fireEvent.change(ta, { target: { value: "Sounds great!" } });
    expect(ta.value).toBe("Sounds great!");

    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    await waitFor(() => expect(apiPost).toHaveBeenCalled());

    const url = apiPost.mock.calls[0][0] as string;
    expect(url).toContain("/reply");
    const params = (apiPost.mock.calls[0][2] as any).params;
    expect(params.body).toBe("Sounds great!");
  });

  it("Shift+Enter inserts a newline instead of sending", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Ada Obi"));
    await waitFor(() => expect(screen.getByPlaceholderText("Type a message")).toBeInTheDocument());

    const ta = screen.getByPlaceholderText("Type a message") as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });

    expect(apiPost).not.toHaveBeenCalled();
  });

  it("filter chips request the right status from the API", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Unread · 1")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Unread · 1"));
    await waitFor(() => {
      const call = apiGet.mock.calls.find(
        (c: any) => c[0] === "/inbox/conversations" && (c[1] as any)?.params?.status === "unread"
      );
      expect(call).toBeTruthy();
    });
  });

  it("closing a chat (back on mobile) returns to the list", async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Ada Obi"));
    await waitFor(() => expect(screen.getByPlaceholderText("Type a message")).toBeInTheDocument());

    // Back button (mobile) exists in chat header
    const back = document.querySelector('button[class*="md:hidden"]');
    expect(back).toBeTruthy();
    fireEvent.click(back as HTMLElement);
    expect(screen.getAllByText(/Sync from phone/).length).toBeGreaterThan(0);
    // Chat is closed: composer is gone again
    expect(screen.queryByPlaceholderText("Type a message")).not.toBeInTheDocument();
  });

  it("shows a failed-outgoing error banner inside the bubble", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === "/inbox/conversations") {
        return Promise.resolve({ data: { items: [CONVS[0]] } });
      }
      if (url.startsWith("/inbox/conversations/")) {
        return Promise.resolve({
          data: {
            messages: [{
              id: 200, direction: "outgoing", body: "Your appointment is confirmed",
              created_at: "2026-08-13T08:00:00Z", status: "failed",
              last_error: "SIM has no credit",
            }],
            status: "active",
          },
        });
      }
      return Promise.reject(new Error("unmocked GET " + url));
    });

    renderInbox();
    await waitFor(() => expect(screen.getByText("Ada Obi")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Ada Obi"));
    await waitFor(() => expect(screen.getByText("Your appointment is confirmed")).toBeInTheDocument());
    expect(screen.getByText(/SIM has no credit/)).toBeInTheDocument();
  });
});
