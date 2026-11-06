import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "./SettingsPage";
import { ThemeProvider, PALETTE_LIST } from "../hooks/useTheme";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
    post: (...args: any[]) => mockPost(...args),
    put: (...args: any[]) => mockPut(...args),
    delete: (...args: any[]) => mockDelete(...args),
  },
}));

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin" }, noteAccess: vi.fn() }),
}));

describe("Settings Appearance Studio & Multi-Phone SMS Manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();

    mockGet.mockImplementation((url: string) => {
      if (url === "/settings/notifications") {
        return Promise.resolve({ data: { providers: [] } });
      }
      if (url === "/settings/compliance") {
        return Promise.resolve({ data: {} });
      }
      if (url === "/settings/gateway") {
        return Promise.resolve({
          data: {
            configured: true,
            render_sender_active: true,
            username: "render_primary_user",
            base_url: "https://api.sms-gate.app/3rdparty/v1",
            sim_number: 1,
            routing_mode: "render_first",
            sim_failover: true,
            phones: [
              {
                id: "env",
                name: "Render Primary Sender",
                is_render_env: true,
                protected: true,
                is_enabled: true,
                is_default: true,
                username: "render_primary_user",
                base_url: "https://api.sms-gate.app/3rdparty/v1",
                sim_number: 1,
                mode: "cloud",
                has_password: true,
                password_masked: "re***pw",
                connection_status: "online",
              },
            ],
          },
        });
      }
      if (url === "/settings/gateway/webhooks") {
        return Promise.resolve({
          data: {
            configured: true,
            public_base_url_set: true,
            signing_secret_set: true,
            healthy: true,
            webhook_url: "https://sendersms.onrender.com/api/v1/webhooks/smsgateway",
            registered_events: ["sms:received", "sms:delivered"],
            required_events: ["sms:received"],
            missing_events: [],
            webhooks: [],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  it("renders all 10 professional color palettes in Appearance Studio and updates CSS variables live", async () => {
    window.history.pushState({}, "", "/settings?tab=appearance");
    render(
      <MemoryRouter initialEntries={["/settings?tab=appearance"]}>
        <ThemeProvider>
          <SettingsPage />
        </ThemeProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Appearance & Theme Studio/i)).toBeInTheDocument();
    });

    expect(PALETTE_LIST).toHaveLength(10);
    for (const p of PALETTE_LIST) {
      expect(screen.getByTestId(`palette-${p.id}`)).toBeInTheDocument();
    }

    fireEvent.click(screen.getByTestId("palette-sapphire"));
    expect(document.documentElement.dataset.palette).toBe("sapphire");
    expect(localStorage.getItem("sendify.palette")).toBe("sapphire");
  });

  it("connects an additional SMS sender phone without disabling the Render Primary sender", async () => {
    window.history.pushState({}, "", "/settings?tab=gateway");
    mockPost.mockImplementation((url: string, body: any) => {
      if (url === "/settings/gateway/phones") {
        return Promise.resolve({
          data: {
            success: true,
            phone: { id: "2", name: body.name, username: body.username },
            phones: [
              {
                id: "env",
                name: "Render Primary Sender",
                is_render_env: true,
                protected: true,
                is_enabled: true,
                username: "render_primary_user",
                base_url: "https://api.sms-gate.app/3rdparty/v1",
                sim_number: 1,
                connection_status: "online",
              },
              {
                id: "2",
                name: body.name,
                is_render_env: false,
                protected: false,
                is_enabled: true,
                username: body.username,
                base_url: body.base_url,
                sim_number: body.sim_number,
                connection_status: "unknown",
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: { success: true } });
    });

    render(
      <MemoryRouter initialEntries={["/settings?tab=gateway"]}>
        <ThemeProvider>
          <SettingsPage />
        </ThemeProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Render Primary Sender")).toBeInTheDocument();
    });
    expect(screen.getByText(/Render Env · Protected/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Connect Phone/i }));

    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Abuja Ops Line #2/i), {
      target: { value: "Lagos Dispatch Phone #2" },
    });
    fireEvent.change(screen.getByPlaceholderText("From SMS-Gate app"), {
      target: { value: "lagos_sms_user" },
    });
    fireEvent.change(screen.getByPlaceholderText("Password from app"), {
      target: { value: "lagos_sms_secret" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Save & Connect Sender Phone/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/settings/gateway/phones",
        expect.objectContaining({
          name: "Lagos Dispatch Phone #2",
          username: "lagos_sms_user",
          password: "lagos_sms_secret",
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Lagos Dispatch Phone #2")).toBeInTheDocument();
      expect(screen.getByText("Render Primary Sender")).toBeInTheDocument();
    });
  });
});
