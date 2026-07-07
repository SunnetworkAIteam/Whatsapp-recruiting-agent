// server.js
//
// The actual webhook entry point (HTTP layer only). All the real decision
// logic — rule engine -> LLM -> send, or escalate and stop — lives in
// pipeline.js so it can be unit tested without a server. This file just
// wires WhatsApp's HTTP webhook to that logic.

require("dotenv").config(); // load .env into process.env before anything else runs

const express = require("express");
const { processIncomingReply } = require("./pipeline");
const conversationStore = require("./conversationStore");

const app = express();
app.use(express.json());

// WhatsApp Cloud API webhook verification handshake (GET).
app.get("/webhook/whatsapp", (req, res) => {
  const verifyToken = process.env.WHATSAPP_VERIFY_TOKEN;
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === verifyToken) {
    return res.status(200).send(challenge);
  }
  return res.sendStatus(403);
});

// Inbound message webhook.
app.post("/webhook/whatsapp", async (req, res) => {
  try {
    // Shape depends on your provider; this matches WhatsApp Cloud API's
    // standard payload. Adjust the extraction if you're on a different
    // provider (Twilio, etc.).
    const entry = req.body?.entry?.[0];
    const change = entry?.changes?.[0];
    const message = change?.value?.messages?.[0];

    if (!message) {
      // Delivery receipts / status updates land here too — nothing to do.
      return res.sendStatus(200);
    }

    const candidateId = message.from;
    const text = message.text?.body || "";

    const result = await processIncomingReply(candidateId, text);
    res.status(200).json(result);
  } catch (err) {
    console.error("Webhook processing failed:", err);
    // Still 200 the webhook so the provider doesn't retry-storm us; log and
    // alert separately for actual failures.
    res.sendStatus(200);
  }
});

// Small internal endpoint the HR dashboard can poll/hit for the escalation
// queue — swap for a real DB-backed query when conversationStore is backed
// by a database instead of the in-memory Map.
app.get("/internal/escalations", (req, res) => {
  res.json(conversationStore.listEscalated());
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
  app.listen(PORT, () => console.log(`Webhook listening on port ${PORT}`));
}

module.exports = { app };
