import { describe, expect, it } from "vitest";
import { balanceMarkers, parseInline, parseMarkdown } from "./markdown";

/**
 * The agent streams half-written markdown, so these cover the two cases that
 * actually broke the old chat: an unclosed ** swallowing the rest of the reply,
 * and customer text being rendered as HTML.
 */
describe("markdown", () => {
  it("closes a bold marker that is still streaming", () => {
    expect(balanceMarkers("Here is **very imp")).toBe("Here is **very imp**");
    expect(balanceMarkers("**done**")).toBe("**done**");
  });

  it("closes an open code fence", () => {
    const balanced = balanceMarkers("Run this:\n```js\nconst a = 1");
    expect(balanced.endsWith("```")).toBe(true);
  });

  it("parses a list, a heading and inline emphasis", () => {
    const blocks = parseMarkdown("### Plan\n- **First** step\n- Second *step*");
    expect(blocks[0]).toMatchObject({ type: "heading", level: 3 });
    expect(blocks[1]).toMatchObject({ type: "list", ordered: false });
    const list = blocks[1] as any;
    expect(list.items[0][0]).toEqual({ type: "bold", text: "First" });
    expect(list.items[1]).toEqual([
      { type: "text", text: "Second " },
      { type: "italic", text: "step" },
    ]);
  });

  it("keeps customer-supplied HTML as text", () => {
    const tokens = parseInline('<script>alert("x")</script>');
    const text = tokens.map((token) => ("text" in token ? token.text : "")).join("");
    expect(text).toContain("<script>");
    // It is a plain text token — there is no html token type at all.
    expect(tokens.every((token) => token.type !== ("html" as any))).toBe(true);
  });

  it("recognises phone numbers so they can be copied in one tap", () => {
    expect(parseInline("+234 803 123 4567")).toEqual([
      { type: "phone", text: "+234 803 123 4567" },
    ]);
  });

  it("never throws on the punctuation a stream starts with", () => {
    for (const fragment of ["", "*", "**", "`", "```", "|", "-", ">", "#"]) {
      expect(() => parseMarkdown(fragment, true)).not.toThrow();
    }
  });

  it("renders a pipe table", () => {
    const blocks = parseMarkdown("| Name | Sent |\n| --- | --- |\n| Ada | 12 |");
    expect(blocks[0]).toMatchObject({
      type: "table",
      header: ["Name", "Sent"],
      rows: [["Ada", "12"]],
    });
  });
});
