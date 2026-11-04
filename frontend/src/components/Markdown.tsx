import { memo, useMemo, useState } from "react";
import toast from "react-hot-toast";
import type { Block, InlineToken } from "../utils/markdown";
import { parseMarkdown } from "../utils/markdown";

/**
 * Renders the agent's markdown.
 *
 * Memoised on the text: the chat re-renders on every streamed character, and a
 * long reply must not be re-parsed 40 times a second. `streaming` adds the
 * balancing of half-written markers so bold text does not flicker.
 */

const copy = async (value: string) => {
  try {
    await navigator.clipboard.writeText(value);
    toast.success("Copied");
  } catch {
    toast.error("Could not copy");
  }
};

function Inline({ tokens }: { tokens: InlineToken[] }) {
  return (
    <>
      {tokens.map((token, index) => {
        switch (token.type) {
          case "bold":
            return (
              <strong key={index} className="font-semibold text-gray-900 dark:text-gray-50">
                {token.text}
              </strong>
            );
          case "italic":
            return (
              <em key={index} className="italic">
                {token.text}
              </em>
            );
          case "code":
            return (
              <code
                key={index}
                className="rounded-md bg-gray-100 px-1.5 py-0.5 font-mono text-[0.85em] text-primary-900 dark:bg-gray-800 dark:text-primary-200"
              >
                {token.text}
              </code>
            );
          case "link":
            return (
              <a
                key={index}
                href={token.href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary-700 underline decoration-primary-300 underline-offset-2 hover:decoration-primary-600 dark:text-primary-300"
              >
                {token.text}
              </a>
            );
          case "phone":
            return (
              <button
                key={index}
                type="button"
                onClick={() => copy(token.text)}
                title="Copy this number"
                className="rounded-md bg-primary-50 px-1.5 py-0.5 font-mono text-[0.9em] font-medium text-primary-900 hover:bg-primary-100 dark:bg-primary-950/60 dark:text-primary-200 dark:hover:bg-primary-900"
              >
                {token.text}
              </button>
            );
          default:
            return <span key={index}>{token.text}</span>;
        }
      })}
    </>
  );
}

function CodeBlock({ language, text }: { language: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="group relative my-2 overflow-hidden rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center justify-between border-b border-gray-200 px-3 py-1.5 dark:border-gray-700">
        <span className="text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
          {language || "text"}
        </span>
        <button
          type="button"
          className="text-[11px] font-medium text-primary-700 hover:underline dark:text-primary-300"
          onClick={async () => {
            await copy(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-[12.5px] leading-relaxed">
        <code className="font-mono">{text}</code>
      </pre>
    </div>
  );
}

const BlockList = memo(function BlockList({
  blocks,
}: {
  blocks: Block[];
}) {
  return (
    <>
      {blocks.map((block, index) => {
        switch (block.type) {
          case "heading": {
            const size =
              block.level <= 2 ? "text-lg" : block.level === 3 ? "text-base" : "text-sm";
            return (
              <p key={index} className={`mt-3 mb-1 font-semibold ${size}`}>
                <Inline tokens={block.tokens} />
              </p>
            );
          }
          case "list":
            return block.ordered ? (
              <ol key={index} className="my-1.5 ml-5 list-decimal space-y-1">
                {block.items.map((item, i) => (
                  <li key={i} className="pl-0.5">
                    <Inline tokens={item} />
                  </li>
                ))}
              </ol>
            ) : (
              <ul key={index} className="my-1.5 ml-5 list-disc space-y-1 marker:text-gray-400">
                {block.items.map((item, i) => (
                  <li key={i} className="pl-0.5">
                    <Inline tokens={item} />
                  </li>
                ))}
              </ul>
            );
          case "code":
            return <CodeBlock key={index} language={block.language} text={block.text} />;
          case "quote":
            return (
              <blockquote
                key={index}
                className="my-2 border-l-2 border-primary-300 pl-3 text-gray-600 dark:border-primary-700 dark:text-gray-300"
              >
                <Inline tokens={block.tokens} />
              </blockquote>
            );
          case "table":
            return (
              <div
                key={index}
                className="my-2 overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700"
              >
                <table className="w-full border-collapse text-left text-[13px]">
                  <thead className="bg-gray-50 dark:bg-gray-800">
                    <tr>
                      {block.header.map((cell, i) => (
                        <th
                          key={i}
                          className="whitespace-nowrap px-3 py-2 font-semibold text-gray-700 dark:text-gray-200"
                        >
                          {cell}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r} className="border-t border-gray-100 dark:border-gray-800">
                        {row.map((cell, c) => (
                          <td key={c} className="px-3 py-1.5 align-top">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "rule":
            return <hr key={index} className="my-3 border-gray-200 dark:border-gray-700" />;
          default:
            return (
              <p key={index} className="my-1.5 leading-relaxed first:mt-0 last:mb-0">
                <Inline tokens={block.tokens} />
              </p>
            );
        }
      })}
    </>
  );
});

export default function Markdown({
  text,
  streaming = false,
  className = "",
}: {
  text: string;
  streaming?: boolean;
  className?: string;
}) {
  const blocks = useMemo(() => parseMarkdown(text || "", streaming), [text, streaming]);
  if (!text) return null;
  return (
    <div className={`break-anywhere text-[14.5px] leading-relaxed ${className}`}>
      <BlockList blocks={blocks} />
      {streaming && (
        <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-primary-600 align-middle dark:bg-primary-300" />
      )}
    </div>
  );
}
