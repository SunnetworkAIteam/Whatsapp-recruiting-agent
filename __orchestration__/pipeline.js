// pipeline.js
//
// Pure orchestration logic — no HTTP framework here on purpose, so it can be
// unit tested (see pipeline.test.js) without spinning up a server or
// installing Express. The webhook route in server.js is the only thing that
// calls into this file.
//
// This is the ONLY place that decides the order of operations: receive ->
// rule engine -> (LLM -> send) OR (escalate -> notify HR, no send).
//
// Flow:
//   1. Webhook receives an inbound reply from the candidate.
//   2. Message is appended to the conversation history immediately, so HR's
//      escalated-conversation view always shows the full thread even if the
//      candidate keeps messaging after escalation.
//   3. Rule engine evaluates the message.
//      - If it says escalate: mark the conversation escalated, notify HR,
//        and STOP. No LLM call, no automatic send, ever.
//      - If it clears: pass to the LLM layer for a reply.
//   4. LLM reply is sent back to the candidate and appended to the history.
//
// This ordering is deliberate: the rule engine runs before the LLM layer on
// every message, including messages in conversations that are not yet
// escalated, so a newly-escalating message is caught before any automatic
// reply is generated for it.

const conversationStore = require("./conversationStore");
const ruleEngine = require("./ruleEngine");
const llmLayer = require("./llmLayer");
const sendFunctions = require("./sendFunctions");

async function processIncomingReply(candidateId, text) {
  const incomingMessage = {
    sender: "candidate",
    text,
    timestamp: new Date().toISOString(),
  };

  // Always record the message first — HR needs to see it even if this
  // conversation ends up escalated by this very message.
  const conversation = conversationStore.appendMessage(candidateId, incomingMessage);

  const ruleResult = ruleEngine.evaluateMessage(conversation, incomingMessage);

  if (ruleResult.escalate) {
    // Mark escalated (idempotent if already escalated) and stop here.
    // Nothing below this branch runs: no LLM call, no automatic send.
    const escalated = conversationStore.markEscalated(candidateId, {
      reason: ruleResult.reason,
      triggeredBy: "rule_engine",
    });
    await sendFunctions.notifyHrOfEscalation(escalated);
    return { escalated: true, reason: ruleResult.reason };
  }

  // Cleared by the rule engine — safe to generate and send an automatic reply.
  const replyText = await llmLayer.generateReply(conversation, incomingMessage);

  conversationStore.appendMessage(candidateId, {
    sender: "assistant",
    text: replyText,
    timestamp: new Date().toISOString(),
  });

  await sendFunctions.sendWhatsAppMessage(candidateId, replyText);

  return { escalated: false, reply: replyText };
}

module.exports = { processIncomingReply };
