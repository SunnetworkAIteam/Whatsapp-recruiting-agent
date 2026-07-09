# Escalated Conversation View — Tasks

## 1. Candidate summary panel
- [x] Show candidate name, role, requisition ID, and current pipeline stage
- [x] Show contact details (email, phone, location)

## 2. Full message history
- [x] Render the entire conversation thread (candidate + assistant messages) in order
- [x] Distinguish candidate messages from assistant/HR messages visually
- [x] Mark the specific message where escalation was triggered

## 3. Escalation reason
- [x] Display why the conversation was escalated, in a clearly separated card
- [x] Show when it was escalated and by what/whom (e.g. Screening Assistant)
- [x] Show urgency/severity so HR can triage

## 4. Manual reply interface
- [x] Text input for HR to compose a reply
- [x] Send action that appends the reply to the thread as "You (HR)"
- [x] Keyboard shortcut (Cmd/Ctrl + Enter) to send
- [x] Disabled state when input is empty or send is in progress

## Notes / next steps for engineering
- Replace the mock `candidate`, `escalation`, and `initialMessages` objects in
  `EscalatedConversationView.jsx` with real data fetched by conversation ID.
- Wire `handleSend` to your messaging API (currently simulates a local send).
- Consider adding: internal-only notes, an "escalate further" action, and
  read receipts once backend support exists.
