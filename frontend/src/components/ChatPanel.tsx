import { MessageSquare } from "lucide-react";

import { HudFrame } from "./HudFrame";

export type ChatMessage = { role: "user" | "assistant"; text: string };
export type SearchSource = { title: string; url: string };

// Display cap — full history lives in App.tsx state; only the tail is rendered.
const DISPLAY_LIMIT = 5;

export function ChatPanel({
  messages,
  sources = [],
}: {
  messages: ChatMessage[];
  sources?: SearchSource[];
}) {
  if (messages.length === 0) return null;

  const visible = messages.slice(-DISPLAY_LIMIT);

  return (
    <HudFrame className="w-full max-w-sm">
      {/* Header row: icon + label + count */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <MessageSquare size={10} style={{ color: "rgba(124,212,232,0.5)" }} />
          <span
            style={{ letterSpacing: "0.18em", fontSize: 10, color: "rgba(124,212,232,0.7)" }}
            className="font-mono uppercase"
          >
            Chat
          </span>
        </div>
        <span
          style={{ letterSpacing: "0.10em", fontSize: 9, color: "rgba(124,212,232,0.35)" }}
          className="font-mono"
        >
          [{visible.length} RECENT]
        </span>
      </div>

      {/* Message list — prefix characters read as ornamentation, not content */}
      <ul className="flex flex-col gap-1 max-h-36 overflow-y-auto">
        {visible.map((msg, i) => (
          <li key={i} className="flex gap-1.5 font-mono" style={{ fontSize: 10 }}>
            {/* Prefix: dim so it doesn't compete with the message text */}
            <span style={{ color: "rgba(124,212,232,0.3)", flexShrink: 0 }}>
              {msg.role === "user" ? ">" : "<"}
            </span>
            <span
              style={{
                color: msg.role === "user"
                  ? "rgba(124,212,232,0.65)"   // user: slightly muted
                  : "rgba(200,230,240,0.85)",  // assistant: more prominent
                wordBreak: "break-word",
              }}
            >
              {msg.text}
            </span>
          </li>
        ))}
      </ul>

      {/* Sources block — only shown after a web_search or grounded_search tool call.
          URLs are never spoken aloud; they come here via the search_results WS event. */}
      {sources.length > 0 && (
        <div className="mt-2 pt-2 border-t border-cyan-900/40">
          <div
            className="font-mono uppercase mb-1"
            style={{ fontSize: 9, letterSpacing: "0.18em", color: "rgba(124,212,232,0.45)" }}
          >
            Sources
          </div>
          <ul className="flex flex-col gap-0.5">
            {sources.map((s) => (
              <li key={s.url} className="font-mono truncate" style={{ fontSize: 9 }}>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "rgba(124,212,232,0.7)" }}
                  className="hover:underline"
                >
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </HudFrame>
  );
}
