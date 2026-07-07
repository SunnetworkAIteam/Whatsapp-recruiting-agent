// llmLayer.js
//
// Only called for messages the rule engine has already cleared. This module
// has one job: turn conversation history + the new candidate message into a
// reply string. It never touches escalation state and never sends anything
// itself — that's the webhook orchestrator's job (see server.js/pipeline.js).
//
// MOCK MODE: if ANTHROPIC_API_KEY isn't set, this returns a canned reply
// instead of calling the real API. Lets you run `npm start` and test the
// full server + webhook flow with fake data before wiring real credentials.

const MOCK_MODE = !process.env.ANTHROPIC_API_KEY;

function mockReply(message) {
  console.log(`[MOCK LLM] No ANTHROPIC_API_KEY set — returning a canned reply for: "${message.text}"`);
  return `(mock reply) Thanks for your message — a member of our team will follow up on: "${message.text}"`;
}

// NOTE: swap in your real Anthropic API call here. Left as a thin wrapper so
// this file is the only place you need to change if you switch providers,
// models, or prompt structure.
async function callModel({ systemPrompt, history }) {
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": process.env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 400,
      system: systemPrompt,
      messages: history,
    }),
  });

  if (!response.ok) {
    throw new Error(`LLM call failed: ${response.status} ${await response.text()}`);
  }

  const data = await response.json();
  const textBlock = data.content?.find((block) => block.type === "text");
  return textBlock?.text?.trim() || "";
}

function buildSystemPrompt(conversation) {
  return [
    "You are a recruiting assistant messaging a job candidate over WhatsApp.",
    "Keep replies short, friendly, and specific to their question.",
    "Never state compensation figures, legal opinions, or make commitments",
    "on the company's behalf beyond what has already been said in this thread.",
    "If you are ever unsure how to answer, say a member of the team will follow up",
    "rather than guessing.",
  ].join(" ");
}

function toModelHistory(conversation, incomingMessage) {
  const priorTurns = conversation.messages.map((m) => ({
    role: m.sender === "candidate" ? "user" : "assistant",
    content: m.text,
  }));
  return [...priorTurns, { role: "user", content: incomingMessage.text }];
}

/**
 * Generate the assistant's reply text for a cleared (non-escalated) message.
 *
 * @param {object} conversation - conversation record from conversationStore
 * @param {object} message - the inbound message, e.g. { text }
 * @returns {Promise<string>} reply text to send back to the candidate
 */
async function generateReply(conversation, message) {
  if (MOCK_MODE) return mockReply(message);
  const systemPrompt = buildSystemPrompt(conversation);
  const history = toModelHistory(conversation, message);
  return callModel({ systemPrompt, history });
}

module.exports = { generateReply };
