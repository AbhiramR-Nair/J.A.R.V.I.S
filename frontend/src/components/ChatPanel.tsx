// Chat message list — grows across days:
//   Day 9:  user transcript bubbles (this file)
//   Day 11: assistant response bubbles added
//   Day 23: PDF-summary cards added

export type ChatMessage = { role: "user" | "assistant"; text: string };

export function ChatPanel({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) return null;

  return (
    // Scrollable message list, anchored to the bottom as new messages arrive.
    <ul className="flex flex-col gap-2 w-full max-w-sm max-h-48 overflow-y-auto px-1">
      {messages.map((msg, i) => (
        <li
          key={i}
          className={
            msg.role === "user"
              ? // User bubbles: right-aligned, cyan tint
                "self-end bg-cyan-600/40 text-cyan-100 font-mono text-xs px-3 py-2 rounded-2xl rounded-br-sm max-w-xs break-words"
              : // Assistant bubbles (Day 11): left-aligned, neutral tint
                "self-start bg-white/10 text-white/80 font-mono text-xs px-3 py-2 rounded-2xl rounded-bl-sm max-w-xs break-words"
          }
        >
          {msg.text}
        </li>
      ))}
    </ul>
  );
}
