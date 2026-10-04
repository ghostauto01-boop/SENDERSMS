import { useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  Bold,
  Code2,
  Eye,
  Heading1,
  Heading2,
  Image as ImageIcon,
  Italic,
  Link2,
  List,
  ListOrdered,
  Minus,
  MousePointerClick,
  Paperclip,
  Quote,
  Table2,
  Underline,
  X,
} from "lucide-react";

/**
 * The email body editor.
 *
 * Plain text is always the source of truth (so variables like
 * ``{{first_name}}`` keep working and every send has a text fallback), and the
 * rich tools write HTML alongside it. Images are inserted by URL — the same
 * thing Brevo's editor does and the only form that survives being sent — and
 * files are attached as base64 through the API.
 */

export type AttachmentPayload = {
  name: string;
  content_base64: string;
  content_type?: string;
  size?: number;
};

const MAX_FILE_BYTES = 4 * 1024 * 1024;

export default function RichEmailEditor({
  body,
  onBody,
  html,
  onHtml,
  attachments,
  onAttachments,
  variables = [
    "{{first_name}}",
    "{{business_name}}",
    "{{city}}",
    "{{state}}",
    "{{website}}",
    "{{industry}}",
  ],
}: {
  body: string;
  onBody: (value: string) => void;
  html: string;
  onHtml: (value: string) => void;
  attachments: AttachmentPayload[];
  onAttachments: (value: AttachmentPayload[]) => void;
  variables?: string[];
}) {
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [showHtml, setShowHtml] = useState(false);
  const [preview, setPreview] = useState(false);
  const [imageUrl, setImageUrl] = useState("");
  const [linkUrl, setLinkUrl] = useState("");
  const [showImageBox, setShowImageBox] = useState(false);
  const [showLinkBox, setShowLinkBox] = useState(false);

  /** Wrap the current selection (or insert at the cursor) in the textarea. */
  const surround = (before: string, after: string, placeholder: string) => {
    const el = textRef.current;
    if (!el) {
      onBody(body + before + placeholder + after);
      return;
    }
    const start = el.selectionStart ?? body.length;
    const end = el.selectionEnd ?? start;
    const selected = body.slice(start, end) || placeholder;
    const next = body.slice(0, start) + before + selected + after + body.slice(end);
    onBody(next);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + before.length, start + before.length + selected.length);
    });
  };

  const insertLine = (text: string) => {
    const el = textRef.current;
    const start = el?.selectionStart ?? body.length;
    const next = `${body.slice(0, start)}${text}${body.slice(start)}`;
    onBody(next);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + text.length, start + text.length);
    });
  };

  /** Mirror the plain text into HTML, keeping the existing markup. */
  const syncHtmlFromText = () => {
    const paragraphs = body
      .split(/\n{2,}/)
      .map((block) => `<p>${block.replace(/\n/g, "<br>")}</p>`)
      .join("\n");
    onHtml(paragraphs);
    toast.success("HTML regenerated from the plain-text body");
  };

  const addImage = () => {
    const url = imageUrl.trim();
    if (!/^https?:\/\//i.test(url)) {
      toast.error("Paste an https:// image link");
      return;
    }
    const el = textRef.current;
    const start = el?.selectionStart ?? body.length;
    const placeholder = `[image: ${url}]`;
    onHtml(
      `${html}\n<p><img src="${url}" alt="" style="max-width:100%;height:auto" /></p>`
    );
    onBody(`${body.slice(0, start)}${placeholder}\n${body.slice(start)}`);
    setImageUrl("");
    setShowImageBox(false);
    toast.success("Image added to the HTML body");
  };

  /** Insert a block of ready-made HTML (heading, button, divider, table). */
  const insertHtmlBlock = (html_block: string, text_hint: string) => {
    const el = textRef.current;
    const start = el?.selectionStart ?? body.length;
    onHtml(`${html}${html ? "\n" : ""}${html_block}`);
    if (text_hint) {
      onBody(`${body.slice(0, start)}${text_hint}\n${body.slice(start)}`);
    }
  };

  const addHeading = (level: 1 | 2) => {
    const el = textRef.current;
    const start = el?.selectionStart ?? body.length;
    const end = el?.selectionEnd ?? start;
    const text = body.slice(start, end) || "Your headline";
    const next = `${body.slice(0, start)}${text}\n${body.slice(end)}`;
    onBody(next);
    onHtml(`${html}\n<h${level} style="margin:0 0 12px">${text}</h${level}>`);
  };

  const addButton = () => {
    const url = window.prompt("Where should the button link to?", "https://");
    if (!url) return;
    const label = window.prompt("Button label", "Book a call") || "Click here";
    // Inline styles only: mail clients strip <style> blocks and classes.
    insertHtmlBlock(
      `<p style="margin:18px 0"><a href="${url}" style="background:#2563eb;color:#ffffff;` +
        `padding:12px 22px;border-radius:6px;text-decoration:none;display:inline-block;` +
        `font-weight:600">${label}</a></p>`,
      `${label}: ${url}`
    );
  };

  const addDivider = () => insertHtmlBlock('<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0">', "----------");

  const addTable = () => {
    const rows = [
      ["Item", "Quantity", "Price"],
      ["", "", ""],
      ["", "", ""],
    ];
    const cells = rows
      .map(
        (row, index) =>
          `<tr>${row
            .map((cell) =>
              index === 0
                ? `<th style="border:1px solid #e5e7eb;padding:8px;text-align:left">${cell}</th>`
                : `<td style="border:1px solid #e5e7eb;padding:8px">${cell}</td>`
            )
            .join("")}</tr>`
      )
      .join("");
    insertHtmlBlock(
      `<table style="border-collapse:collapse;width:100%;margin:16px 0" role="presentation">${cells}</table>`,
      "Item / Quantity / Price table added to the HTML body"
    );
  };

  const addLink = () => {
    const url = linkUrl.trim() || "https://";
    const el = textRef.current;
    const start = el?.selectionStart ?? body.length;
    const end = el?.selectionEnd ?? start;
    const label = body.slice(start, end) || url;
    const next = `${body.slice(0, start)}${label} (${url})${body.slice(end)}`;
    onBody(next);
    onHtml(`${html}\n<p><a href="${url}">${label}</a></p>`);
    setLinkUrl("");
    setShowLinkBox(false);
  };

  const readFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const accepted: AttachmentPayload[] = [];
    for (const file of Array.from(files)) {
      if (file.size > MAX_FILE_BYTES) {
        toast.error(`${file.name} is larger than 4MB and was skipped`);
        continue;
      }
      const buffer = await file.arrayBuffer();
      // btoa on a binary string is the portable way to get base64 in a browser.
      let binary = "";
      const bytes = new Uint8Array(buffer);
      for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
      accepted.push({
        name: file.name,
        content_type: file.type || "application/octet-stream",
        size: file.size,
        content_base64: btoa(binary),
      });
    }
    if (accepted.length) {
      onAttachments([...attachments, ...accepted].slice(0, 10));
      toast.success(`${accepted.length} file(s) attached`);
    }
  };

  const tools = [
    { icon: Bold, title: "Bold", run: () => surround("**", "**", "bold text") },
    { icon: Italic, title: "Italic", run: () => surround("_", "_", "italic text") },
    { icon: Underline, title: "Underline", run: () => surround("<u>", "</u>", "underlined") },
    { icon: List, title: "Bullet list", run: () => insertLine("\n• item one\n• item two\n") },
    { icon: ListOrdered, title: "Numbered list", run: () => insertLine("\n1. first\n2. second\n") },
    { icon: Quote, title: "Quote", run: () => surround("\n> ", "\n", "quoted text") },
    { icon: Heading1, title: "Heading", run: () => addHeading(1) },
    { icon: Heading2, title: "Sub-heading", run: () => addHeading(2) },
    { icon: Link2, title: "Insert link", run: () => setShowLinkBox((v) => !v) },
    { icon: ImageIcon, title: "Insert image (by link)", run: () => setShowImageBox((v) => !v) },
    { icon: MousePointerClick, title: "Button / call to action", run: addButton },
    { icon: Table2, title: "Table (e.g. a price list)", run: addTable },
    { icon: Minus, title: "Divider", run: addDivider },
  ];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1 rounded-lg border border-gray-200 dark:border-gray-700 p-1">
        {tools.map(({ icon: Icon, title, run }) => (
          <button
            key={title}
            type="button"
            title={title}
            onClick={run}
            className="p-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <Icon size={15} />
          </button>
        ))}
        <span className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1" />
        <label className="p-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer" title="Attach files">
          <Paperclip size={15} />
          <input
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              readFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        <button
          type="button"
          title="Edit the raw HTML"
          onClick={() => setShowHtml((v) => !v)}
          className={`p-1.5 rounded ${showHtml ? "text-primary-600 bg-primary-50 dark:bg-primary-900/20" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"}`}
        >
          <Code2 size={15} />
        </button>
        <button
          type="button"
          title="Preview what the recipient sees"
          onClick={() => setPreview((v) => !v)}
          className={`p-1.5 rounded ${preview ? "text-primary-600 bg-primary-50 dark:bg-primary-900/20" : "text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"}`}
        >
          <Eye size={15} />
        </button>
        <button type="button" className="ml-auto text-xs text-gray-400 hover:text-primary-600 px-2" onClick={syncHtmlFromText}>
          Text → HTML
        </button>
      </div>

      {showLinkBox && (
        <div className="flex gap-2">
          <input
            className="input text-sm"
            placeholder="https://your-site.com/page"
            value={linkUrl}
            onChange={(e) => setLinkUrl(e.target.value)}
          />
          <button type="button" className="btn-secondary text-sm" onClick={addLink}>
            Add link
          </button>
        </div>
      )}

      {showImageBox && (
        <div className="flex gap-2">
          <input
            className="input text-sm"
            placeholder="https://cdn.your-site.com/photo.png"
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
          />
          <button type="button" className="btn-secondary text-sm" onClick={addImage}>
            Add image
          </button>
        </div>
      )}

      {showHtml ? (
        <textarea
          className="input min-h-[160px] font-mono text-xs"
          value={html}
          onChange={(e) => onHtml(e.target.value)}
          placeholder={'<p>Hi {{first_name}},</p>'}
        />
      ) : (
        <textarea
          ref={textRef}
          className="input min-h-[160px]"
          value={body}
          onChange={(e) => onBody(e.target.value)}
          placeholder={"Hi {{first_name}},\n\n…"}
        />
      )}

      <div className="flex flex-wrap items-center gap-1">
        <span className="text-xs text-gray-400 mr-1">Insert:</span>
        {variables.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => insertLine(v)}
            className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded font-mono text-gray-600 dark:text-gray-300"
          >
            {v}
          </button>
        ))}
      </div>

      {(attachments.length > 0 || html.trim()) && (
        <div className="space-y-1">
          {attachments.map((file, index) => (
            <div key={`${file.name}-${index}`} className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <Paperclip size={12} />
              <span className="truncate">{file.name}</span>
              <span className="text-gray-400">
                {file.size ? `${Math.max(1, Math.round(file.size / 1024))} KB` : ""}
              </span>
              <button
                type="button"
                className="text-gray-400 hover:text-red-500"
                onClick={() => onAttachments(attachments.filter((_, i) => i !== index))}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {preview && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-gray-50 dark:bg-gray-900/40">
          <p className="text-xs text-gray-500 mb-2">Preview</p>
          {html.trim() ? (
            <div
              className="prose prose-sm max-w-none dark:prose-invert"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            <p className="whitespace-pre-wrap text-sm">{body}</p>
          )}
          {attachments.length > 0 && (
            <p className="text-xs text-gray-500 mt-3">
              {attachments.length} attachment{attachments.length === 1 ? "" : "s"}:{" "}
              {attachments.map((a) => a.name).join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
