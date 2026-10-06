import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SetupPage from "./SetupPage";

/**
 * The setup tutorial. These tests hold it to the promise it makes: the status of
 * every step comes from the server, the reason is stated in words an operator can
 * act on, and the values that have to be pasted into *another* system (the SMS
 * gateway's webhook, Brevo's webhook, an MCP URL) are on the screen with a copy
 * button rather than described in prose.
 */

const apiGet = vi.fn();
const navigate = vi.fn();

vi.mock("../api/client", () => ({
  default: { get: (...a: any[]) => apiGet(...a), post: vi.fn() },
  setSitePasswordRequired: () => {},
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const step = (over: any = {}) => ({
  id: "x", group: "foundation", title: "A step", status: "todo",
  why: "Because it matters.", detail: "Not configured.", steps: [],
  route: null, route_label: null, copy: [], docs: [], verify: "", ...over,
});

const GUIDE = {
  groups: [
    {
      id: "foundation", label: "1. Foundation", hint: "Secrets and the public address.",
      done: 1, total: 2, needs_attention: 1,
      steps: [
        step({
          id: "public-base-url", title: "Set PUBLIC_BASE_URL", status: "todo",
          why: "Inbound SMS, the unsubscribe link and the AI connectors all need it.",
          detail: "Not set.",
          steps: ["Set PUBLIC_BASE_URL to the public HTTPS address.", "Restart the server."],
          verify: "Open the address from a phone on mobile data.",
        }),
        step({
          id: "database", title: "Database", status: "done",
          detail: "Connected to PostgreSQL.", steps: ["Do the thing."],
        }),
      ],
    },
    {
      id: "sms", label: "2. SMS (Nigeria)", hint: "The handset gateway.",
      done: 0, total: 1, needs_attention: 1,
      steps: [
        step({
          id: "sms-webhook", group: "sms", title: "Register the inbound SMS webhook",
          status: "attention",
          detail: "The gateway still has the old address.",
          copy: [{ label: "Webhook URL", value: "https://app.example.test/api/v1/webhooks/smsgateway" }],
          route: "/settings", route_label: "Open Settings → SMS Gateway",
          docs: [{ label: "SMS-Gate setup notes", url: "/SMS_GATE.md" }],
        }),
      ],
    },
    {
      id: "ai", label: "5. AI connectors", hint: "ChatGPT, Claude, Arena.",
      done: 0, total: 1, needs_attention: 1,
      steps: [
        step({
          id: "mcp", group: "ai", title: "Connect an AI assistant", status: "todo",
          detail: "No connector has been tested yet.",
          copy: [
            { label: "ChatGPT MCP URL", value: "https://app.example.test/connectors/chatgpt/mcp" },
            { label: "Claude MCP URL", value: "https://app.example.test/connectors/claude/mcp" },
          ],
          route: "/settings", route_label: "Open Settings → AI (MCP)",
        }),
      ],
    },
    {
      id: "optional", label: "6. Optional", hint: "Notifications and calls.",
      done: 0, total: 1, needs_attention: 0,
      steps: [
        step({ id: "notifications", group: "optional", title: "Push notifications", status: "optional" }),
      ],
    },
  ],
  steps: [],
  progress: { done: 1, blocking: 3, optional: 1, total: 5, percent: 40, ready_to_send: false },
  next: ["public-base-url"],
  environment: {
    app_env: "production", public_base_url: "https://app.example.test", inline_poller: true,
    google_oauth_ready: false, gmail_send_replies: true, gmail_rescue_from_spam: true,
    mcp_oauth_enabled: true,
  },
};

const renderPage = () => render(<MemoryRouter><SetupPage /></MemoryRouter>);

beforeEach(() => {
  apiGet.mockReset();
  navigate.mockReset();
  apiGet.mockImplementation((url: string) => {
    if (url === "/guide") {
      return Promise.resolve({
        data: { ...GUIDE, steps: GUIDE.groups.flatMap((g) => g.steps) },
      });
    }
    return Promise.reject(new Error("unmocked GET " + url));
  });
});

describe("SetupPage", () => {
  it("shows the six sections in order with live progress", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("Set up everything")).toBeInTheDocument());
    expect(screen.getByText("1. Foundation")).toBeInTheDocument();
    expect(screen.getByText("2. SMS (Nigeria)")).toBeInTheDocument();
    expect(screen.getByText("5. AI connectors")).toBeInTheDocument();
    expect(screen.getByText("1 of 5 steps done")).toBeInTheDocument();
    expect(screen.getByText(/3 steps need attention · 1 optional/)).toBeInTheDocument();
    // The environment line is what tells an operator which build they are on.
    expect(screen.getByText("production")).toBeInTheDocument();
    expect(screen.getByText("https://app.example.test")).toBeInTheDocument();
  });

  it("opens the steps that need attention and closes the ones that are done", async () => {
    renderPage();

    // Blocking steps arrive expanded: the instructions are the point.
    await waitFor(() =>
      expect(screen.getByText("Set PUBLIC_BASE_URL to the public HTTPS address.")).toBeInTheDocument(),
    );
    expect(screen.getByText("Restart the server.")).toBeInTheDocument();
    expect(screen.getByText(/How to check:/)).toBeInTheDocument();

    // A done step stays collapsed until asked — its detail is still visible.
    expect(screen.getByText("Connected to PostgreSQL.")).toBeInTheDocument();
    expect(screen.queryByText("Do the thing.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Database"));
    await waitFor(() => expect(screen.getByText("Do the thing.")).toBeInTheDocument());
  });

  it("hands over the values that must be pasted into another system", async () => {
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText("https://app.example.test/api/v1/webhooks/smsgateway"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("https://app.example.test/connectors/chatgpt/mcp")).toBeInTheDocument();
    expect(screen.getByText("https://app.example.test/connectors/claude/mcp")).toBeInTheDocument();

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    fireEvent.click(screen.getAllByRole("button").find((b) => b.querySelector("svg.lucide-copy"))!);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
  });

  it("routes to the screen that fixes the step", async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("Open Settings → SMS Gateway")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Open Settings → SMS Gateway"));
    expect(navigate).toHaveBeenCalledWith("/settings");

    fireEvent.click(screen.getByText("Open Settings → AI (MCP)"));
    expect(navigate).toHaveBeenCalledWith("/settings");
  });

  it("filters to only what needs attention", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByText("Needs attention (3)")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Needs attention (3)"));

    // Everything still to do or to fix stays; what is done or optional goes.
    await waitFor(() => expect(screen.getByText("Register the inbound SMS webhook")).toBeInTheDocument());
    expect(screen.getByText("Set PUBLIC_BASE_URL")).toBeInTheDocument();     // todo
    expect(screen.getByText("Connect an AI assistant")).toBeInTheDocument(); // todo
    expect(screen.queryByText("Database")).not.toBeInTheDocument();          // done
    expect(screen.queryByText("Push notifications")).not.toBeInTheDocument(); // optional
    // The Foundation group survives (it has a todo step) but SMS/AI keep theirs.
    expect(screen.getByText("1. Foundation")).toBeInTheDocument();
  });

  it("celebrates when nothing blocks sending", async () => {
    apiGet.mockImplementation((url: string) =>
      url === "/guide"
        ? Promise.resolve({
            data: {
              ...GUIDE,
              steps: GUIDE.groups.flatMap((g) => g.steps),
              progress: { done: 5, blocking: 0, optional: 0, total: 5, percent: 100,
                          ready_to_send: true },
            },
          })
        : Promise.reject(new Error("unmocked")),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText("You can send")).toBeInTheDocument());
    expect(screen.getByText("Nothing is blocking you")).toBeInTheDocument();
  });

  it("tells the sidebar the count changed when it re-checks", async () => {
    const events: string[] = [];
    window.addEventListener("setup-guide-updated", () => events.push("updated"));
    renderPage();

    await waitFor(() => expect(events).toEqual(["updated"]));
    fireEvent.click(screen.getByText("Re-check"));
    await waitFor(() => expect(events.length).toBe(2));
  });
});
