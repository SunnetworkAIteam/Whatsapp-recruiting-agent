# Team Workflow: WhatsApp Recruiting Pipeline

This document explains how we work on the project together. Your job is to write code that fits into our existing setup. You don't need to run or test the whole project on your own laptop.

---

## 1. Branching and PRs

- One branch per task, not per person. Branch off main for each individual task or issue you are assigned.
- Branch naming: yourname/short-task-description. Example: Nanditha/excel-validation, Sanjan/rule-engine, Aditya/webhook-idempotency
- Push small, frequent commits. Don't wait until a whole milestone is done to push.
- Open a PR as soon as a logical chunk is ready.
- Every PR should include pytest tests for the logic you wrote, where testable. See section 3.

---

## 2. Config: never hardcode connection details

Copy .env.example to your own .env file. Always read credentials through os.environ. Never hardcode a real value in a .py file. Never commit your real .env file.

---

## 3. What you CAN test on your own laptop

| Person | What's testable standalone |
|---|---|
| Nanditha | pandas parsing, validation, and dedup logic |
| Sanjan | Rule engine (opt-out, escalation keywords, window check) and LLM prompt output parsing. Mock the real API call in tests. |
| Aditya | Dashboard UI against mock data, and webhook idempotency logic |

Write pytest tests for these and run them yourself before opening a PR.

---

## 4. What you CANNOT test on your own laptop

- Anything needing the database, queue, or a real Twilio send or receive
- The full end-to-end flow

Write the required code along with unit tests for the testable parts, then push your changes to GitHub. Rohith tests the rest against the real Supabase database and Twilio.

---

## 5. Test file location

All test files go in a folder named tests, at the root of the repo.

File naming: test_whatever_you_are_testing.py
Example: tests/test_excel_parsing.py

Function naming inside the file: every test function must start with test_
Example: def test_valid_excel_parses_correctly():

This matters because the CI workflow automatically runs pytest across the whole repo. If your test file or function names don't follow this pattern, pytest will not find them, and your tests will not actually run in CI even if you wrote them.

---

## 6. Function contracts

Agree on these with each other before building, so nobody builds against a guess.

- Nanditha provides: the DB schema, meaning table names, column names, and the status enum values
- Sanjan provides: the rule-engine gate return shape, and the LLM JSON output shape (reply, status, confidence, escalate)
- Aditya needs both of the above to build the dashboard queries and the orchestration logic

If a contract changes after you start building, post about it in the team channel right away.

## 7. The database

The project uses Supabase, which is hosted Postgres. You have been given access directly in Supabase. You do not need Docker to connect to the database. Use the connection string from your own .env file.

You can also open the Supabase dashboard directly to look at real tables and data, using the Table Editor.
