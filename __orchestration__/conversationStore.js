// conversationStore.js
//
// Single source of truth for conversation state. Swap the in-memory Map for
// your real database (Postgres/Supabase/Mongo/etc.) — every other module in
// this pipeline only talks to the functions exported here, so the storage
// backend can change without touching the webhook, rule engine, or LLM layer.

const conversations = new Map();

const STATUS = {
  ACTIVE: "active",       // normal flow, assistant may reply automatically
  ESCALATED: "escalated", // flagged for HR, automatic replies are BLOCKED
  RESOLVED: "resolved",   // HR closed it out
};

function getConversation(candidateId) {
  if (!conversations.has(candidateId)) {
    conversations.set(candidateId, {
      candidateId,
      status: STATUS.ACTIVE,
      escalation: null, // { reason, triggeredBy, triggeredAt } when escalated
      messages: [],
    });
  }
  return conversations.get(candidateId);
}

function appendMessage(candidateId, message) {
  // message: { sender: 'candidate' | 'assistant' | 'hr', text, timestamp }
  const convo = getConversation(candidateId);
  convo.messages.push(message);
  return convo;
}

function isEscalated(candidateId) {
  return getConversation(candidateId).status === STATUS.ESCALATED;
}

// Marks a conversation escalated. This is the ONLY place status flips to
// ESCALATED, so it's the one place to audit if a conversation was ever
// incorrectly left on the automatic path.
function markEscalated(candidateId, { reason, triggeredBy = "rule_engine" } = {}) {
  const convo = getConversation(candidateId);
  convo.status = STATUS.ESCALATED;
  convo.escalation = {
    reason: reason || "Unspecified escalation trigger",
    triggeredBy,
    triggeredAt: new Date().toISOString(),
  };
  return convo;
}

function markResolved(candidateId) {
  const convo = getConversation(candidateId);
  convo.status = STATUS.RESOLVED;
  return convo;
}

function listEscalated() {
  return Array.from(conversations.values()).filter((c) => c.status === STATUS.ESCALATED);
}

module.exports = {
  STATUS,
  getConversation,
  appendMessage,
  isEscalated,
  markEscalated,
  markResolved,
  listEscalated,
};
