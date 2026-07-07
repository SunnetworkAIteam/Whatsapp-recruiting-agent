// pipeline.test.js
//
// Run with: node pipeline.test.js
// (Kept dependency-free — a simple assert-based smoke test rather than a
// full Jest setup, so it runs anywhere without extra install steps. Swap in
// Jest/Vitest if that's already in your repo's toolchain.)

const assert = require("assert");

// Stub out the LLM and send layers so this test never makes a real network
// call — it only verifies the ORCHESTRATION logic in index.js.
const Module = require("module");
const originalRequire = Module.prototype.require;

let sendCalled = false;
let notifyCalled = false;

Module.prototype.require = function (id) {
  if (id === "./llmLayer") {
    return { generateReply: async () => "mock reply" };
  }
  if (id === "./sendFunctions") {
    return {
      sendWhatsAppMessage: async () => {
        sendCalled = true;
      },
      notifyHrOfEscalation: async () => {
        notifyCalled = true;
      },
    };
  }
  return originalRequire.apply(this, arguments);
};

const { processIncomingReply } = require("./pipeline.js");
const conversationStore = require("./conversationStore");

async function run() {
  // Case 1: normal message clears the rule engine and gets sent automatically.
  sendCalled = false;
  notifyCalled = false;
  const result1 = await processIncomingReply("candidate_1", "Hi, when is my interview?");
  assert.strictEqual(result1.escalated, false, "normal message should not escalate");
  assert.strictEqual(sendCalled, true, "cleared message should trigger an automatic send");
  assert.strictEqual(notifyCalled, false, "cleared message should not notify HR");
  console.log("PASS: normal message sends automatically");

  // Case 2: escalation-trigger message is escalated, HR notified, NOT sent automatically.
  sendCalled = false;
  notifyCalled = false;
  const result2 = await processIncomingReply("candidate_2", "The salary band you quoted is wrong.");
  assert.strictEqual(result2.escalated, true, "trigger phrase should escalate");
  assert.strictEqual(sendCalled, false, "escalated message must NEVER be sent automatically");
  assert.strictEqual(notifyCalled, true, "escalated message must notify HR");
  assert.strictEqual(
    conversationStore.isEscalated("candidate_2"),
    true,
    "conversation should be marked escalated in the store"
  );
  console.log("PASS: escalation blocks automatic send and notifies HR");

  // Case 3: once escalated, further messages from the same candidate stay
  // escalated and still never trigger an automatic send.
  sendCalled = false;
  notifyCalled = false;
  const result3 = await processIncomingReply("candidate_2", "Following up on this.");
  assert.strictEqual(result3.escalated, true, "already-escalated conversation stays escalated");
  assert.strictEqual(sendCalled, false, "no automatic send once escalated, even on later messages");
  console.log("PASS: escalated conversations stay escalated across further messages");

  console.log("\nAll pipeline tests passed.");
}

run().catch((err) => {
  console.error("TEST FAILED:", err);
  process.exit(1);
});
