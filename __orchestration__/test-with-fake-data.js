// test-with-fake-data.js
//
// Run: node test-with-fake-data.js
//
// Sends a handful of fake candidate messages straight through the real
// pipeline (pipeline.js -> ruleEngine.js -> llmLayer.js -> sendFunctions.js)
// without needing a running server or real API credentials. llmLayer.js and
// sendFunctions.js automatically fall back to mock mode when
// ANTHROPIC_API_KEY / WHATSAPP_ACCESS_TOKEN aren't set, so this just works
// out of the box after `npm install`.

const { processIncomingReply } = require("./pipeline");
const conversationStore = require("./conversationStore");

const fakeMessages = [
  { candidateId: "fake_candidate_1", text: "Hi, when is my interview?" },
  { candidateId: "fake_candidate_1", text: "Great, thanks!" },
  { candidateId: "fake_candidate_2", text: "The salary band you quoted is wrong." },
  { candidateId: "fake_candidate_2", text: "Following up on this." },
  { candidateId: "fake_candidate_3", text: "I'd like to speak to a human recruiter." },
];

async function run() {
  for (const { candidateId, text } of fakeMessages) {
    console.log(`\n>>> ${candidateId} says: "${text}"`);
    const result = await processIncomingReply(candidateId, text);
    console.log("    result:", JSON.stringify(result));
  }

  console.log("\n--- Escalation queue ---");
  console.log(JSON.stringify(conversationStore.listEscalated(), null, 2));
}

run();
