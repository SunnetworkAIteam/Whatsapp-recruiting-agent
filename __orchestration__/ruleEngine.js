// ruleEngine.js
//
// Runs BEFORE the LLM layer on every inbound reply. Its only job is to decide:
// can the assistant keep handling this automatically, or does a human need to
// take over? Keep this fast, deterministic, and easy to audit — it is the
// safety gate for the whole pipeline, so prefer explicit keyword/condition
// checks over anything fuzzy or model-based here.

// Phrases that mean "a human needs to look at this," grouped by why.
// Extend these lists as you see real escalation cases come through.
const ESCALATION_TRIGGERS = {
  compensation_dispute: [
    "salary", "compensation", "pay band", "offer amount", "band is lower", "different figure",
  ],
  explicit_human_request: [
    "speak to a human", "talk to a person", "real person", "speak to hr", "human recruiter",
  ],
  legal_or_complaint: [
    "lawyer", "legal action", "discriminat", "harassment", "complaint", "unfair treatment",
  ],
  withdrawal_or_urgent: [
    "withdraw my application", "no longer interested", "cancel my interview", "urgent",
  ],
};

const MAX_AUTOMATED_TURNS = 4; // after this many back-and-forths, hand off rather than loop forever

function normalize(text) {
  return (text || "").toLowerCase();
}

function findTriggeredCategory(text) {
  const normalized = normalize(text);
  for (const [category, phrases] of Object.entries(ESCALATION_TRIGGERS)) {
    if (phrases.some((phrase) => normalized.includes(phrase))) {
      return category;
    }
  }
  return null;
}

/**
 * Evaluate an inbound candidate message against escalation rules.
 *
 * @param {object} conversation - current conversation record (from conversationStore)
 * @param {object} message - the inbound message being evaluated, e.g. { text }
 * @returns {{ escalate: boolean, reason?: string, category?: string }}
 */
function evaluateMessage(conversation, message) {
  // Already escalated conversations never get re-evaluated back onto the
  // automatic path by this function — that decision belongs to HR only.
  if (conversation.status === "escalated") {
    return { escalate: true, reason: "Conversation is already escalated.", category: "already_escalated" };
  }

  const category = findTriggeredCategory(message.text);
  if (category) {
    return {
      escalate: true,
      category,
      reason: `Message matched escalation trigger: ${category.replace(/_/g, " ")}.`,
    };
  }

  const automatedTurnsSoFar = conversation.messages.filter((m) => m.sender === "assistant").length;
  if (automatedTurnsSoFar >= MAX_AUTOMATED_TURNS) {
    return {
      escalate: true,
      category: "turn_limit_exceeded",
      reason: `Assistant has already replied ${automatedTurnsSoFar} times without resolution.`,
    };
  }

  return { escalate: false };
}

module.exports = { evaluateMessage, ESCALATION_TRIGGERS, MAX_AUTOMATED_TURNS };
