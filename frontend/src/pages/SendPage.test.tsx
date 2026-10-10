import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChannelProvider, useChannel } from "../hooks/useChannel";
import SendPage from "./SendPage";

vi.mock("../api/client", () => ({ default: { get: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }) } }));
vi.mock("../api/email", () => ({ default: { reference: vi.fn().mockResolvedValue({}), listAccounts: vi.fn().mockResolvedValue({ items: [] }) } }));
vi.mock("../components/EmailSendPanel", () => ({ default: () => <div>Email composer</div> }));
vi.mock("../hooks/useVisiblePolling", () => ({ useVisiblePolling: () => {} }));

function Toggle() {
  const { setChannel } = useChannel();
  return <><button onClick={() => setChannel("sms")}>Test SMS</button><button onClick={() => setChannel("email")}>Test Email</button></>;
}
afterEach(() => localStorage.removeItem("sendersms.channel"));

describe("Send page channel switching", () => {
  it("switches SMS → Email → SMS without changing a component's hook order", async () => {
    render(<MemoryRouter><ChannelProvider><Toggle /><SendPage /></ChannelProvider></MemoryRouter>);
    await screen.findByRole("heading", { name: "Send SMS" });
    fireEvent.click(screen.getByText("Test Email"));
    expect(await screen.findByText("Email composer")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Test SMS"));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Send SMS" })).toBeInTheDocument());
    expect(screen.queryByText("Email composer")).not.toBeInTheDocument();
  });
});
