import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ValidatorPage from "./ValidatorPage";
import type { ValidationRow } from "../api/validator";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../api/client", () => ({ default: { get, post } }));

function row(email: string, status: ValidationRow["status"] = "bad", id: number | null = null): ValidationRow {
  return {
    contact_id: id, row: null, name: "", input_email: email, input_phone: null,
    email: { verdict: status === "bad" ? "undeliverable" : status === "good" ? "deliverable" : status, problems: [status === "bad" ? "invalid syntax" : "Mailbox not confirmed"], provider: "builtin" },
    phone: null, status, saved: false, blocked: [], checked_at: "2026-10-10T12:00:00Z",
  };
}

function show(url = "/validator") {
  render(<MemoryRouter initialEntries={[url]}><ValidatorPage /></MemoryRouter>);
}

beforeEach(() => {
  get.mockReset(); post.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  get.mockImplementation(async (url: string) => {
    if (url === "/validator/status") return { data: { engine: "builtin", dns_available: true, smtp_enabled: true, batch_size: 5, api_key_required: false, reacher_configured: false, reacher_key_configured: false } };
    if (url === "/lists/") return { data: { total: 1, items: [{ id: 7, name: "Lagos list", contact_count: 30 }] } };
    throw new Error(`Unexpected GET ${url}`);
  });
  post.mockImplementation(async (url: string, body: any) => {
    if (url === "/validator/selection") return { data: { contact_ids: [1, 2], total: 2 } };
    if (url === "/validator/batch") return { data: { processed: body.contact_ids?.length || body.items?.length, items: body.contact_ids ? body.contact_ids.map((id: number) => row(`person${id}@example.com`, "unknown", id)) : body.items.map((item: any) => ({ ...row(item.email || "", item.email?.includes("@") ? "unknown" : "bad"), row: item.row, name: item.name || "", input_phone: item.phone_number || null })) } };
    if (url === "/validator/self-test") return { data: { passed: true, checked_at: "2026-10-10T12:00:00Z", checks: [{ name: "Reject malformed email", passed: true }], notice: "Local checks only, not live network connectivity." } };
    throw new Error(`Unexpected POST ${url}`);
  });
});

