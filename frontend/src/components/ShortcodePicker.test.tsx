/**
 * Note the fixtures store shortcodes BARE ("first_name"), exactly as
 * /variables/ returns them; the braces are a display/insert concern.
 *
 * The point of the picker is that it inserts AT THE CURSOR. Appending to the
 * end would be much simpler and looks fine in a screenshot, so these tests pin
 * the behaviour that actually matters: pick a shortcode mid-sentence and carry
 * on typing where you left off.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import ShortcodePicker, { clearShortcodeCache } from "./ShortcodePicker";
import api from "../api/client";

vi.mock("../api/client", () => ({
  default: { get: vi.fn() },
}));

const VARIABLES = [
  { id: 1, field_key: "first_name", label: "First name", shortcode: "first_name",
    fallback_text: null, description: null, source: "csv", is_active: true,
    contact_count: 12, sample_value: "Ada", created_at: "", updated_at: "" },
  { id: 2, field_key: "pain_point", label: "Pain point", shortcode: "Pain point",
    fallback_text: null, description: null, source: "csv", is_active: true,
    contact_count: 9, sample_value: "no website", created_at: "", updated_at: "" },
];

/** Minimal host mirroring how the composers wire the picker up. */
function Harness({ initial = "" }: { initial?: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState(initial);
  return (
    <div>
      <label htmlFor="body">Body</label>
      <textarea id="body" ref={ref} value={value} onChange={e => setValue(e.target.value)} />
      <ShortcodePicker targetRef={ref} value={value} onChange={setValue} />
    </div>
  );
}

beforeEach(() => {
  clearShortcodeCache();
  (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { items: VARIABLES } });
});
afterEach(cleanup);

describe("ShortcodePicker", () => {
  it("lists the shortcodes from the registry", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /shortcode/i }));
    expect(await screen.findByText("{{first_name}}")).toBeInTheDocument();
    expect(screen.getByText("{{Pain point}}")).toBeInTheDocument();
  });

  it("inserts at the caret, not at the end, and leaves the caret after it", async () => {
    const user = userEvent.setup();
    render(<Harness initial="Hi, how are you?" />);
    const ta = screen.getByLabelText("Body") as HTMLTextAreaElement;

    // Put the caret right after "Hi" (before the comma).
    ta.focus();
    ta.setSelectionRange(2, 2);
    await user.click(screen.getByRole("button", { name: /shortcode/i }));
    await user.click(await screen.findByText("{{first_name}}"));

    await waitFor(() =>
      expect(ta.value).toBe("Hi {{first_name}}, how are you?"),
    );
    // Caret sits just after the inserted code so typing continues naturally.
    await waitFor(() => expect(ta.selectionStart).toBe("Hi {{first_name}}".length));
  });

  it("lets the user keep typing after an insertion", async () => {
    const user = userEvent.setup();
    render(<Harness initial="Hi" />);
    const ta = screen.getByLabelText("Body") as HTMLTextAreaElement;
    ta.focus();
    ta.setSelectionRange(2, 2);

    await user.click(screen.getByRole("button", { name: /shortcode/i }));
    await user.click(await screen.findByText("{{first_name}}"));
    await waitFor(() => expect(ta.value).toBe("Hi {{first_name}}"));

    await user.type(ta, " — quick question");
    expect(ta.value).toBe("Hi {{first_name}} — quick question");
  });

  it("filters the list by label or shortcode", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /shortcode/i }));
    await user.type(await screen.findByLabelText("Search variables"), "pain");
    expect(screen.getByText("{{Pain point}}")).toBeInTheDocument();
    expect(screen.queryByText("{{first_name}}")).not.toBeInTheDocument();
  });

  it("explains itself when no variables have been imported yet", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { items: [] } });
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /shortcode/i }));
    expect(await screen.findByText(/import a csv/i)).toBeInTheDocument();
  });
});
