import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AgentComposer from "./AgentComposer";

describe("AgentComposer", () => {
  it("sends on Enter, but keeps Shift+Enter and composition in the draft", () => {
    const onSend = vi.fn();
    render(<AgentComposer onSend={onSend} busy={false} />);
    const input = screen.getByLabelText("Message the agent");
    fireEvent.change(input, { target: { value: "Find my contacts" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("Find my contacts");
    expect(input).toHaveValue("");
  });
  it("offers microphone and file controls", () => {
    const openFiles = vi.fn();
    render(<AgentComposer onSend={vi.fn()} busy={false} onOpenFiles={openFiles} />);
    expect(screen.getByRole("button", { name: "Record a voice note" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    expect(openFiles).toHaveBeenCalledOnce();
  });
});
