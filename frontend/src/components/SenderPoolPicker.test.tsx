import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SenderPoolPicker, {
  emptySenderPool,
  senderSplitPreview,
} from "./SenderPoolPicker";

const accounts = [
  { id: 1, name: "Main", from_email: "main@acme.io", is_active: true, sender_type: "brevo" },
  { id: 2, name: "Backup", from_email: "backup@acme.io", is_active: true, sender_type: "brevo" },
  { id: -3, name: "Gmail", from_email: "me@gmail.com", is_active: true, sender_type: "gmail" },
];

describe("SenderPoolPicker", () => {
  it("is off by default and explains the single-sender behaviour", () => {
    render(
      <SenderPoolPicker accounts={accounts} value={emptySenderPool} onChange={vi.fn()} />
    );
    const box = screen.getByRole("checkbox", { name: /multiple senders/i });
    expect(box).not.toBeChecked();
    expect(screen.queryByText(/Round robin/i)).not.toBeInTheDocument();
  });

  it("turning it on seeds a pool of two senders", () => {
    const onChange = vi.fn();
    render(
      <SenderPoolPicker accounts={accounts} value={emptySenderPool} onChange={onChange} />
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /multiple senders/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ ids: [1, 2] })
    );
  });

  it("shows an even split preview and weighted shares only when weighted", () => {
    const { rerender } = render(
      <SenderPoolPicker
        accounts={accounts}
        value={{ ids: [1, 2, -3], distribution: "round_robin", weights: {} }}
        onChange={vi.fn()}
      />
    );
    expect(screen.getByText(/Of the next 100 contacts:/)).toHaveTextContent("Main ≈ 34");
    expect(screen.queryByLabelText(/Share for Main/)).not.toBeInTheDocument();

    rerender(
      <SenderPoolPicker
        accounts={accounts}
        value={{ ids: [1, 2], distribution: "weighted", weights: { "1": 75, "2": 25 } }}
        onChange={vi.fn()}
      />
    );
    expect(screen.getByLabelText(/Share for Main/)).toBeInTheDocument();
    expect(screen.getByText(/Of the next 100 contacts:/)).toHaveTextContent("Main ≈ 75");
  });
});

describe("senderSplitPreview", () => {
  it("handles an empty pool", () => {
    expect(senderSplitPreview(emptySenderPool, accounts)).toEqual([]);
  });
});
