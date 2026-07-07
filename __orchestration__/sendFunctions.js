// sendFunctions.js
//
// All outbound I/O lives here: messages to the candidate over WhatsApp, and
// the internal notification that tells HR a conversation needs them. Keeping
// these in one file makes it easy to guarantee escalated conversations never
// silently skip the "notify HR" step — every escalation goes through
// notifyHrOfEscalation, and nothing reaches sendWhatsAppMessage unless the
// orchestrator explicitly calls it after a rule-engine pass.
//
// MOCK MODE: if WHATSAPP_ACCESS_TOKEN isn't set, this logs the message
// instead of calling the real WhatsApp API. Lets you run `npm start` and
// test the full server + webhook flow with fake data before wiring real
// WhatsApp credentials.

const MOCK_MODE = !process.env.WHATSAPP_ACCESS_TOKEN;

// NOTE: replace with your real WhatsApp Business / Cloud API call.
async function sendWhatsAppMessage(candidateId, text) {
  if (MOCK_MODE) {
    console.log(`[MOCK SEND] Would send to ${candidateId}: "${text}"`);
    return { mock: true, to: candidateId, text };
  }

  const response = await fetch(
    `https://graph.facebook.com/v19.0/${process.env.WHATSAPP_PHONE_NUMBER_ID}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
      },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        to: candidateId,
        type: "text",
        text: { body: text },
      }),
    }
  );

  if (!response.ok) {
    throw new Error(`WhatsApp send failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}

// Notifies HR that a conversation needs manual attention. Swap this for
// whatever your team actually uses — Slack webhook, email, a row insert that
// your escalation-queue dashboard reads from, etc. This does NOT message the
// candidate; the candidate hears nothing until HR replies from the manual
// reply interface.
async function notifyHrOfEscalation(conversation) {
  console.log(
    `[HR NOTIFY] Conversation with ${conversation.candidateId} escalated: ${conversation.escalation.reason}`
  );
  // Example Slack webhook call:
  // await fetch(process.env.SLACK_WEBHOOK_URL, {
  //   method: "POST",
  //   headers: { "Content-Type": "application/json" },
  //   body: JSON.stringify({
  //     text: `🚩 Escalated: ${conversation.candidateId} — ${conversation.escalation.reason}`,
  //   }),
  // });
}

module.exports = { sendWhatsAppMessage, notifyHrOfEscalation };