describe("Validator workspace", () => {
  it("explains keys and runs the real engine self-test endpoint", async () => {
    show();
    expect(screen.getByText("No API key required for built-in checks")).toBeInTheDocument();
    await screen.findByText("Built-in engine");
    fireEvent.click(screen.getByRole("button", { name: "Run engine self-test" }));
    expect(await screen.findByText(/Local engine self-test passed/)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/validator/self-test");
    expect(screen.getByText(/not live network connectivity/)).toBeInTheDocument();
  });

  it("accepts invalid single inputs and displays reasons without creating contacts", async () => {
    show();
    fireEvent.click(screen.getByRole("tab", { name: "Single contact" }));
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "broken address" } });
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    expect(await screen.findByText(/Complete · 1 \/ 1/)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/validator/batch", expect.objectContaining({ items: [expect.objectContaining({ email: "broken address" })], save: false }), expect.anything());
    expect(screen.getByText("invalid syntax")).toBeInTheDocument();
    expect(post.mock.calls.some(([url]) => String(url).startsWith("/contacts/"))).toBe(false);
    expect(screen.getByRole("button", { name: "Export all" })).toBeEnabled();
  });

  it("validates ALL matched contacts in batches, never only the first 200/500", async () => {
    const ids = Array.from({ length: 207 }, (_, i) => i + 1);
    const original = post.getMockImplementation()!;
    post.mockImplementation((url, body) => url === "/validator/selection" ? Promise.resolve({ data: { contact_ids: ids, total: ids.length } }) : original(url, body));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Complete · 207 \/ 207/);
    const batches = post.mock.calls.filter(([url]) => url === "/validator/batch");
    expect(batches).toHaveLength(42);
    expect(batches.flatMap(([, body]) => body.contact_ids)).toEqual(ids);
    expect(screen.getByText(/207 results · page 1 of 9/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next result page" }));
    expect(screen.getByText(/207 results · page 2 of 9/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export all" })).toBeEnabled();
  });

  it("receives the exact selected IDs from Contacts, defaults to no changes", async () => {
    show("/validator?scope=ids&contact_ids=3,7");
    expect(screen.getByText("2 selected contacts from the Contacts page.")).toBeInTheDocument();
    expect(screen.getByLabelText("Save results to existing contacts")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Complete · 2 \/ 2/);
    expect(post).toHaveBeenCalledWith("/validator/selection", { scope: "ids", contact_ids: [3, 7] });
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("opens a list with inherited filters and confirms before saving", async () => {
    show("/validator?list_id=7&search=Ada&channel=email&email_state=unverified&lead_status=new");
    await screen.findByRole("option", { name: "Lagos list (30 contacts)" });
    expect(screen.getByRole("tab", { name: "Contact list" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByLabelText("Save results to existing contacts"));
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Complete · 2 \/ 2/);
    expect(post).toHaveBeenCalledWith("/validator/selection", { scope: "list", list_id: 7, search: "Ada", channel: "email", email_state: "unverified", lead_status: "new" });
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/validator/batch", expect.objectContaining({ save: true }), expect.anything());
  });

  it("uploads CSV, auto-maps columns, keeps every bad/empty data row and leading zeros", async () => {
    show();
    fireEvent.click(screen.getByRole("tab", { name: "CSV file" }));
    const file = new File(["Name,Email,Phone\nAda,broken,08034567891\nEmpty,,\nObi,obi@example.com,09034567891"], "contacts.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Upload a CSV to validate"), { target: { files: [file] } });
    await screen.findByText("contacts.csv · 3 rows ready to map");
    await waitFor(() => expect(screen.getByLabelText("Email column")).toHaveValue("1"));
    expect(screen.queryByLabelText("Save results to existing contacts")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Complete · 3 \/ 3/);
    expect(post).toHaveBeenCalledWith("/validator/batch", expect.objectContaining({ save: false, items: [
      { name: "Ada", email: "broken", phone_number: "08034567891", row: 1 },
      { name: "Empty", email: "", phone_number: "", row: 2 },
      { name: "Obi", email: "obi@example.com", phone_number: "09034567891", row: 3 },
    ] }), expect.anything());
  });

  it("preserves results on error and resumes exactly the unfinished batch", async () => {
    const ids = [1, 2, 3, 4, 5, 6];
    const original = post.getMockImplementation()!;
    let failed = false;
    post.mockImplementation((url, body) => {
      if (url === "/validator/selection") return Promise.resolve({ data: { contact_ids: ids, total: 6 } });
      if (url === "/validator/batch" && body.contact_ids[0] === 6 && !failed) { failed = true; return Promise.reject(new Error("Temporary network failure")); }
      return original(url, body);
    });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Incomplete · 5 \/ 6/);
    expect(screen.getByRole("alert")).toHaveTextContent("Temporary network failure");
    fireEvent.click(screen.getByRole("button", { name: "Resume remaining" }));
    await screen.findByText(/Complete · 6 \/ 6/);
    expect(post.mock.calls.filter(([url]) => url === "/validator/batch").map(([, body]) => body.contact_ids)).toEqual([[1, 2, 3, 4, 5], [6], [6]]);
  });

  it("can stop after a batch and resume without losing rows", async () => {
    let complete: (value: any) => void = () => {};
    const first = new Promise(resolve => { complete = resolve; });
    const original = post.getMockImplementation()!;
    post.mockImplementation((url, body) => {
      if (url === "/validator/selection") return Promise.resolve({ data: { contact_ids: [1, 2, 3, 4, 5, 6], total: 6 } });
      if (url === "/validator/batch" && body.contact_ids[0] === 1) return first;
      return original(url, body);
    });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await waitFor(() => expect(post.mock.calls.filter(([url]) => url === "/validator/batch")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Stop after batch" }));
    complete({ data: { items: [1, 2, 3, 4, 5].map(id => row(`${id}@domain.com`, "unknown", id)), processed: 5 } });
    await screen.findByText(/Paused · 5 \/ 6/);
    fireEvent.click(screen.getByRole("button", { name: "Resume remaining" }));
    await screen.findByText(/Complete · 6 \/ 6/);
  });

  it("filters bad, good, risky, unknown and missing results separately", async () => {
    post.mockResolvedValueOnce({ data: { contact_ids: [1, 2, 3, 4, 5], total: 5 } }).mockResolvedValueOnce({ data: { items: ["good", "bad", "risky", "unknown", "missing"].map((s, i) => row(`${s}@domain.com`, s as ValidationRow["status"], i + 1)), processed: 5 } });
    show();
    fireEvent.click(screen.getByRole("button", { name: "Start validation" }));
    await screen.findByText(/Complete · 5 \/ 5/);
    const filters = screen.getByLabelText("Result filters");
    fireEvent.click(within(filters).getByRole("button", { name: /Bad/ }));
    expect(screen.getByText(/1 results · page 1 of 1/)).toBeInTheDocument();
    expect(screen.queryByText("unknown@domain.com", { selector: "p" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export shown (1)" })).toBeEnabled();
  });
});
