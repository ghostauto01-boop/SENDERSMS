import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ListPicker from "./ListPicker";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

const LISTS = [
  { id: 1, name: "VIP Restaurants", description: null, contact_count: 42 },
  { id: 2, name: "Cold Leads", description: null, contact_count: 130 },
];

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiGet.mockImplementation((url: string) => {
    if (url === "/lists/") return Promise.resolve({ data: { items: LISTS } });
    return Promise.reject(new Error("unmocked GET " + url));
  });
  apiPost.mockRejectedValue(new Error("unmocked POST"));
});

describe("ListPicker", () => {
  it("opens a searchable dropdown and selects a list", async () => {
    const onChange = vi.fn();
    render(<ListPicker value="" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /select a list/i }));
    await waitFor(() => expect(screen.getByText("VIP Restaurants")).toBeInTheDocument());
    expect(screen.getByText("Cold Leads")).toBeInTheDocument();

    // Search narrows the options.
    fireEvent.change(screen.getByPlaceholderText("Search lists…"), { target: { value: "cold" } });
    expect(screen.queryByText("VIP Restaurants")).not.toBeInTheDocument();
    expect(screen.getByText("Cold Leads")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Cold Leads"));
    expect(onChange).toHaveBeenCalledWith("2");
  });

  it("creates a list inline and selects it immediately", async () => {
    const onChange = vi.fn();
    apiPost.mockImplementation((url: string, _body: any, config: any) => {
      if (url === "/lists/") {
        return Promise.resolve({
          data: { id: 9, name: config.params.name, description: null },
        });
      }
      return Promise.reject(new Error("unmocked POST " + url));
    });
    render(<ListPicker value="" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /select a list/i }));
    await waitFor(() => expect(screen.getByText("VIP Restaurants")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Create new list"));
    fireEvent.change(screen.getByPlaceholderText("New list name…"), {
      target: { value: "Fresh Imports" },
    });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/lists/", null, { params: { name: "Fresh Imports" } }));
    expect(onChange).toHaveBeenCalledWith("9");
  });

  it("shows the selected list name with its count", async () => {
    render(<ListPicker value="1" onChange={() => {}} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /VIP Restaurants \(42\)/ })).toBeInTheDocument()
    );
  });
});
