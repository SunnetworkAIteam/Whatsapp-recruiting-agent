# Escalation Pipeline

Wires the WhatsApp webhook to the rule engine, the LLM layer, and the send
functions — with a hard guarantee that escalated conversations are never
sent an automatic reply.

## Flow

```
Candidate message
      │
      ▼
 webhook (server.js)
      │  appends message to conversation history
      ▼
 rule engine (ruleEngine.js)
      │
      ├── escalate? ──► mark conversation ESCALATED (conversationStore.js)
      │                 notify HR (sendFunctions.js: notifyHrOfEscalation)
      │                 STOP — no LLM call, no automatic send
      │
      └── clears ────► LLM layer (llmLayer.js) generates a reply
                        │
                        ▼
                        send functions (sendFunctions.js: sendWhatsAppMessage)
                        reply appended to conversation history
```

## Files

| File | Responsibility |
|---|---|
| `server.js` | HTTP layer only — WhatsApp webhook verification + inbound route. Calls into `pipeline.js`. |
| `pipeline.js` | Pure orchestration: receive → rule engine → (LLM → send) OR (escalate → notify, stop). No HTTP framework, so it's directly unit-testable. |
| `ruleEngine.js` | Decides whether a message can stay on the automatic path or must escalate. Deterministic keyword/condition checks — this is the safety gate. |
| `llmLayer.js` | Generates the reply text. Only ever called for messages the rule engine has cleared. |
| `sendFunctions.js` | All outbound I/O: sending the WhatsApp reply, and notifying HR of an escalation. |
| `conversationStore.js` | Conversation state (`active` / `escalated` / `resolved`) and full message history. In-memory `Map` — swap for your real DB. |
| `pipeline.test.js` | Smoke tests proving escalated conversations never trigger an automatic send. Run with `npm test`. |

## The escalation guarantee

Three things enforce that escalated conversations are never auto-replied to:

1. **`ruleEngine.evaluateMessage`** immediately returns `escalate: true` if
   `conversation.status === "escalated"` — an already-escalated conversation
   can never be re-cleared back onto the automatic path by this function.
2. **`pipeline.js`** calls `return` right after marking a conversation
   escalated. The LLM layer and `sendWhatsAppMessage` are literally
   unreachable code on that branch — not just skipped by a flag.
3. **`conversationStore.markEscalated`** is the only function that flips
   status to `escalated`, so there's exactly one place to audit if a
   conversation was ever handled incorrectly.

Once escalated, the conversation shows up in `GET /internal/escalations`
for the HR escalation-queue view, and any reply to the candidate has to come
from HR's manual reply interface — nothing in this pipeline sends on their
behalf again until HR resolves it.

## Setup

```bash
npm install
```

Environment variables needed:

| Variable | Used by |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | `server.js` webhook verification handshake |
| `WHATSAPP_ACCESS_TOKEN` | `sendFunctions.js` outbound send |
| `WHATSAPP_PHONE_NUMBER_ID` | `sendFunctions.js` outbound send |
| `ANTHROPIC_API_KEY` | `llmLayer.js` reply generation |
| `SLACK_WEBHOOK_URL` *(optional)* | `sendFunctions.js` HR notification |

## Run

```bash
npm start        # starts the webhook server
npm test         # runs pipeline.test.js (no network calls, no server needed)
```

## Assumptions made

No existing webhook/rule-engine/LLM code was shared in this conversation, so
this was built as a standalone Node.js + Express module you can drop into
the `Whatsapp-recruiting-agent` repo and wire up to your actual data store,
WhatsApp credentials, and Anthropic API key. If your repo already has a
different structure for any of these pieces (e.g. an existing rule engine or
message store), point me at those files and I'll adapt this to match instead
of introducing a second implementation.
