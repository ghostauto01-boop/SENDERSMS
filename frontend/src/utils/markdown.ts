/**
 * A small, safe markdown renderer for agent replies.
 *
 * Why not a library: the agent streams text token by token, so whatever renders
 * it must be cheap to re-run on every chunk, must never execute HTML (the model
 * echoes customer messages back at us), and must degrade gracefully while the
 * message is still half-written — an unclosed ** or ``` must not swallow the
 * rest of the chat.
 *
 * Two deliberate choices:
 *
 *   1. Everything is escaped first, then a fixed set of patterns is applied.
 *      There is no `dangerouslySetInnerHTML` anywhere; the output is React
 *      elements, so a customer called "<script>" is a customer called
 *      "<script>".
 *   2. Unclosed markers are closed for us before parsing. A streaming "**Bold"
 *      renders as bold from the first character instead of flickering.
 */

export type InlineToken =
  | { type: "text"; text: string }
  | { type: "bold"; text: string }
  | { type: "italic"; text: string }
  | { type: "code"; text: string }
  | { type: "link"; text: string; href: string }
  | { type: "phone"; text: string };

export type Block =
  | { type: "paragraph"; tokens: InlineToken[] }
  | { type: "heading"; level: number; tokens: InlineToken[] }
  | { type: "list"; ordered: boolean; items: InlineToken[][] }
  | { type: "code"; language: string; text: string }
  | { type: "quote"; tokens: InlineToken[] }
  | { type: "table"; header: string[]; rows: string[][] }
  | { type: "rule" };

const PHONE = /^\+?\d[\d\s().-]{6,}\d$/;

/** Close markers the stream has not finished sending yet. */
export function balanceMarkers(text: string): string {
  const fences = (text.match(/```/g) || []).length;
  let out = text;
  if (fences % 2 === 1) out += "\n```";
  const bold = (out.match(/\*\*/g) || []).length;
  if (bold % 2 === 1) out += "**";
  // Single asterisks/underscores only matter outside the pairs we just fixed.
  const withoutPairs = out.replace(/\*\*/g, "");
  if ((withoutPairs.match(/\*/g) || []).length % 2 === 1) out += "*";
  return out;
}

export function parseInline(raw: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let rest = raw;
  const push = (token: InlineToken) => {
    const last = tokens[tokens.length - 1];
    if (token.type === "text" && last && last.type === "text") last.text += token.text;
    else tokens.push(token);
  };

  while (rest.length) {
    let match: RegExpExecArray | null;

    if ((match = /^`([^`]+)`/.exec(rest))) {
      push({ type: "code", text: match[1] });
      rest = rest.slice(match[0].length);
      continue;
    }
    if ((match = /^\*\*([\s\S]+?)\*\*/.exec(rest))) {
      push({ type: "bold", text: match[1] });
      rest = rest.slice(match[0].length);
      continue;
    }
    if ((match = /^\*([^*\n]+)\*/.exec(rest)) || (match = /^_([^_\n]+)_/.exec(rest))) {
      push({ type: "italic", text: match[1] });
      rest = rest.slice(match[0].length);
      continue;
    }
    if ((match = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/.exec(rest))) {
      push({ type: "link", text: match[1], href: match[2] });
      rest = rest.slice(match[0].length);
      continue;
    }
    if ((match = /^(https?:\/\/[^\s<]+)/.exec(rest))) {
      const href = match[1].replace(/[.,;:)]+$/, "");
      push({ type: "link", text: href, href });
      rest = rest.slice(href.length);
      continue;
    }
    // A bare phone number is the one thing operators paste constantly: make it
    // copyable in one tap.
    if ((match = /^\+?\d[\d\s().-]{6,}\d/.exec(rest)) && PHONE.test(match[0].trim())) {
      push({ type: "phone", text: match[0].trim() });
      rest = rest.slice(match[0].length);
      continue;
    }
    push({ type: "text", text: rest[0] });
    rest = rest.slice(1);
  }
  return tokens;
}

/** Turn markdown into blocks. Never throws; unknown syntax becomes text. */
export function parseMarkdown(source: string, streaming = false): Block[] {
  const text = streaming ? balanceMarkers(source || "") : source || "";
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const joined = paragraph.join(" ").trim();
    if (joined) blocks.push({ type: "paragraph", tokens: parseInline(joined) });
    paragraph = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      continue;
    }

    // ``` fenced code
    const fence = /^```([\w+-]*)\s*$/.exec(trimmed);
    if (fence) {
      flushParagraph();
      const language = fence[1] || "";
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        body.push(lines[index]);
        index += 1;
      }
      blocks.push({ type: "code", language, text: body.join("\n") });
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph();
      blocks.push({ type: "rule" });
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      blocks.push({
        type: "heading",
        level: heading[1].length,
        tokens: parseInline(heading[2]),
      });
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      flushParagraph();
      const quoted: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index].trim())) {
        quoted.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      index -= 1;
      blocks.push({ type: "quote", tokens: parseInline(quoted.join(" ")) });
      continue;
    }

    // A pipe table: at least a header row and a separator row.
    if (trimmed.includes("|") && /^\|?[\s:-]+\|/.test((lines[index + 1] || "").trim())) {
      flushParagraph();
      const split = (row: string) =>
        row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
      const header = split(trimmed);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(split(lines[index]));
        index += 1;
      }
      index -= 1;
      blocks.push({ type: "table", header, rows });
      continue;
    }

    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
    if (bullet || numbered) {
      flushParagraph();
      const ordered = Boolean(numbered);
      const items: InlineToken[][] = [];
      while (index < lines.length) {
        const current = lines[index].trim();
        const m = ordered ? /^\d+[.)]\s+(.*)$/.exec(current) : /^[-*+]\s+(.*)$/.exec(current);
        if (!m) break;
        items.push(parseInline(m[1]));
        index += 1;
      }
      index -= 1;
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    paragraph.push(trimmed);
  }
  flushParagraph();
  return blocks;
}
