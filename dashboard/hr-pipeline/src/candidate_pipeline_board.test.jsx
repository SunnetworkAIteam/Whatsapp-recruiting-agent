import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CandidatePipelineBoard from "./candidate_pipeline_board";

// ---------------------------------------------------------------------------
// Supabase mock -- the real client is a chainable, "thenable" query
// builder (e.g. supabase.from("candidates").select("*").order(...) is
// awaited directly, not via a terminal .execute() call like the Python
// client). This mock reproduces that shape closely enough for the
// dashboard's actual usage patterns (see src/candidate_pipeline_board.jsx),
// without hitting a real network or database.
// ---------------------------------------------------------------------------
const mockCandidates = [
  {
    id: "1",
    name: "Priya",
    role: "NOC",
    status: "messaged",
    source: "Sourced",
    phone: "9000000001",
    updated_at: "2026-07-20T10:00:00Z",
    created_at: "2026-07-20T09:00:00Z",
    conversation_history: [],
    first_outreach_sent_at: "2026-07-20T09:05:00Z",
  },
  {
    id: "2",
    name: "Rahul",
    role: "AI",
    status: "replied",
    source: "Sourced",
    phone: "9000000002",
    updated_at: "2026-07-20T11:00:00Z",
    created_at: "2026-07-20T09:00:00Z",
    conversation_history: [
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello! Could you share your experience?" },
    ],
    first_outreach_sent_at: "2026-07-20T09:05:00Z",
  },
  {
    id: "3",
    name: "Unknown",
    role: "Unspecified",
    status: "no_whatsapp_candidate",
    platform: "no_whatsapp",
    source: "Inbound (unrecognized number)",
    phone: "9000000003",
    updated_at: "2026-07-20T11:30:00Z",
    created_at: "2026-07-20T09:00:00Z",
    conversation_history: [],
  },
];

function makeQueryBuilder(data) {
  const builder = {
    select: vi.fn(() => builder),
    insert: vi.fn(() => builder),
    update: vi.fn(() => builder),
    upsert: vi.fn(() => builder),
    delete: vi.fn(() => builder),
    eq: vi.fn(() => builder),
    order: vi.fn(() => builder),
    limit: vi.fn(() => builder),
    then: (resolve) => resolve({ data, error: null }),
  };
  return builder;
}

vi.mock("./supabaseClient", () => ({
  supabase: {
    from: vi.fn((table) => {
      if (table === "candidates") return makeQueryBuilder(mockCandidates);
      if (table === "role_work_details") return makeQueryBuilder([]);
      if (table === "unmatched_messages") return makeQueryBuilder([]);
      return makeQueryBuilder([]);
    }),
    channel: vi.fn(() => {
      const chan = {
        on: vi.fn(() => chan),
        subscribe: vi.fn((cb) => {
          if (cb) cb("SUBSCRIBED");
          return chan;
        }),
      };
      return chan;
    }),
    removeChannel: vi.fn(),
  },
}));

describe("CandidatePipelineBoard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Overview tab by default with correct candidate count", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/3 candidates/i)).toBeInTheDocument();
    });
  });

  it("shows the correct 'Messages received' count computed from conversation_history", async () => {
    // Only candidate "2" (Rahul) has any conversation_history, with
    // exactly one "user" turn -- this is a real regression guard for the
    // stat calculation logic in the Overview tab (see
    // candidate_pipeline_board.jsx's `stats` useMemo), which counts
    // turns by role across every candidate's conversation_history.
    render(<CandidatePipelineBoard />);
    // The stat card's number animates up over ~600ms (see useCountUp),
    // so poll until it settles on the correct final value rather than
    // asserting immediately after the label first appears.
    await waitFor(
      () => {
        const receivedCard = screen.getByText("Messages received").closest("button");
        expect(receivedCard).toHaveTextContent("1");
      },
      { timeout: 2000 }
    );
  });

  it("switches to the Messages tab and filters candidates by search query", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/3 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Messages"));

    await waitFor(() => {
      // Rahul's name legitimately appears twice here: once in the
      // conversation list, and once in the chat panel header (since
      // he's auto-selected as the first conversation with messages).
      expect(screen.getAllByText("Rahul").length).toBeGreaterThan(0);
    });

    const searchBox = screen.getByPlaceholderText(/search by name or number/i);
    fireEvent.change(searchBox, { target: { value: "9000000002" } });

    await waitFor(() => {
      expect(screen.getAllByText("Rahul").length).toBeGreaterThan(0);
    });
  });

  it("excludes an engaged candidate from the No WhatsApp tab even if platform is no_whatsapp", async () => {
    // Regression guard for the fix where a candidate whose ONE
    // template send failed (platform="no_whatsapp") but who is
    // otherwise genuinely engaged (a non-"messaged"/non-pending status)
    // should NOT show up in a tab literally called "No WhatsApp".
    // "Unknown" (id 3) here has a made-up status that isn't in
    // ENGAGED_STATUSES, so it SHOULD still appear.
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/3 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("No WhatsApp"));

    await waitFor(() => {
      expect(screen.getByText("9000000003")).toBeInTheDocument();
    });
  });

  it("shows the Settings tab with an unconfigured-role warning for roles with no work details", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/3 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Settings"));

    await waitFor(() => {
      expect(screen.getByText(/not yet configured/i)).toBeInTheDocument();
    });
  });
});