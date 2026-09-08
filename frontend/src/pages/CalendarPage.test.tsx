import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CalendarPage from "./CalendarPage";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const MEETING = (() => {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 10, 0, 0);
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 10, 30, 0);
  return {
    id: 7,
    title: "Site visit",
    description: null,
    event_type: "meeting",
    status: "scheduled",
    starts_at: start.toISOString(),
    ends_at: end.toISOString(),
    all_day: false,
    location: "12 Allen Avenue",
    meeting_link: null,
    contact_id: 3,
    contact_name: "Ada Obi",
    conversation_id: 1,
    campaign_id: null,
    tags: ["vip"],
    send_invite_sms: true,
    invite_template_id: null,
    invite_body: null,
    invite_sent: false,
    send_sms_reminder: true,
    reminder_minutes: [60, 15],
    reminder_template_id: null,
    reminder_body: null,
    reminders_sent: {},
    outcome_notes: null,
    attendees: [{ id: 1, contact_id: 3, status: "invited", notified: false, name: "Ada Obi", phone: "+2348012345678" }],
    created_at: start.toISOString(),
    updated_at: start.toISOString(),
  };
})();

function renderCalendar() {
  return render(
    <MemoryRouter initialEntries={["/calendar"]}>
      <CalendarPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiGet.mockImplementation((url: string) => {
    if (url === "/calendar/") return Promise.resolve({ data: { total: 1, items: [MEETING] } });
    if (url === "/calendar/tags") return Promise.resolve({ data: { items: [{ name: "vip", count: 1 }] } });
    return Promise.reject(new Error("unmocked GET " + url));
  });
});

describe("CalendarPage", () => {
  it("loads the visible range and shows the meeting on the grid", async () => {
    renderCalendar();

    await waitFor(() => expect(screen.getAllByText("Site visit").length).toBeGreaterThan(0));
    // The range query went out (month grid spans ~6 weeks).
    const call = apiGet.mock.calls.find(([url]) => url === "/calendar/");
    expect(call).toBeTruthy();
    expect(call![1].params.date_from).toBeTruthy();
    expect(call![1].params.date_to).toBeTruthy();
    // Tag filter chips come from the server.
    expect(await screen.findByText("vip · 1")).toBeInTheDocument();
  });

  it("switches to the agenda view and opens the booking form", async () => {
    renderCalendar();
    await waitFor(() => expect(screen.getAllByText("Site visit").length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole("button", { name: /^agenda$/i }));
    await waitFor(() => expect(screen.getByText("Upcoming")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /new meeting/i }));
    await waitFor(() => expect(screen.getByText("Book a meeting")).toBeInTheDocument());
    // Booking form essentials: contacts, SMS invite, reminders.
    expect(screen.getByText(/contacts \*/i)).toBeInTheDocument();
    expect(screen.getByText("SMS invite & reminders")).toBeInTheDocument();
  });

  it("opens a meeting for editing when its chip is clicked", async () => {
    renderCalendar();
    await waitFor(() => expect(screen.getAllByText("Site visit").length).toBeGreaterThan(0));

    apiGet.mockImplementation((url: string) => {
      if (url === "/calendar/") return Promise.resolve({ data: { total: 1, items: [MEETING] } });
      if (url === "/calendar/tags") return Promise.resolve({ data: { items: [] } });
      if (url === "/calendar/7") return Promise.resolve({ data: MEETING });
      if (url === "/contacts/3")
        return Promise.resolve({ data: { id: 3, first_name: "Ada", phone_number: "+2348012345678" } });
      return Promise.reject(new Error("unmocked GET " + url));
    });

    fireEvent.click(screen.getAllByText("Site visit")[0]);
    await waitFor(() => expect(screen.getByText("Edit meeting")).toBeInTheDocument());
    expect(screen.getByDisplayValue("Site visit")).toBeInTheDocument();
  });
});
