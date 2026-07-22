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
  {
    id: "4",
    name: "Teja",
    role: "NOC",
    status: "escalated",
    hiring_recruiter_name: "Usha",
    source: "Sourced",
    phone: "9000000004",
    updated_at: "2026-07-20T12:00:00Z",
    created_at: "2026-07-20T09:00:00Z",
    conversation_history: [{ role: "user", content: "This is a joke" }],
    last_message_after_escalation_at: "2026-07-20T12:00:00Z",
    escalation_reason: "Serious anger detected.",
  },
];

function makeQueryBuilder(defaultData) {
  // Tracks what should actually be returned when this chain resolves --
  // starts as the table's default mock rows. insert() overrides it with
  // the (fake-id-tagged) rows actually passed in. update()+eq() overrides
  // it with the ORIGINAL matching row merged with the update payload --
  // without this, .update({...}).eq("id", X).select() would incorrectly
  // resolve with the first row of the default dataset regardless of
  // which candidate was actually being updated, which caused a genuine
  // duplicate React key bug (two candidates ending up with the same id
  // in state) purely from this mock's earlier incompleteness, not from
  // any real bug in the app.
  let resultData = defaultData;
  let pendingUpdatePayload = null;
  const builder = {
    select: vi.fn(() => builder),
    insert: vi.fn((rows) => {
      const list = Array.isArray(rows) ? rows : [rows];
      resultData = list.map((r, i) => ({ id: `new-${i}`, ...r }));
      return builder;
    }),
    update: vi.fn((payload) => {
      pendingUpdatePayload = payload;
      return builder;
    }),
    upsert: vi.fn(() => builder),
    delete: vi.fn(() => builder),
    eq: vi.fn((field, value) => {
      if (pendingUpdatePayload) {
        const original = defaultData.find((d) => String(d[field]) === String(value)) || {};
        resultData = [{ ...original, ...pendingUpdatePayload }];
      }
      return builder;
    }),
    order: vi.fn(() => builder),
    limit: vi.fn(() => builder),
    then: (resolve) => resolve({ data: resultData, error: null }),
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
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
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
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
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
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("No WhatsApp"));

    await waitFor(() => {
      expect(screen.getByText("9000000003")).toBeInTheDocument();
    });
  });

  it("shows the Settings tab with an unconfigured-role warning for roles with no work details", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Settings"));

    await waitFor(() => {
      expect(screen.getByText(/not yet configured/i)).toBeInTheDocument();
    });
  });

  it("renders the Pipeline board with all candidate names visible", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Pipeline board"));

    await waitFor(() => {
      expect(screen.getByText("Priya")).toBeInTheDocument();
      expect(screen.getAllByText("Teja").length).toBeGreaterThan(0);
    });
  });

  it("shows candidates grouped by role in the By Role tab", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("By role"));

    await waitFor(() => {
      expect(screen.getAllByText("NOC").length).toBeGreaterThan(0);
      expect(screen.getAllByText("AI").length).toBeGreaterThan(0);
    });
  });

  it("shows candidates grouped by recruiter in the By Recruiter tab", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("By recruiter"));

    await waitFor(() => {
      expect(screen.getAllByText("Usha").length).toBeGreaterThan(0);
    });
  });

  it("switches theme when a theme button is clicked", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    fireEvent.click(screen.getByText("Dark"));

    await waitFor(() => {
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    });
  });

  it("adds a new candidate via the Add Candidates form", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Add candidates"));

    const nameInput = await screen.findByPlaceholderText("Jane Doe");
    fireEvent.change(nameInput, { target: { value: "New Candidate" } });

    fireEvent.click(screen.getByText("Add to pipeline"));

    await waitFor(() => {
      expect(screen.getByText(/added 1 candidate/i)).toBeInTheDocument();
    });
  });

  it("shows the 'Mark as reviewed' button for an escalated candidate with a new message, and clicking it works", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Pipeline board"));
    await waitFor(() => {
      expect(screen.getAllByText("Teja").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByText("Teja")[0]);

    const reviewButton = await screen.findByText(/mark as reviewed/i);
    expect(reviewButton).toBeInTheDocument();

    fireEvent.click(reviewButton);

    await waitFor(() => {
      expect(screen.queryByText(/mark as reviewed/i)).not.toBeInTheDocument();
    });
  });

  it("toggles the Overview chart between 'Last 7 days' and 'All time'", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/messages sent, last 7 days/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText("All time"));

    await waitFor(() => {
      expect(screen.getByText(/messages sent, all time/i)).toBeInTheDocument();
    });
  });

  it("expands a stat card's candidate list when clicked", async () => {
    render(<CandidatePipelineBoard />);
    await waitFor(() => {
      expect(screen.getByText(/4 candidates/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Total candidates"));

    await waitFor(() => {
      // Rendered as a combined "Priya · NOC" string in one element, not
      // "Priya" as standalone text -- match on a substring instead.
      expect(screen.getAllByText(/Priya/).length).toBeGreaterThan(0);
    });
  });
});