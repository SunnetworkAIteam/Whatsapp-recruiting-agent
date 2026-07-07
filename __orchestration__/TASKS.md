# Escalation Pipeline — Tasks

## 1. Wire the webhook to the rule engine
- [x] Webhook (`server.js`) receives inbound WhatsApp replies
- [x] Every inbound message is appended to conversation history immediately,
      before any decision is made, so HR always sees the full thread
- [x] Message is passed to the rule engine (`ruleEngine.js`) for evaluation

## 2. Connect rule engine → LLM layer → send
- [x] If the rule engine clears the message, it's passed to the LLM layer
      (`llmLayer.js`) to generate a reply
- [x] The generated reply is sent back to the candidate via the send
      functions (`sendFunctions.js: sendWhatsAppMessage`)
- [x] The outbound reply is appended to conversation history

## 3. Escalated conversations are marked correctly and never auto-sent
- [x] If the rule engine flags escalation, the conversation is marked
      `escalated` in `conversationStore.js` (single source of truth for status)
- [x] HR is notified (`sendFunctions.js: notifyHrOfEscalation`)
- [x] Pipeline returns immediately on escalation — the LLM call and the send
      call are unreachable code on that branch, not just skipped by a flag
- [x] Already-escalated conversations are re-flagged by the rule engine on
      every subsequent message, so they can never drift back onto the
      automatic path
- [x] Verified with `pipeline.test.js`: normal message → auto-sent;
      trigger-phrase message → escalated, not sent, HR notified;
      follow-up message on an escalated conversation → stays escalated, still
      not sent

## Notes / next steps for engineering
- Replace `conversationStore.js`'s in-memory `Map` with your real database.
- Replace the `fetch` calls in `llmLayer.js` and `sendFunctions.js` with your
  actual Anthropic API key and WhatsApp Business/Cloud API credentials.
- Extend `ESCALATION_TRIGGERS` in `ruleEngine.js` as real escalation cases
  come in.
- `GET /internal/escalations` in `server.js` is what the HR escalation-queue
  dashboard (the earlier "Escalated Conversation View") should read from.
