import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import Papa from "papaparse";
import {
  LayoutDashboard,
  KanbanSquare,
  Users,
  UserCog,
  PhoneOff,
  UserPlus,
  Send,
  MessageSquareText,
  Repeat,
  AlertTriangle,
  TrendingUp,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { supabase } from "./supabaseClient";

// ---------------------------------------------------------------------------
// STATUS CONFIG
// Each status carries a real hex color (used for dots, glows, borders, and
// the pipeline pulse bar) rather than Tailwind color-name classes, so the
// palette is centralized and easy to retune. Must stay in sync with
// app/status.py's VALID_STATUSES on the backend -- if a status exists there
// but not here, candidates in that status fall back to "Pending" via
// normalizeStatus(), which will look wrong on the board even though the DB
// data is correct.
// ---------------------------------------------------------------------------
const STATUSES = [
  { key: "pending",             label: "Pending",             color: "#8892B0" },
  { key: "messaged",            label: "Messaged",            color: "#4C8DFF" },
  { key: "follow_up_sent",      label: "Follow-up sent",      color: "#9B7BFF" },
  { key: "final_reminder_sent", label: "Final reminder sent", color: "#FFB020" },
  { key: "replied",             label: "Replied",             color: "#3DD9EB" },
  { key: "awaiting_interview",  label: "Awaiting interview",  color: "#22C3A6" },
  { key: "qualified",           label: "Qualified",           color: "#34D399" },
  { key: "escalated",           label: "Escalated",           color: "#FB5D74" },
  { key: "booked",              label: "Booked",              color: "#22C55E" },
  { key: "declined",            label: "Declined",            color: "#6B7290" },
];

const STATUS_KEYS = STATUSES.map((s) => s.key);
const STATUS_MAP = Object.fromEntries(STATUSES.map((s) => [s.key, s]));

// Statuses that mean the candidate engaged at least once (replied to
// something), for the response-rate calculation on the Overview tab.
const ENGAGED_STATUSES = new Set([
  "replied", "awaiting_interview", "qualified", "escalated", "booked", "declined",
]);

const SOURCES_FALLBACK = ["LinkedIn", "Referral", "Careers page", "Indeed", "Sourced"];

// ---------------------------------------------------------------------------
// SCHEDULING LIKELIHOOD -- a simple, transparent rule-based score
// estimating how likely a candidate is to schedule an interview, based
// on their current status, how engaged they've been in conversation,
// and a few positive/negative keyword signals in what they've actually
// said. Not an LLM call -- deliberately cheap, instant, and explainable
// (each contributing factor is returned alongside the score) rather
// than a black-box prediction.
// ---------------------------------------------------------------------------
const POSITIVE_SIGNAL_WORDS = [
  "interested", "yes", "sure", "sounds good", "when", "schedule",
  "available", "notice period", "ctc", "salary", "role", "details",
];
const NEGATIVE_SIGNAL_WORDS = [
  "not interested", "no thanks", "busy", "later", "stop", "unsubscribe",
  "declined", "not looking", "already have", "no ",
];

const STATUS_BASE_SCORE = {
  pending: 0,
  messaged: 10,
  follow_up_sent: 8,
  final_reminder_sent: 5,
  replied: 40,
  awaiting_interview: 90,
  qualified: 70,
  escalated: 45,
  booked: 100,
  declined: 0,
};

function computeSchedulingLikelihood(candidate) {
  const history = candidate.conversation_history || [];
  const userTurns = history.filter((t) => t.role === "user");

  let score = STATUS_BASE_SCORE[candidate.status] ?? 20;
  const factors = [];

  factors.push({ label: `Status: ${candidate.status}`, value: STATUS_BASE_SCORE[candidate.status] ?? 20 });

  // Engagement: more back-and-forth signals real interest, up to a cap.
  const engagementBonus = Math.min(userTurns.length * 4, 20);
  if (engagementBonus > 0) {
    score += engagementBonus;
    factors.push({ label: `${userTurns.length} message(s) from candidate`, value: engagementBonus });
  }

  // Keyword signals in the candidate's own messages only.
  const allUserText = userTurns.map((t) => t.content.toLowerCase()).join(" ");
  let positiveHits = 0;
  let negativeHits = 0;
  POSITIVE_SIGNAL_WORDS.forEach((w) => {
    if (allUserText.includes(w)) positiveHits += 1;
  });
  NEGATIVE_SIGNAL_WORDS.forEach((w) => {
    if (allUserText.includes(w)) negativeHits += 1;
  });

  if (positiveHits > 0) {
    const bonus = Math.min(positiveHits * 5, 15);
    score += bonus;
    factors.push({ label: `${positiveHits} positive signal(s) in messages`, value: bonus });
  }
  if (negativeHits > 0) {
    const penalty = Math.min(negativeHits * 15, 40);
    score -= penalty;
    factors.push({ label: `${negativeHits} negative signal(s) in messages`, value: -penalty });
  }

  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  return { score: clamped, factors };
}

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function useCountUp(target, duration = 600) {
  const [value, setValue] = useState(target);
  const prevRef = useRef(target);

  useEffect(() => {
    const start = prevRef.current;
    const end = target;
    if (start === end) return;
    const startTime = performance.now();

    let raf;
    const tick = (now) => {
      const progress = Math.min((now - startTime) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      setValue(Math.round(start + (end - start) * eased));
      if (progress < 1) raf = requestAnimationFrame(tick);
      else prevRef.current = end;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

function initials(name) {
  if (!name) return "?";
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

function timeAgo(iso) {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function normalizeStatus(raw) {
  const s = String(raw || "pending").trim().toLowerCase();
  return STATUS_KEYS.includes(s) ? s : "pending";
}

// Maps a raw Supabase `candidates` row into the shape this component's UI
// expects. Centralizing this here means if the DB schema changes, only
// this function needs updating.
function mapRow(row) {
  // For auto-created candidates from an unrecognized number (see
  // app/status.py's create_candidate_for_unknown_number), the DB stores
  // a placeholder name "Unknown" (name is a required/NOT NULL column) --
  // but the dashboard shows their phone number instead, since that's
  // more useful and identifiable than a generic "Unknown" label.
  const displayName = (!row.name || row.name === "Unknown") ? (row.phone || "Unknown") : row.name;
  return {
    id: row.id,
    name: displayName,
    role: row.role || "—",
    source: row.source || "Sourced",
    status: normalizeStatus(row.status),
    last_activity_at: row.updated_at || row.created_at,
    created_at: row.created_at,
    email: row.email || "",
    phone: row.phone || "",
    location: row.location || "",
    current_company: row.current_company || "",
    college_name: row.college_name || "",
    hiring_recruiter_name: row.hiring_recruiter_name || "",
    remarks: row.remarks || "",
    interview_score: row.interview_score,
    communication: row.communication,
    overall_feedback: row.overall_feedback || "",
    final_status: row.final_status || "",
    platform: row.platform || "whatsapp",
    first_outreach_sent_at: row.first_outreach_sent_at || null,
    followup_1_sent_at: row.followup_1_sent_at || null,
    followup_2_sent_at: row.followup_2_sent_at || null,
    interview_followup_sent_at: row.interview_followup_sent_at || null,
    last_candidate_message_at: row.last_candidate_message_at || null,
    escalation_reason: row.escalation_reason || "",
    delivery_status: row.delivery_status || "",
    delivery_error_code: row.delivery_error_code || "",
    delivery_error_message: row.delivery_error_message || "",
    escalated_at: row.escalated_at || null,
    last_message_after_escalation_at: row.last_message_after_escalation_at || null,
    conversation_history: row.conversation_history || [],
  };
}

// Reverse-maps a UI row (from the manual entry table / CSV import) back
// into columns the `candidates` table actually has, for inserts.
function mapRowForInsert(row) {
  return {
    name: row.name?.trim(),
    email: row.email?.trim() || null,
    phone: row.phone?.trim() || null,
    role: row.role?.trim() || null,
    source: row.source?.trim() || "Sourced",
    status: normalizeStatus(row.status),
    remarks: row.notes?.trim() || null,
  };
}

// ---------------------------------------------------------------------------
// DATA LAYER -- wired to real Supabase data (candidates table), with
// Realtime subscription so the board updates live as the backend pipeline
// (webhook -> rule engine -> LLM -> status update) changes candidate rows.
// ---------------------------------------------------------------------------
function usePipelineData() {
  const [candidates, setCandidates] = useState([]);
  const [lastSyncedAt, setLastSyncedAt] = useState(new Date());
  const [connection, setConnection] = useState("syncing"); // 'connected' | 'syncing' | 'offline'
  const [justMoved, setJustMoved] = useState(null);
  const prevStatusRef = useRef({});

  const fetchAll = useCallback(async () => {
    setConnection("syncing");
    const { data, error } = await supabase
      .from("candidates")
      .select("*")
      .order("updated_at", { ascending: false });

    if (error) {
      // eslint-disable-next-line no-console
      console.error("Failed to fetch candidates:", error);
      setConnection("offline");
      return;
    }

    setCandidates(data.map(mapRow));
    setLastSyncedAt(new Date());
    setConnection("connected");
  }, []);

  const addCandidates = useCallback(async (partials) => {
    const list = Array.isArray(partials) ? partials : [partials];
    const rows = list
      .filter((p) => p && p.name && p.name.trim())
      .map(mapRowForInsert);

    if (rows.length === 0) return 0;

    const { data, error } = await supabase.from("candidates").insert(rows).select();
    if (error) {
      // eslint-disable-next-line no-console
      console.error("Failed to insert candidates:", error);
      return 0;
    }

    setCandidates((prev) => [...prev, ...data.map(mapRow)]);
    setLastSyncedAt(new Date());
    return data.length;
  }, []);

  useEffect(() => {
    fetchAll();

    // Realtime: reflect backend-driven status changes (rule engine, LLM
    // layer, follow-up scheduler) as they happen, without polling.
    const channel = supabase
      .channel("candidates-changes")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "candidates" },
        (payload) => {
          if (payload.eventType === "DELETE") {
            setCandidates((prev) => prev.filter((c) => c.id !== payload.old.id));
            return;
          }
          const mapped = mapRow(payload.new);
          setCandidates((prev) => {
            const prevStatus = prevStatusRef.current[mapped.id];
            if (prevStatus && prevStatus !== mapped.status) {
              setJustMoved(mapped.id);
              window.setTimeout(
                () => setJustMoved((cur) => (cur === mapped.id ? null : cur)),
                1700
              );
            }
            prevStatusRef.current[mapped.id] = mapped.status;
            const next = prev.filter((c) => c.id !== mapped.id);
            return [...next, mapped];
          });
          setLastSyncedAt(new Date());
        }
      )
      .subscribe((status) => {
        setConnection(status === "SUBSCRIBED" ? "connected" : "syncing");
      });

    return () => {
      supabase.removeChannel(channel);
    };
  }, [fetchAll]);

  const markReviewed = useCallback(async (candidateId) => {
    // Also moves status from "escalated" back to "replied" -- the LLM
    // layer permanently refuses to auto-reply to anyone still in
    // "escalated" status, so without this, "reviewing" an escalation had
    // no real effect: the candidate could never receive another
    // automated message even after a human had reviewed the situation.
    const { data, error } = await supabase
      .from("candidates")
      .update({ last_message_after_escalation_at: null, status: "replied" })
      .eq("id", candidateId)
      .select();
    if (error) {
      // eslint-disable-next-line no-console
      console.error("Failed to mark candidate as reviewed:", error);
      return false;
    }
    if (data && data[0]) {
      const mapped = mapRow(data[0]);
      setCandidates((prev) => prev.map((c) => (c.id === candidateId ? mapped : c)));
    }
    return true;
  }, []);

  return { candidates, connection, lastSyncedAt, refresh: fetchAll, justMoved, addCandidates, markReviewed };
}

// ---------------------------------------------------------------------------
// SIGNATURE ELEMENT: Pipeline Pulse -- a live segmented bar showing the
// real-time proportion of candidates in each status, with a sweeping sheen
// to read as a "live monitor" rather than a static chart.
// ---------------------------------------------------------------------------
function PipelinePulse({ candidates }) {
  const total = candidates.length;

  return (
    <div className="relative h-2.5 w-full overflow-hidden rounded-full" style={{ background: "var(--surface)" }}>
      {total === 0 ? (
        <div className="h-full w-full" style={{ background: "var(--line)" }} />
      ) : (
        <div className="flex h-full w-full">
          {STATUSES.map((status) => {
            const count = candidates.filter((c) => c.status === status.key).length;
            if (count === 0) return null;
            const pct = (count / total) * 100;
            return (
              <div
                key={status.key}
                className="h-full transition-all duration-700 ease-out"
                style={{ width: `${pct}%`, background: status.color }}
                title={`${status.label}: ${count}`}
              />
            );
          })}
        </div>
      )}
      <div
        className="pulse-sheen absolute inset-y-0 w-1/4"
        style={{
          background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent)",
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SHARED UI
// ---------------------------------------------------------------------------
function ConnectionBadge({ connection, lastSyncedAt }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const label =
    connection === "syncing" ? "Syncing" : connection === "offline" ? "Offline" : "Live";
  const dotColor =
    connection === "syncing" ? "#FFB020" : connection === "offline" ? "#FB5D74" : "#3DD9EB";

  return (
    <div className="flex items-center gap-2 font-mono text-xs" style={{ color: "var(--text-mid)" }}>
      <span className="relative flex h-2 w-2">
        {connection === "connected" && (
          <span
            className="live-ping absolute inline-flex h-full w-full rounded-full"
            style={{ background: dotColor }}
          />
        )}
        <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: dotColor }} />
      </span>
      <span className="font-medium tracking-wide" style={{ color: "var(--text-hi)" }}>
        {label.toUpperCase()}
      </span>
      <span style={{ color: "var(--text-dim)" }}>· synced {timeAgo(lastSyncedAt.toISOString())}</span>
    </div>
  );
}

function StatusChip({ status }) {
  const s = STATUS_MAP[status] || STATUSES[0];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: hexToRgba(s.color, 0.14), color: s.color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.color }} />
      {s.label}
    </span>
  );
}

function CandidateCard({ candidate, highlighted, onOpen }) {
  const status = STATUS_MAP[candidate.status] || STATUSES[0];
  const isEscalated = candidate.status === "escalated";
  // True if the candidate messaged again after being escalated -- means
  // there's activity a human hasn't seen yet, since the bot intentionally
  // stays silent once escalated (see agent/llm_layer.py's short-circuit).
  const hasNewMessageSinceEscalation =
    isEscalated && candidate.last_message_after_escalation_at;

  return (
    <button
      type="button"
      onClick={() => onOpen(candidate)}
      style={{
        background: "var(--surface)",
        borderColor: "var(--line)",
        borderLeftColor: status.color,
        borderLeftWidth: "3px",
        "--glow-color": hexToRgba(status.color, 0.55),
        boxShadow: "0 1px 2px rgba(0,0,0,0.25)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = `0 12px 28px -10px ${hexToRgba(status.color, 0.35)}, 0 4px 10px -4px rgba(0,0,0,0.5)`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = "0 1px 2px rgba(0,0,0,0.25)";
      }}
      className={`group w-full rounded-lg border p-3 text-left transition-all duration-300 hover:-translate-y-1 ${
        highlighted ? "glow-pop" : ""
      } ${isEscalated ? "escalate-pulse" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold font-display"
            style={{ background: hexToRgba(status.color, 0.18), color: status.color }}
          >
            {initials(candidate.name)}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium" style={{ color: "var(--text-hi)" }}>
              {candidate.name}
            </div>
            <div className="truncate text-xs" style={{ color: "var(--text-mid)" }}>
              {candidate.role}
            </div>
          </div>
        </div>
        {hasNewMessageSinceEscalation && (
          <span
            className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
            style={{ background: "#FB5D74", color: "#ffffff" }}
            title="Candidate messaged again after escalation -- needs human reply"
          >
            NEW
          </span>
        )}
      </div>
      <div className="mt-2.5 flex items-center justify-between font-mono text-[11px]" style={{ color: "var(--text-dim)" }}>
        <span
          className="rounded px-1.5 py-0.5"
          style={{ background: "var(--surface-hover)", color: "var(--text-mid)" }}
        >
          {candidate.source}
        </span>
        <span>{timeAgo(candidate.last_activity_at)}</span>
      </div>
    </button>
  );
}

function Column({ status, candidates, justMoved, onOpenCandidate }) {
  const prevCountRef = useRef(candidates.length);
  const [pop, setPop] = useState(false);

  useEffect(() => {
    if (prevCountRef.current !== candidates.length) {
      setPop(true);
      const t = setTimeout(() => setPop(false), 400);
      prevCountRef.current = candidates.length;
      return () => clearTimeout(t);
    }
  }, [candidates.length]);

  return (
    <div className="flex w-64 shrink-0 flex-col">
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: status.color, boxShadow: `0 0 8px ${hexToRgba(status.color, 0.7)}` }} />
          <span className="font-display text-sm font-medium" style={{ color: "var(--text-hi)" }}>
            {status.label}
          </span>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 font-mono text-xs font-medium ${pop ? "count-pop" : ""}`}
          style={{ background: "var(--surface)", color: "var(--text-mid)" }}
        >
          {candidates.length}
        </span>
      </div>
      <div
        className="flex min-h-[120px] flex-1 flex-col gap-2 rounded-xl p-2"
        style={{ background: "rgba(255,255,255,0.015)", border: "1px solid var(--line)" }}
      >
        {candidates.length === 0 && (
          <div className="flex flex-1 items-center justify-center py-8 font-mono text-xs" style={{ color: "var(--text-dim)" }}>
            — empty —
          </div>
        )}
        {candidates.map((c) => (
          <CandidateCard key={c.id} candidate={c} highlighted={justMoved === c.id} onOpen={onOpenCandidate} />
        ))}
      </div>
    </div>
  );
}

function CandidateDetailModal({ candidate, onClose, onMarkReviewed }) {
  if (!candidate) return null;
  const status = STATUS_MAP[candidate.status] || STATUSES[0];
  const hasNewMessageSinceEscalation =
    candidate.status === "escalated" && candidate.last_message_after_escalation_at;
  const likelihood = computeSchedulingLikelihood(candidate);
  const likelihoodColor = likelihood.score >= 70 ? "#34D399" : likelihood.score >= 40 ? "#FFB020" : "#6B7290";

  const Field = ({ label, value }) => (
    <div className="flex justify-between gap-4">
      <dt className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>{label}</dt>
      <dd className="text-right text-sm" style={{ color: "var(--text-hi)" }}>{value || "—"}</dd>
    </div>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(5, 7, 14, 0.7)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="fade-slide-in max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl p-6"
        style={{ background: "var(--surface)", border: "1px solid var(--line)", boxShadow: "0 24px 64px -20px rgba(0,0,0,0.7)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full font-display text-sm font-semibold"
              style={{ background: hexToRgba(status.color, 0.18), color: status.color }}
            >
              {initials(candidate.name)}
            </div>
            <div>
              <div className="font-display text-base font-semibold" style={{ color: "var(--text-hi)" }}>
                {candidate.name}
              </div>
              <div className="text-sm" style={{ color: "var(--text-mid)" }}>{candidate.role}</div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm transition-colors hover:bg-white/5"
            style={{ color: "var(--text-dim)" }}
          >
            Close
          </button>
        </div>

        <div className="mt-4 flex items-center justify-between gap-3 rounded-lg p-3" style={{ background: hexToRgba(likelihoodColor, 0.1), border: `1px solid ${hexToRgba(likelihoodColor, 0.3)}` }}>
          <div>
            <div className="font-mono text-xs uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>
              Interview scheduling likelihood
            </div>
            <div className="mt-0.5 font-mono text-[11px]" style={{ color: "var(--text-mid)" }}>
              {likelihood.factors.map((f) => f.label).join(" · ")}
            </div>
          </div>
          <div className="font-display text-2xl font-semibold" style={{ color: likelihoodColor }}>
            {likelihood.score}%
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <StatusChip status={candidate.status} />
          <span
            className="rounded-full px-2 py-0.5 font-mono text-xs"
            style={{ background: "var(--surface-hover)", color: "var(--text-mid)" }}
          >
            {candidate.source}
          </span>
          <span className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>
            last activity {timeAgo(candidate.last_activity_at)}
          </span>
        </div>

        {hasNewMessageSinceEscalation && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-lg p-3" style={{ background: hexToRgba("#FB5D74", 0.1), border: "1px solid rgba(251,93,116,0.3)" }}>
            <span className="text-sm font-medium" style={{ color: "#FB5D74" }}>
              New message since escalation -- needs a human reply
            </span>
            <button
              onClick={async () => {
                const ok = await onMarkReviewed(candidate.id);
                if (ok) onClose();
              }}
              className="shrink-0 rounded-md px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-90"
              style={{ background: "#FB5D74", color: "#ffffff" }}
            >
              Mark as reviewed
            </button>
          </div>
        )}

        {candidate.status === "escalated" && candidate.escalation_reason && (
          <div
            className="mt-4 rounded-lg p-3 text-sm"
            style={{ background: hexToRgba("#FB5D74", 0.12), color: "#FB5D74", border: "1px solid rgba(251,93,116,0.3)" }}
          >
            <div className="font-mono text-xs font-semibold uppercase tracking-wide">Escalation reason</div>
            <div className="mt-1">{candidate.escalation_reason}</div>
          </div>
        )}

        <dl className="mt-5 space-y-3">
          <Field label="EMAIL" value={candidate.email} />
          <Field label="PHONE" value={candidate.phone} />
          <Field label="LOCATION" value={candidate.location} />
          <Field label="COMPANY" value={candidate.current_company} />
          <Field label="COLLEGE" value={candidate.college_name} />
          <Field label="RECRUITER" value={candidate.hiring_recruiter_name} />
          {candidate.interview_score != null && (
            <Field label="INTERVIEW SCORE" value={candidate.interview_score} />
          )}
        </dl>

        <div className="mt-5">
          <div className="font-mono text-xs font-medium tracking-wide" style={{ color: "var(--text-dim)" }}>
            MESSAGE HISTORY
          </div>
          <dl className="mt-2 space-y-2">
            <Field label="Initial outreach" value={candidate.first_outreach_sent_at ? timeAgo(candidate.first_outreach_sent_at) : "not sent"} />
            <Field label="Follow-up 1" value={candidate.followup_1_sent_at ? timeAgo(candidate.followup_1_sent_at) : "not sent"} />
            <Field label="Follow-up 2 (final)" value={candidate.followup_2_sent_at ? timeAgo(candidate.followup_2_sent_at) : "not sent"} />
            <Field label="Interview follow-up" value={candidate.interview_followup_sent_at ? timeAgo(candidate.interview_followup_sent_at) : "not sent"} />
          </dl>
        </div>

        {candidate.remarks && (
          <div className="mt-5">
            <div className="font-mono text-xs font-medium tracking-wide" style={{ color: "var(--text-dim)" }}>
              REMARKS
            </div>
            <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--text-mid)" }}>{candidate.remarks}</p>
          </div>
        )}

        {candidate.overall_feedback && (
          <div className="mt-5">
            <div className="font-mono text-xs font-medium tracking-wide" style={{ color: "var(--text-dim)" }}>
              OVERALL FEEDBACK
            </div>
            <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--text-mid)" }}>{candidate.overall_feedback}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PIPELINE BOARD VIEW (columns by status)
// ---------------------------------------------------------------------------
function PipelineBoardView({ candidates, justMoved, onOpenCandidate }) {
  const byStatus = STATUS_KEYS.reduce((acc, key) => {
    acc[key] = candidates.filter((c) => c.status === key);
    return acc;
  }, {});

  return (
    <div className="thin-scroll flex gap-4 overflow-x-auto pb-3">
      {STATUSES.map((status) => (
        <Column
          key={status.key}
          status={status}
          candidates={byStatus[status.key]}
          justMoved={justMoved}
          onOpenCandidate={onOpenCandidate}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BY ROLE VIEW
// ---------------------------------------------------------------------------
function RoleSummaryCard({ role, candidates, onOpenCandidate }) {
  const total = candidates.length;
  const bySource = candidates.reduce((acc, c) => {
    acc[c.source] = (acc[c.source] || 0) + 1;
    return acc;
  }, {});
  const topSource = Object.entries(bySource).sort((a, b) => b[1] - a[1])[0];
  const active = candidates.filter((c) => !["booked", "declined"].includes(c.status)).length;
  const escalated = candidates.filter((c) => c.status === "escalated").length;
  const booked = candidates.filter((c) => c.status === "booked").length;

  return (
    <div
      className="rounded-xl p-4"
      style={{ background: "var(--surface)", border: "1px solid var(--line)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-display text-sm font-semibold" style={{ color: "var(--text-hi)" }}>{role}</div>
          <div className="font-mono text-xs" style={{ color: "var(--text-mid)" }}>{total} total · {active} active</div>
        </div>
        {escalated > 0 && (
          <span
            className="escalate-pulse rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ background: hexToRgba("#FB5D74", 0.16), color: "#FB5D74" }}
          >
            {escalated} escalated
          </span>
        )}
      </div>

      <div className="mt-3 flex h-1.5 w-full overflow-hidden rounded-full" style={{ background: "var(--line)" }}>
        {STATUSES.map((status) => {
          const count = candidates.filter((c) => c.status === status.key).length;
          if (count === 0 || total === 0) return null;
          const pct = (count / total) * 100;
          return (
            <div
              key={status.key}
              className="h-full transition-all duration-500"
              style={{ width: `${pct}%`, background: status.color }}
              title={`${status.label}: ${count}`}
            />
          );
        })}
      </div>

      <div className="mt-3 flex items-center justify-between font-mono text-xs" style={{ color: "var(--text-mid)" }}>
        <span>Top source: {topSource ? `${topSource[0]} (${topSource[1]})` : "—"}</span>
        <span>{booked} booked</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {candidates.slice(0, 6).map((c) => {
          const s = STATUS_MAP[c.status] || STATUSES[0];
          return (
            <button
              key={c.id}
              onClick={() => onOpenCandidate(c)}
              className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-medium transition-transform hover:scale-110"
              style={{ background: hexToRgba(s.color, 0.18), color: s.color }}
              title={c.name}
            >
              {initials(c.name)}
            </button>
          );
        })}
        {candidates.length > 6 && (
          <span className="flex h-6 items-center px-1 font-mono text-[10px]" style={{ color: "var(--text-dim)" }}>
            +{candidates.length - 6}
          </span>
        )}
      </div>
    </div>
  );
}

function RoleSummaryView({ candidates, onOpenCandidate }) {
  const roles = Array.from(new Set(candidates.map((c) => c.role))).sort();
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {roles.map((role) => (
        <RoleSummaryCard
          key={role}
          role={role}
          candidates={candidates.filter((c) => c.role === role)}
          onOpenCandidate={onOpenCandidate}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BY RECRUITER VIEW -- same layout as By Role, grouped by
// hiring_recruiter_name instead. Candidates with no recruiter assigned
// yet are grouped under "Unassigned".
// ---------------------------------------------------------------------------
// Normalizes a recruiter name for grouping: trims whitespace and treats
// blank/missing values as "Unassigned". Sheets data can have trailing
// spaces or inconsistent casing from manual entry (e.g. "Usha " vs
// "Usha"), which without this would silently split into separate cards.
function normalizeRecruiterName(raw) {
  const trimmed = (raw || "").trim();
  return trimmed || "Unassigned";
}

function RecruiterSummaryView({ candidates, onOpenCandidate }) {
  const recruiters = Array.from(
    new Set(candidates.map((c) => normalizeRecruiterName(c.hiring_recruiter_name)))
  ).sort();
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {recruiters.map((recruiter) => (
        <RoleSummaryCard
          key={recruiter}
          role={recruiter}
          candidates={candidates.filter((c) => normalizeRecruiterName(c.hiring_recruiter_name) === recruiter)}
          onOpenCandidate={onOpenCandidate}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OVERVIEW VIEW -- messaging stats, response rate, per-role breakdown, and
// a timeline of messages sent over the last 7 days.
// ---------------------------------------------------------------------------
function StatCard({ label, value, sublabel, color, onClick, active }) {
  const isAnimatable = typeof value === "number";
  const animated = useCountUp(isAnimatable ? value : 0);
  const displayValue = isAnimatable ? animated : value;

  return (
    <button
      onClick={onClick}
      className="group w-full rounded-xl p-4 text-left transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg active:scale-[0.98]"
      style={{
        background: "var(--surface)",
        border: active ? `1px solid ${color || "var(--signal)"}` : "1px solid var(--line)",
        boxShadow: active ? `0 0 0 3px ${hexToRgba(color || "#0891B2", 0.15)}` : "0 1px 2px rgba(0,0,0,0.3)",
      }}
    >
      <div className="font-mono text-xs uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>{label}</div>
      <div className="mt-2 font-display text-2xl font-semibold" style={{ color: color || "var(--text-hi)" }}>
        {displayValue}
      </div>
      {sublabel && <div className="mt-1 text-xs" style={{ color: "var(--text-mid)" }}>{sublabel}</div>}
    </button>
  );
}

function OverviewView({ candidates }) {
  const [selectedDay, setSelectedDay] = useState(null);
  const [selectedRole, setSelectedRole] = useState(null);
  const [selectedStat, setSelectedStat] = useState(null);

  const stats = useMemo(() => {
    const outreachSent = candidates.filter((c) => c.first_outreach_sent_at).length;
    // Distinct from "outreachSent" above (which counts anyone EVER sent
    // outreach, even if they've since replied/progressed) -- this counts
    // candidates who are STILL sitting at the outreach stage with no
    // progress yet, i.e. genuinely awaiting a first response.
    const stillAtOutreachOnly = candidates.filter((c) => c.status === "messaged").length;
    const followup1Sent = candidates.filter((c) => c.followup_1_sent_at).length;
    const followup2Sent = candidates.filter((c) => c.followup_2_sent_at).length;
    const interviewFollowupSent = candidates.filter((c) => c.interview_followup_sent_at).length;
    const totalMessagesSent = outreachSent + followup1Sent + followup2Sent + interviewFollowupSent;

    const engaged = candidates.filter((c) => ENGAGED_STATUSES.has(c.status)).length;
    const neverResponded = outreachSent - engaged > 0 ? outreachSent - engaged : 0;
    const responseRate = outreachSent > 0 ? Math.round((engaged / outreachSent) * 100) : 0;

    // Accurate sent/received counts computed directly from
    // conversation_history, now that every send (outreach, follow-ups,
    // template resends) and every reply properly appends a turn there --
    // this is more complete than the timestamp-field-based
    // totalMessagesSent above, since it also counts multi-turn LLM
    // conversations (e.g. several back-and-forth exchanges), not just
    // one entry per template type.
    let messagesSent = 0;
    let messagesReceived = 0;
    candidates.forEach((c) => {
      (c.conversation_history || []).forEach((turn) => {
        if (turn.role === "assistant") messagesSent += 1;
        else if (turn.role === "user") messagesReceived += 1;
      });
    });

    return { outreachSent, followup1Sent, followup2Sent, interviewFollowupSent, totalMessagesSent, engaged, neverResponded, responseRate, messagesSent, messagesReceived, stillAtOutreachOnly };
  }, [candidates]);

  const perRole = useMemo(() => {
    const roles = Array.from(new Set(candidates.map((c) => c.role))).sort();
    return roles.map((role) => {
      const inRole = candidates.filter((c) => c.role === role);
      const outreach = inRole.filter((c) => c.first_outreach_sent_at).length;
      const engaged = inRole.filter((c) => ENGAGED_STATUSES.has(c.status)).length;
      return {
        role,
        total: inRole.length,
        outreach,
        engaged,
        rate: outreach > 0 ? Math.round((engaged / outreach) * 100) : 0,
      };
    });
  }, [candidates]);

  const [chartView, setChartView] = useState("7d"); // "7d" | "all"

  const timeline = useMemo(() => {
    let rangeDays;
    if (chartView === "7d") {
      rangeDays = 6;
    } else {
      // "All time" -- find the earliest activity timestamp across every
      // candidate's send events, and build one bucket per day from
      // there to today.
      const allTimestamps = [];
      candidates.forEach((c) => {
        [c.first_outreach_sent_at, c.followup_1_sent_at, c.followup_2_sent_at, c.interview_followup_sent_at]
          .filter(Boolean)
          .forEach((t) => allTimestamps.push(new Date(t)));
      });
      if (allTimestamps.length === 0) {
        rangeDays = 6;
      } else {
        const earliest = new Date(Math.min(...allTimestamps));
        const today = new Date();
        rangeDays = Math.max(0, Math.round((today - earliest) / (1000 * 60 * 60 * 24)));
      }
    }

    const days = [];
    for (let i = rangeDays; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      const label = chartView === "7d"
        ? d.toLocaleDateString("en-IN", { weekday: "short" })
        : d.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
      days.push({ key, label, outreach: 0, followup: 0 });
    }
    const byKey = Object.fromEntries(days.map((d) => [d.key, d]));

    const bump = (iso, field) => {
      if (!iso) return;
      const key = iso.slice(0, 10);
      if (byKey[key]) byKey[key][field] += 1;
    };

    candidates.forEach((c) => {
      bump(c.first_outreach_sent_at, "outreach");
      bump(c.followup_1_sent_at, "followup");
      bump(c.followup_2_sent_at, "followup");
      bump(c.interview_followup_sent_at, "followup");
    });

    return days;
  }, [candidates, chartView]);

  // For the day currently selected (by clicking a bar), find exactly
  // which candidates were messaged that day and with what.
  const selectedDayDetails = useMemo(() => {
    if (!selectedDay) return [];
    const entries = [];
    candidates.forEach((c) => {
      const checks = [
        { iso: c.first_outreach_sent_at, label: "Initial outreach" },
        { iso: c.followup_1_sent_at, label: "Follow-up 1" },
        { iso: c.followup_2_sent_at, label: "Follow-up 2 (final)" },
        { iso: c.interview_followup_sent_at, label: "Interview follow-up" },
      ];
      checks.forEach(({ iso, label }) => {
        if (iso && iso.slice(0, 10) === selectedDay) {
          entries.push({ name: c.name, role: c.role, type: label, id: c.id });
        }
      });
    });
    return entries;
  }, [selectedDay, candidates]);

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard
          label="Messages sent" value={stats.messagesSent} sublabel="all outbound, including replies" color="#0891B2"
        />
        <StatCard
          label="Messages received" value={stats.messagesReceived} sublabel="all inbound from candidates" color="#9333EA"
        />
        <StatCard
          label="Total candidates" value={candidates.length} sublabel="in the pipeline" color="#0891B2"
          active={selectedStat === "total"}
          onClick={() => setSelectedStat(selectedStat === "total" ? null : "total")}
        />
        <StatCard
          label="Awaiting first reply" value={stats.stillAtOutreachOnly} sublabel="sent outreach, no response yet" color="#4C8DFF"
          active={selectedStat === "outreach"}
          onClick={() => setSelectedStat(selectedStat === "outreach" ? null : "outreach")}
        />
        <StatCard
          label="Follow-up 1" value={stats.followup1Sent} color="#9B7BFF"
          active={selectedStat === "followup1"}
          onClick={() => setSelectedStat(selectedStat === "followup1" ? null : "followup1")}
        />
        <StatCard
          label="Follow-up 2 (final)" value={stats.followup2Sent} color="#D97706"
          active={selectedStat === "followup2"}
          onClick={() => setSelectedStat(selectedStat === "followup2" ? null : "followup2")}
        />
        <StatCard
          label="Response rate" value={`${stats.responseRate}%`} sublabel={`${stats.engaged} replied · ${stats.neverResponded} silent`} color="#16A34A"
          active={selectedStat === "response"}
          onClick={() => setSelectedStat(selectedStat === "response" ? null : "response")}
        />
      </div>

      {selectedStat && (() => {
        const filters = {
          // "total" now matches the "Total candidates" card's own
          // definition (candidates.length, i.e. literally everyone) --
          // this used to mean "has some outreach/follow-up activity",
          // which became inconsistent with the card's label and
          // displayed number after it was renamed from "Templates
          // dispatched" to "Total candidates" earlier. Clicking a stat
          // card should always expand to exactly what its own number
          // represents.
          total: () => true,
          outreach: (c) => c.status === "messaged",
          followup1: (c) => c.followup_1_sent_at,
          followup2: (c) => c.followup_2_sent_at,
          response: (c) => ENGAGED_STATUSES.has(c.status),
        };
        const matching = candidates.filter(filters[selectedStat]);
        return (
          <div className="mt-3 rounded-xl p-3" style={{ background: "var(--surface-hover)", border: "1px solid var(--line)" }}>
            <div className="flex items-center justify-between">
              <div className="font-mono text-xs uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>
                {matching.length} candidate{matching.length === 1 ? "" : "s"}
              </div>
              <button
                onClick={() => setSelectedStat(null)}
                className="text-xs transition-colors hover:text-[var(--text-hi)]"
                style={{ color: "var(--text-dim)" }}
              >
                Close ✕
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {matching.map((c) => {
                const s = STATUS_MAP[c.status] || STATUSES[0];
                return (
                  <span
                    key={c.id}
                    className="rounded-full px-2 py-0.5 text-xs"
                    style={{ background: hexToRgba(s.color, 0.14), color: s.color }}
                  >
                    {c.name} · {c.role}
                  </span>
                );
              })}
            </div>
          </div>
        );
      })()}

      <div className="mt-5 rounded-xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--line)" }}>
        <div className="flex items-center justify-between">
          <div className="font-display text-sm font-semibold" style={{ color: "var(--text-hi)" }}>
            {chartView === "7d" ? "Messages sent, last 7 days" : "Messages sent, all time"}
          </div>
          <div className="flex gap-0.5 rounded-full p-0.5" style={{ background: "var(--surface-hover)", border: "1px solid var(--line)" }}>
            <button
              onClick={() => setChartView("7d")}
              className="rounded-full px-3 py-1 text-xs font-medium transition-all duration-200"
              style={chartView === "7d" ? { background: "var(--signal)", color: "#ffffff" } : { color: "var(--text-mid)" }}
            >
              Last 7 days
            </button>
            <button
              onClick={() => setChartView("all")}
              className="rounded-full px-3 py-1 text-xs font-medium transition-all duration-200"
              style={chartView === "all" ? { background: "var(--signal)", color: "#ffffff" } : { color: "var(--text-mid)" }}
            >
              All time
            </button>
          </div>
        </div>
        <div className="mt-4 h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={timeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
              <XAxis dataKey="label" stroke="var(--text-dim)" fontSize={11} tickLine={false} axisLine={false} />
              <YAxis stroke="var(--text-dim)" fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip
                contentStyle={{ background: "var(--surface-hover)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--text-hi)" }}
                labelStyle={{ color: "var(--text-mid)" }}
              />
              <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-mid)" }} />
              <Bar
                dataKey="outreach"
                name="Outreach"
                fill="#4C8DFF"
                radius={[3, 3, 0, 0]}
                cursor="pointer"
                onClick={(data) => setSelectedDay(data.key === selectedDay ? null : data.key)}
              />
              <Bar
                dataKey="followup"
                name="Follow-ups"
                fill="#9B7BFF"
                radius={[3, 3, 0, 0]}
                cursor="pointer"
                onClick={(data) => setSelectedDay(data.key === selectedDay ? null : data.key)}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {selectedDay && (
          <div className="mt-4 rounded-lg p-3" style={{ background: "var(--surface-hover)", border: "1px solid var(--line)" }}>
            <div className="flex items-center justify-between">
              <div className="font-mono text-xs uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>
                {new Date(selectedDay).toLocaleDateString("en-IN", { weekday: "long", month: "short", day: "numeric" })}
                {" · "}{selectedDayDetails.length} message{selectedDayDetails.length === 1 ? "" : "s"} sent
              </div>
              <button
                onClick={() => setSelectedDay(null)}
                className="text-xs transition-colors hover:text-[var(--text-hi)]"
                style={{ color: "var(--text-dim)" }}
              >
                Close ✕
              </button>
            </div>
            {selectedDayDetails.length === 0 ? (
              <div className="mt-2 font-mono text-xs" style={{ color: "var(--text-dim)" }}>No messages sent this day.</div>
            ) : (
              <div className="mt-2 space-y-1">
                {selectedDayDetails.map((d, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span style={{ color: "var(--text-hi)" }}>{d.name} <span style={{ color: "var(--text-mid)" }}>· {d.role}</span></span>
                    <span className="font-mono text-xs" style={{ color: "var(--text-mid)" }}>{d.type}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-5 overflow-x-auto rounded-xl" style={{ border: "1px solid var(--line)" }}>
        <table className="w-full min-w-[600px] border-collapse text-sm">
          <thead>
            <tr
              className="border-b text-left font-mono text-xs font-medium uppercase tracking-wide"
              style={{ borderColor: "var(--line)", background: "var(--surface)", color: "var(--text-dim)" }}
            >
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Total</th>
              <th className="px-3 py-2">Outreach sent</th>
              <th className="px-3 py-2">Replied</th>
              <th className="px-3 py-2">Response rate</th>
            </tr>
          </thead>
          <tbody>
            {perRole.map((r) => (
              <React.Fragment key={r.role}>
                <tr
                  onClick={() => setSelectedRole(r.role === selectedRole ? null : r.role)}
                  className="cursor-pointer border-b transition-colors last:border-0 hover:bg-white/5"
                  style={{ borderColor: "var(--line)" }}
                >
                  <td className="px-3 py-2 font-medium" style={{ color: "var(--text-hi)" }}>{r.role}</td>
                  <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.total}</td>
                  <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.outreach}</td>
                  <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.engaged}</td>
                  <td className="px-3 py-2 font-mono font-medium" style={{ color: "#34D399" }}>{r.rate}%</td>
                </tr>
                {selectedRole === r.role && (
                  <tr style={{ background: "var(--surface-hover)" }}>
                    <td colSpan={5} className="px-3 py-2">
                      <div className="flex flex-wrap gap-1.5">
                        {candidates.filter((c) => c.role === r.role).map((c) => {
                          const s = STATUS_MAP[c.status] || STATUSES[0];
                          return (
                            <span
                              key={c.id}
                              className="rounded-full px-2 py-0.5 text-xs"
                              style={{ background: hexToRgba(s.color, 0.14), color: s.color }}
                            >
                              {c.name}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
            {perRole.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center font-mono text-xs" style={{ color: "var(--text-dim)" }}>
                  No candidates yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NO WHATSAPP VIEW -- candidates who couldn't be reached via WhatsApp.
//
// NOTE: as of this build, nothing on the backend actually detects or sets
// this yet -- app/google_sheets.py always defaults `platform` to
// "whatsapp" for every synced candidate. This view is ready to display
// such candidates once that detection is added (e.g. a WhatsApp-existence
// check during Sheets ingestion); until then, this list will stay empty.
// ---------------------------------------------------------------------------
function NoWhatsappView({ candidates, onOpenCandidate }) {
  // Exclude anyone genuinely engaged (replied, qualified, escalated,
  // etc.) even if platform === "no_whatsapp" -- that flag only means
  // ONE specific template send failed (e.g. a follow-up template still
  // categorized as Marketing and blocked by Meta), not that the person
  // is unreachable on WhatsApp entirely. Someone actively mid-conversation
  // clearly CAN be reached, so showing them in a tab literally called
  // "No WhatsApp" was misleading.
  const noWhatsapp = candidates.filter(
    (c) => c.platform !== "whatsapp" && !ENGAGED_STATUSES.has(c.status)
  );

  return (
    <div>
      <div
        className="mb-4 rounded-xl p-4 font-mono text-xs"
        style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
      >
        Candidates who couldn't actually be reached on WhatsApp -- flagged automatically via Twilio's
        delivery status callbacks (or manually, for known account/template-level delivery blocks).
      </div>

      {noWhatsapp.length === 0 ? (
        <div
          className="flex items-center justify-center rounded-xl py-12 font-mono text-sm"
          style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          — no candidates flagged —
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {noWhatsapp.map((c) => (
            <button
              key={c.id}
              onClick={() => onOpenCandidate(c)}
              className="rounded-lg border p-3 text-left transition-colors hover:bg-white/5"
              style={{ background: "var(--surface)", borderColor: "var(--line)" }}
            >
              <div className="text-sm font-medium" style={{ color: "var(--text-hi)" }}>{c.name}</div>
              <div className="mt-0.5 font-mono text-xs" style={{ color: "var(--text-mid)" }}>{c.phone} · {c.role}</div>
              {c.delivery_error_message && (
                <div className="mt-1.5 font-mono text-[11px]" style={{ color: "#FB5D74" }}>
                  {c.delivery_status} ({c.delivery_error_code}): {c.delivery_error_message}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MESSAGES VIEW -- list of candidates with any conversation activity, and
// a WhatsApp-style transcript of their full conversation_history when
// selected.
// ---------------------------------------------------------------------------
function ChatBubble({ turn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-start" : "justify-end"}`}>
      <div
        className="max-w-[75%] rounded-2xl px-3 py-2 text-sm"
        style={
          isUser
            ? { background: "var(--surface-hover)", color: "var(--text-hi)", borderTopLeftRadius: 4 }
            : { background: hexToRgba("#0891B2", 0.16), color: "var(--text-hi)", borderTopRightRadius: 4 }
        }
      >
        {turn.content}
      </div>
    </div>
  );
}

function MessagesView({ candidates, onOpenCandidate }) {
  const [searchQuery, setSearchQuery] = useState("");
  const withMessages = candidates.filter((c) => c.conversation_history && c.conversation_history.length > 0);
  const filteredMessages = withMessages.filter((c) => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    return (c.name || "").toLowerCase().includes(q) || (c.phone || "").includes(q);
  });
  const [selectedId, setLocalSelectedId] = useState(withMessages[0]?.id || null);
  const selected = filteredMessages.find((c) => c.id === selectedId) || filteredMessages[0] || null;

  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  const sendReply = async () => {
    if (!replyText.trim() || !selected) return;
    setSending(true);
    setSendError("");
    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL;
      const res = await fetch(`${apiBase}/send-message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: selected.id, message: replyText }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSendError(data.detail || "Failed to send message.");
      } else {
        setReplyText("");
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Send message failed:", err);
      setSendError("Failed to send — check that the backend server is running.");
    } finally {
      setSending(false);
    }
  };

  // NOTE: messages from previously-unrecognized numbers no longer need
  // a special banner here -- they now auto-create a real candidate
  // record (see app/status.py's create_candidate_for_unknown_number)
  // and flow through the exact same conversation UI as everyone else.
  // record_unmatched_message() still runs as a lightweight backend audit
  // log, just not surfaced as separate UI here anymore.
  const UnmatchedSection = null;

  if (withMessages.length === 0) {
    return (
      <div>
        {UnmatchedSection}
        <div
          className="flex items-center justify-center rounded-xl py-12 font-mono text-sm"
          style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
        >
          — no conversations yet —
        </div>
      </div>
    );
  }

  return (
    <div>
      {UnmatchedSection}
      <div className="flex gap-4" style={{ height: "600px" }}>
      <div
        className="flex w-72 shrink-0 flex-col overflow-hidden rounded-xl"
        style={{ background: "var(--surface)", border: "1px solid var(--line)" }}
      >
        <div className="border-b p-2" style={{ borderColor: "var(--line)" }}>
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by name or number..."
            className={inputClass}
            style={inputStyle}
          />
        </div>
        <div className="thin-scroll flex-1 overflow-y-auto">
        {filteredMessages
          .slice()
          .sort((a, b) => new Date(b.last_activity_at) - new Date(a.last_activity_at))
          .map((c) => {
            const lastTurn = c.conversation_history[c.conversation_history.length - 1];
            const isSelected = selected && c.id === selected.id;
            return (
              <button
                key={c.id}
                onClick={() => setLocalSelectedId(c.id)}
                className="block w-full border-b px-3 py-2.5 text-left transition-colors hover:bg-white/5"
                style={{
                  borderColor: "var(--line)",
                  background: isSelected ? "var(--surface-hover)" : "transparent",
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium" style={{ color: "var(--text-hi)" }}>{c.name}</span>
                  <span className="shrink-0 font-mono text-[10px]" style={{ color: "var(--text-dim)" }}>
                    {timeAgo(c.last_activity_at)}
                  </span>
                </div>
                <div className="mt-0.5 truncate text-xs" style={{ color: "var(--text-mid)" }}>
                  {lastTurn ? lastTurn.content : ""}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div
        className="flex flex-1 flex-col overflow-hidden rounded-xl"
        style={{ background: "var(--surface)", border: "1px solid var(--line)" }}
      >
        {selected ? (
          <>
            <button
              onClick={() => onOpenCandidate(selected)}
              className="flex shrink-0 items-center gap-2.5 border-b px-4 py-3 text-left transition-colors hover:bg-white/5"
              style={{ borderColor: "var(--line)" }}
            >
              <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold font-display"
                style={{ background: hexToRgba(STATUS_MAP[selected.status]?.color || "#8892B0", 0.18), color: STATUS_MAP[selected.status]?.color }}
              >
                {initials(selected.name)}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium" style={{ color: "var(--text-hi)" }}>{selected.name}</div>
                <div className="truncate text-xs" style={{ color: "var(--text-mid)" }}>{selected.role} · {selected.phone}</div>
                {selected.status === "messaged" && (
                  selected.platform === "no_whatsapp" ? (
                    <div className="mt-0.5 truncate text-xs font-medium" style={{ color: "#FB5D74" }}>
                      Failed to deliver — see the No WhatsApp tab
                    </div>
                  ) : (
                    <div className="mt-0.5 truncate text-xs font-medium" style={{ color: "#FFB020" }}>
                      Delivered, no reply yet
                    </div>
                  )
                )}
                {selected.status === "messaged" && (
                  selected.platform === "no_whatsapp" ? (
                    <div className="mt-0.5 truncate text-xs font-medium" style={{ color: "#FB5D74" }}>
                      Failed to deliver — see the No WhatsApp tab
                    </div>
                  ) : (
                    <div className="mt-0.5 truncate text-xs font-medium" style={{ color: "#FFB020" }}>
                      Delivered, no reply yet
                    </div>
                  )
                )}
              </div>
            </button>
            <div className="thin-scroll flex-1 space-y-2 overflow-y-auto p-4">
              {selected.conversation_history.map((turn, i) => (
                <ChatBubble key={i} turn={turn} />
              ))}
            </div>
            <div className="border-t p-3" style={{ borderColor: "var(--line)" }}>
              {sendError && (
                <div className="mb-2 font-mono text-xs" style={{ color: "#FB5D74" }}>{sendError}</div>
              )}
              <div className="flex gap-2">
                <input
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !sending && sendReply()}
                  placeholder="Type a reply..."
                  className={inputClass}
                  style={inputStyle}
                />
                <button
                  onClick={sendReply}
                  disabled={sending || !replyText.trim()}
                  className="shrink-0 rounded-md px-4 py-1.5 text-sm font-medium transition-all duration-150 hover:opacity-90 active:scale-95 disabled:opacity-50"
                  style={{ background: "var(--signal)", color: "#ffffff" }}
                >
                  {sending ? "Sending..." : "Send"}
                </button>
              </div>
              <div className="mt-1.5 font-mono text-[11px]" style={{ color: "var(--text-dim)" }}>
                Only works within 24 hours of the candidate's last message (WhatsApp policy).
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center font-mono text-sm" style={{ color: "var(--text-dim)" }}>
            Select a conversation
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SETTINGS VIEW -- lets HR edit per-role work type/hours directly,
// without needing an engineer to change code. Backs the {{3}}/{{4}}
// placeholders in the outreach WhatsApp template (see
// agent/pipeline.py's _build_job_outreach_variables and
// app/status.py's get_role_work_details).
// ---------------------------------------------------------------------------
function SettingsView({ candidates }) {
  const [details, setDetails] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState({});
  const [savedFlash, setSavedFlash] = useState({});
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleMessage, setNewRoleMessage] = useState("");

  // Per-recruiter Calendly links, not a single global one -- different
  // recruiters use their own booking link, and candidates should be
  // sent whichever recruiter they're actually assigned to (from
  // Sheets ingestion's hiring_recruiter_name field).
  const [recruiterLinks, setRecruiterLinks] = useState({});
  const [recruiterLinkSaving, setRecruiterLinkSaving] = useState({});
  const [recruiterLinkSavedFlash, setRecruiterLinkSavedFlash] = useState({});

  const [newRecruiterName, setNewRecruiterName] = useState("");
  const [newRecruiterMessage, setNewRecruiterMessage] = useState("");

  // Recruiters come from BOTH existing candidates AND whatever's already
  // saved in recruiter_links -- this second source matters because it
  // lets HR proactively add a recruiter's link (via "+ Add new
  // recruiter" below) before any candidate is assigned to them yet,
  // rather than only being able to react after the fact.
  const recruiters = Array.from(
    new Set([
      ...candidates.map((c) => c.hiring_recruiter_name).filter(Boolean),
      ...Object.keys(recruiterLinks),
    ])
  ).sort();

  const addNewRecruiter = () => {
    const trimmed = newRecruiterName.trim();
    if (!trimmed) return;
    if (recruiterLinks[trimmed] !== undefined) {
      setNewRecruiterMessage(`"${trimmed}" already exists below.`);
      window.setTimeout(() => setNewRecruiterMessage(""), 2500);
      return;
    }
    setRecruiterLinks((prev) => ({ ...prev, [trimmed]: "" }));
    setNewRecruiterName("");
  };

  const updateRecruiterLink = (recruiter, value) => {
    setRecruiterLinks((prev) => ({ ...prev, [recruiter]: value }));
  };

  const saveRecruiterLink = async (recruiter) => {
    setRecruiterLinkSaving((prev) => ({ ...prev, [recruiter]: true }));
    const { error } = await supabase
      .from("recruiter_links")
      .upsert({ recruiter_name: recruiter, calendly_link: recruiterLinks[recruiter] || "", updated_at: new Date().toISOString() });
    setRecruiterLinkSaving((prev) => ({ ...prev, [recruiter]: false }));
    if (!error) {
      setRecruiterLinkSavedFlash((prev) => ({ ...prev, [recruiter]: true }));
      window.setTimeout(() => setRecruiterLinkSavedFlash((prev) => ({ ...prev, [recruiter]: false })), 2000);
    } else {
      // eslint-disable-next-line no-console
      console.error("Failed to save recruiter's Calendly link:", error);
    }
  };

  const blankRoleDetails = { work_type: "", hours: "", job_description: "", company_name: "", company_website: "", mandatory_questions: "", optional_questions: "" };

  // Roles come from BOTH existing candidates AND whatever's already saved
  // in role_work_details -- this second source matters because it lets HR
  // proactively add a brand-new role's details (via "+ Add new role"
  // below) BEFORE any candidate for that role has synced in yet, rather
  // than only being able to react after the first candidate arrives with
  // "TBD" placeholders.
  const roles = Array.from(
    new Set([...candidates.map((c) => c.role), ...Object.keys(details)])
  ).filter(Boolean).sort();

  const addNewRole = () => {
    const trimmed = newRoleName.trim();
    if (!trimmed) return;
    if (details[trimmed]) {
      setNewRoleMessage(`"${trimmed}" already exists below.`);
      window.setTimeout(() => setNewRoleMessage(""), 2500);
      return;
    }
    setDetails((prev) => ({ ...prev, [trimmed]: { ...blankRoleDetails } }));
    setNewRoleName("");
  };

  useEffect(() => {
    (async () => {
      const { data, error } = await supabase.from("role_work_details").select("*");
      if (!error && data) {
        const map = {};
        data.forEach((r) => {
          map[r.role] = {
            work_type: r.work_type || "",
            hours: r.hours || "",
            job_description: r.job_description || "",
            company_name: r.company_name || "",
            company_website: r.company_website || "",
            mandatory_questions: r.mandatory_questions || "",
            optional_questions: r.optional_questions || "",
          };
        });
        setDetails(map);
      }

      const { data: recruiterLinksData } = await supabase.from("recruiter_links").select("*");
      if (recruiterLinksData) {
        const map = {};
        recruiterLinksData.forEach((r) => { map[r.recruiter_name] = r.calendly_link || ""; });
        setRecruiterLinks(map);
      }

      setLoading(false);
    })();
  }, []);

  const updateField = (role, field, value) => {
    setDetails((prev) => ({
      ...prev,
      [role]: { ...(prev[role] || blankRoleDetails), [field]: value },
    }));
  };

  const saveRole = async (role) => {
    setSaving((prev) => ({ ...prev, [role]: true }));
    const row = details[role] || blankRoleDetails;
    const { error } = await supabase
      .from("role_work_details")
      .upsert({
        role,
        work_type: row.work_type,
        hours: row.hours,
        job_description: row.job_description,
        company_name: row.company_name,
        company_website: row.company_website,
        mandatory_questions: row.mandatory_questions,
        optional_questions: row.optional_questions,
        updated_at: new Date().toISOString(),
      });
    setSaving((prev) => ({ ...prev, [role]: false }));
    if (!error) {
      setSavedFlash((prev) => ({ ...prev, [role]: true }));
      window.setTimeout(() => setSavedFlash((prev) => ({ ...prev, [role]: false })), 2000);
    } else {
      // eslint-disable-next-line no-console
      console.error("Failed to save role work details:", error);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center rounded-xl py-12 font-mono text-sm" style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
        Loading...
      </div>
    );
  }

  return (
    <div>
      <div
        className="mb-4 rounded-xl p-4"
        style={{ background: "var(--surface)", border: "1px solid var(--line)" }}
      >
        <div className="font-mono text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>
          Interview booking links, by recruiter
        </div>
        <div className="mt-1 text-xs" style={{ color: "var(--text-mid)" }}>
          Each recruiter has their own Calendly link. Candidates are sent whichever recruiter they're actually assigned to (from the sheet's "hiring_recruiter_name" column).
        </div>
        <div className="mt-3 mb-3 flex flex-wrap items-center gap-2">
          <input
            value={newRecruiterName}
            onChange={(e) => setNewRecruiterName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addNewRecruiter()}
            placeholder="New recruiter name, e.g. Priya"
            className={inputClass}
            style={{ ...inputStyle, maxWidth: "220px" }}
          />
          <button
            onClick={addNewRecruiter}
            className="rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150 hover:opacity-90 active:scale-95"
            style={{ background: "var(--signal)", color: "#ffffff" }}
          >
            + Add new recruiter
          </button>
          {newRecruiterMessage && (
            <span className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>{newRecruiterMessage}</span>
          )}
        </div>
        <div className="space-y-2">
          {recruiters.map((recruiter) => (
            <div key={recruiter} className="flex items-center gap-2">
              <div className="w-32 shrink-0 truncate text-sm font-medium" style={{ color: "var(--text-hi)" }}>{recruiter}</div>
              <input
                value={recruiterLinks[recruiter] || ""}
                onChange={(e) => updateRecruiterLink(recruiter, e.target.value)}
                placeholder="https://calendly.com/this-recruiter/interview"
                className={inputClass}
                style={inputStyle}
              />
              <button
                onClick={() => saveRecruiterLink(recruiter)}
                disabled={recruiterLinkSaving[recruiter]}
                className="shrink-0 rounded-md px-4 py-1.5 text-sm font-medium transition-all duration-150 hover:opacity-90 active:scale-95 disabled:opacity-50"
                style={{ background: recruiterLinkSavedFlash[recruiter] ? "#16A34A" : "var(--signal)", color: "#ffffff" }}
              >
                {recruiterLinkSaving[recruiter] ? "Saving..." : recruiterLinkSavedFlash[recruiter] ? "Saved ✓" : "Save"}
              </button>
            </div>
          ))}
          {recruiters.length === 0 && (
            <div className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>
              No recruiters found yet -- add candidates with a hiring_recruiter_name first.
            </div>
          )}
        </div>
      </div>

      <div
        className="mb-4 rounded-xl p-4"
        style={{ background: hexToRgba("#0891B2", 0.08), border: "1px solid rgba(8,145,178,0.25)" }}
      >
        <div className="font-mono text-xs font-semibold uppercase tracking-wide" style={{ color: "#0891B2" }}>Example</div>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div>
            <div className="text-xs" style={{ color: "var(--text-dim)" }}>Role</div>
            <div className="text-sm font-medium" style={{ color: "var(--text-hi)" }}>NOC</div>
          </div>
          <div>
            <div className="text-xs" style={{ color: "var(--text-dim)" }}>Work Type</div>
            <div className="text-sm font-medium" style={{ color: "var(--text-hi)" }}>Remote</div>
          </div>
          <div>
            <div className="text-xs" style={{ color: "var(--text-dim)" }}>Hours</div>
            <div className="text-sm font-medium" style={{ color: "var(--text-hi)" }}>24x7 rotational shifts</div>
          </div>
        </div>
        <div className="mt-2 text-xs" style={{ color: "var(--text-mid)" }}>
          Work Type and Hours go in the first WhatsApp message. Company Name/Website and Job Description are shared naturally by the AI later in the conversation, whenever relevant (e.g. someone asks about the role or the company) -- useful since Amerisource recruits for multiple client companies, not just itself.
        </div>
      </div>

      {(() => {
        const unconfigured = roles.filter((role) => {
          const row = details[role] || blankRoleDetails;
          return !row.work_type.trim() || !row.hours.trim();
        });
        if (unconfigured.length === 0) return null;
        return (
          <div
            className="mb-4 rounded-xl p-3"
            style={{ background: hexToRgba("#FFB020", 0.1), border: "1px solid rgba(255,176,32,0.3)" }}
          >
            <div className="font-mono text-xs font-semibold uppercase tracking-wide" style={{ color: "#D97706" }}>
              Not yet configured -- candidates in these roles will see "TBD"
            </div>
            <div className="mt-1 text-sm" style={{ color: "var(--text-hi)" }}>
              {unconfigured.join(", ")}
            </div>
          </div>
        );
      })()}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={newRoleName}
          onChange={(e) => setNewRoleName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addNewRole()}
          placeholder="New role name, e.g. DevOps Engineer"
          className={inputClass}
          style={{ ...inputStyle, maxWidth: "280px" }}
        />
        <button
          onClick={addNewRole}
          className="rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150 hover:opacity-90 active:scale-95"
          style={{ background: "var(--signal)", color: "#ffffff" }}
        >
          + Add new role
        </button>
        {newRoleMessage && (
          <span className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>{newRoleMessage}</span>
        )}
      </div>

      <div className="space-y-3">
        {roles.map((role) => {
          const row = details[role] || blankRoleDetails;
          return (
            <div key={role} className="rounded-xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--line)" }}>
              <div className="flex items-center justify-between gap-3">
                <div className="font-display text-sm font-semibold" style={{ color: "var(--text-hi)" }}>{role}</div>
                <button
                  onClick={() => saveRole(role)}
                  disabled={saving[role]}
                  className="shrink-0 rounded-md px-4 py-1.5 text-xs font-medium transition-all duration-150 hover:opacity-90 active:scale-95 disabled:opacity-50"
                  style={{ background: savedFlash[role] ? "#16A34A" : "var(--signal)", color: "#ffffff" }}
                >
                  {saving[role] ? "Saving..." : savedFlash[role] ? "Saved ✓" : "Save"}
                </button>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Work Type</div>
                  <input
                    value={row.work_type}
                    onChange={(e) => updateField(role, "work_type", e.target.value)}
                    placeholder="e.g. Remote / Onsite / Hybrid"
                    className={inputClass}
                    style={inputStyle}
                  />
                </div>
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Hours</div>
                  <input
                    value={row.hours}
                    onChange={(e) => updateField(role, "hours", e.target.value)}
                    placeholder="e.g. 9 AM - 6 PM IST"
                    className={inputClass}
                    style={inputStyle}
                  />
                </div>
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Company Name</div>
                  <input
                    value={row.company_name}
                    onChange={(e) => updateField(role, "company_name", e.target.value)}
                    placeholder="e.g. Amerisource, or a client like MGT"
                    className={inputClass}
                    style={inputStyle}
                  />
                </div>
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Company Website</div>
                  <input
                    value={row.company_website}
                    onChange={(e) => updateField(role, "company_website", e.target.value)}
                    placeholder="https://..."
                    className={inputClass}
                    style={inputStyle}
                  />
                </div>
              </div>

              <div className="mt-2">
                <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Job Description</div>
                <textarea
                  value={row.job_description}
                  onChange={(e) => updateField(role, "job_description", e.target.value)}
                  placeholder="Short summary of the role, responsibilities, and requirements -- shared by the AI when a candidate asks for more info."
                  rows={3}
                  className={inputClass}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
              </div>

              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Mandatory Questions (one per line)</div>
                  <textarea
                    value={row.mandatory_questions}
                    onChange={(e) => updateField(role, "mandatory_questions", e.target.value)}
                    placeholder={"Name\nUpdated Resume\nCurrent Location\nCurrent Salary\nExpected Salary\nNotice Period\nTotal Years of Experience"}
                    rows={5}
                    className={inputClass}
                    style={{ ...inputStyle, resize: "vertical" }}
                  />
                  <div className="mt-1 text-[11px]" style={{ color: "var(--text-dim)" }}>
                    The AI keeps following up until every one of these is answered.
                  </div>
                </div>
                <div>
                  <div className="mb-1 font-mono text-[11px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>Optional Questions (one per line)</div>
                  <textarea
                    value={row.optional_questions}
                    onChange={(e) => updateField(role, "optional_questions", e.target.value)}
                    placeholder={"LinkedIn Profile\nGitHub Profile"}
                    rows={5}
                    className={inputClass}
                    style={{ ...inputStyle, resize: "vertical" }}
                  />
                  <div className="mt-1 text-[11px]" style={{ color: "var(--text-dim)" }}>
                    Asked once -- the AI never chases these if left unanswered.
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {roles.length === 0 && (
          <div className="rounded-xl py-6 text-center font-mono text-xs" style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}>
            No roles found yet -- add candidates first.
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ADD CANDIDATES VIEW
// ---------------------------------------------------------------------------
function blankRow() {
  return { name: "", email: "", phone: "", role: "", source: SOURCES_FALLBACK[0], status: "pending", notes: "" };
}

const inputClass =
  "w-full rounded-md border px-2 py-1.5 text-sm outline-none transition-colors focus:border-[var(--signal)]";
const inputStyle = { background: "var(--surface-hover)", borderColor: "var(--line)", color: "var(--text-hi)" };

function AddCandidatesView({ addCandidates }) {
  const [rows, setRows] = useState([blankRow()]);
  const [message, setMessage] = useState("");
  const fileInputRef = useRef(null);

  const updateCell = (idx, field, value) => {
    setRows((prev) => prev.map((row, i) => (i === idx ? { ...row, [field]: value } : row)));
  };

  const addRow = () => setRows((prev) => [...prev, blankRow()]);
  const removeRow = (idx) => setRows((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));

  const submitRows = async () => {
    const count = await addCandidates(rows);
    setMessage(count > 0 ? `Added ${count} candidate${count === 1 ? "" : "s"} to the pipeline.` : "Add at least a name to each row before submitting.");
    if (count > 0) setRows([blankRow()]);
  };

  const handleCsvUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: async (results) => {
        const mapped = results.data.map((r) => ({
          name: r.name || r.Name,
          email: r.email || r.Email,
          phone: r.phone || r.Phone,
          role: r.role || r.Role,
          source: r.source || r.Source,
          status: r.status || r.Status,
          notes: r.notes || r.Notes,
        }));
        const count = await addCandidates(mapped);
        setMessage(`Imported ${count} candidate${count === 1 ? "" : "s"} from CSV.`);
      },
      error: () => setMessage("Couldn't read that CSV — check the file and try again."),
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div>
      <div
        className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-4"
        style={{ borderColor: "var(--line)", background: "var(--surface)" }}
      >
        <div>
          <div className="text-sm font-medium" style={{ color: "var(--text-hi)" }}>Import from a spreadsheet</div>
          <div className="mt-0.5 font-mono text-xs" style={{ color: "var(--text-dim)" }}>
            CSV columns: name, email, phone, role, source, status, notes
          </div>
        </div>
        <label
          className="cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-white/5"
          style={{ borderColor: "var(--line)", color: "var(--text-hi)" }}
        >
          Upload CSV
          <input ref={fileInputRef} type="file" accept=".csv" className="hidden" onChange={handleCsvUpload} />
        </label>
      </div>

      <div className="overflow-x-auto rounded-xl" style={{ border: "1px solid var(--line)" }}>
        <table className="w-full min-w-[900px] border-collapse text-sm">
          <thead>
            <tr
              className="border-b text-left font-mono text-xs font-medium uppercase tracking-wide"
              style={{ borderColor: "var(--line)", background: "var(--surface)", color: "var(--text-dim)" }}
            >
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Email</th>
              <th className="px-3 py-2">Phone</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Notes</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              <tr key={idx} className="border-b last:border-0" style={{ borderColor: "var(--line)" }}>
                <td className="px-1.5 py-1.5">
                  <input value={row.name} onChange={(e) => updateCell(idx, "name", e.target.value)} placeholder="Jane Doe" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5">
                  <input value={row.email} onChange={(e) => updateCell(idx, "email", e.target.value)} placeholder="jane@example.com" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5">
                  <input value={row.phone} onChange={(e) => updateCell(idx, "phone", e.target.value)} placeholder="9876543210" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5">
                  <input value={row.role} onChange={(e) => updateCell(idx, "role", e.target.value)} placeholder="Backend Engineer" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5">
                  <input value={row.source} onChange={(e) => updateCell(idx, "source", e.target.value)} placeholder="Referral" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5">
                  <select value={row.status} onChange={(e) => updateCell(idx, "status", e.target.value)} className={inputClass} style={inputStyle}>
                    {STATUSES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                  </select>
                </td>
                <td className="px-1.5 py-1.5">
                  <input value={row.notes} onChange={(e) => updateCell(idx, "notes", e.target.value)} placeholder="Optional note" className={inputClass} style={inputStyle} />
                </td>
                <td className="px-1.5 py-1.5 text-right">
                  <button onClick={() => removeRow(idx)} className="px-2 text-xs transition-colors hover:text-[#FB5D74]" style={{ color: "var(--text-dim)" }}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <button
          onClick={addRow}
          className="rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-white/5"
          style={{ borderColor: "var(--line)", color: "var(--text-hi)" }}
        >
          + Add row
        </button>
        <div className="flex items-center gap-3">
          {message && <span className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>{message}</span>}
          <button
            onClick={submitRows}
            className="rounded-md px-4 py-1.5 text-sm font-medium transition-opacity hover:opacity-90"
            style={{ background: "var(--signal)", color: "var(--ink)" }}
          >
            Add to pipeline
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MAIN BOARD
// ---------------------------------------------------------------------------
const TABS = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "pipeline", label: "Pipeline board", icon: KanbanSquare },
  { key: "roles", label: "By role", icon: Users },
  { key: "recruiters", label: "By recruiter", icon: UserCog },
  { key: "messages", label: "Messages", icon: MessageSquareText },
  { key: "no-whatsapp", label: "No WhatsApp", icon: PhoneOff },
  { key: "add", label: "Add candidates", icon: UserPlus },
  { key: "settings", label: "Role & Messaging Setup" },
];

export default function CandidatePipelineBoard() {
  const { candidates, connection, lastSyncedAt, refresh, justMoved, addCandidates, markReviewed } = usePipelineData();
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState("overview");
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const selectedCandidate = candidates.find((c) => c.id === selectedId) || null;

  const [theme, setTheme] = useState(() => localStorage.getItem("dashboard-theme") || "light");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("dashboard-theme", theme);
  }, [theme]);

  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [skippedDetails, setSkippedDetails] = useState([]);

  const triggerSync = async () => {
    setSyncing(true);
    setSyncMessage("");
    setSkippedDetails([]);
    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL;
      const res = await fetch(`${apiBase}/trigger-sync`, { method: "POST" });
      const data = await res.json();
      const insertedCount = data.inserted?.length ?? 0;
      const skipped = data.skipped ?? [];

      let msg = `Synced: ${insertedCount} new candidate${insertedCount === 1 ? "" : "s"} added, messages queued.`;
      if (skipped.length > 0) {
        const reasonCounts = skipped.reduce((acc, s) => {
          acc[s.reason] = (acc[s.reason] || 0) + 1;
          return acc;
        }, {});
        const reasonSummary = Object.entries(reasonCounts)
          .map(([reason, count]) => `${count} ${reason}`)
          .join(", ");
        msg += ` ${skipped.length} skipped (${reasonSummary}).`;

        // Keep the actual name/phone/role/reason for each skipped row so
        // HR knows exactly WHO was rejected, WHY, and WHICH SHEET (role
        // tab) to go fix -- a bare count alone gives no way to find the
        // problem row. Duplicates are filtered out here since they're
        // not actionable (the candidate is already in the system); only
        // genuinely invalid rows need someone to go fix the sheet.
        setSkippedDetails(
          skipped
            .filter((s) => s.reason !== "duplicate")
            .map((s) => ({
              name: s.record?.name || "(no name)",
              phone: s.record?.phone || "(no phone)",
              role: s.record?.role || "(unknown sheet)",
              reason: s.reason,
            }))
        );
      }
      setSyncMessage(msg);
      refresh();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Trigger sync failed:", err);
      setSyncMessage("Sync failed — check that the backend server is running.");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="w-full">
      <div className="fade-slide-in mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight" style={{ color: "var(--text-hi)" }}>
            Amerisource <span style={{ color: "var(--signal)" }}>·</span> Candidate Pipeline
          </h1>
          <p className="mt-1 text-sm" style={{ color: "var(--text-mid)" }}>
            {candidates.length} candidate{candidates.length === 1 ? "" : "s"} · click a card for full details
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex gap-0.5 rounded-full p-0.5" style={{ background: "var(--surface-hover)", border: "1px solid var(--line)" }}>
            {[
              { key: "light", label: "Light" },
              { key: "dark", label: "Dark" },
              { key: "cyber", label: "Cyber" },
            ].map((opt) => (
              <button
                key={opt.key}
                onClick={() => setTheme(opt.key)}
                className="rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-200 active:scale-90"
                style={
                  theme === opt.key
                    ? { background: "var(--signal)", color: theme === "light" ? "#ffffff" : "var(--ink)" }
                    : { color: "var(--text-mid)" }
                }
              >
                {opt.label}
              </button>
            ))}
          </div>
          <span className="font-mono text-xs" style={{ color: "var(--text-dim)" }}>
            {clock.toLocaleTimeString("en-IN", { hour12: false })} IST
          </span>
          <ConnectionBadge connection={connection} lastSyncedAt={lastSyncedAt} />
          <button
            onClick={refresh}
            className="rounded-md border px-3 py-1.5 text-sm font-medium transition-all duration-150 hover:bg-white/5 active:scale-95"
            style={{ borderColor: "var(--line)", color: "var(--text-hi)" }}
          >
            Refresh
          </button>
          <button
            onClick={triggerSync}
            disabled={syncing}
            className="rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150 hover:opacity-90 hover:shadow-lg active:scale-95 disabled:opacity-50 disabled:active:scale-100"
            style={{ background: "var(--signal)", color: "var(--ink)" }}
          >
            {syncing ? "Syncing…" : "Sync & send now"}
          </button>
        </div>
      </div>
      {syncMessage && (
        <div className="mb-2 font-mono text-xs" style={{ color: "var(--text-mid)" }}>{syncMessage}</div>
      )}
      {skippedDetails.length > 0 && (
        <div
          className="mb-3 rounded-lg p-3 font-mono text-xs"
          style={{ background: hexToRgba("#FFB020", 0.1), border: "1px solid rgba(255,176,32,0.3)", color: "#FFB020" }}
        >
          <div className="font-semibold uppercase tracking-wide">Skipped rows (fix in the sheet)</div>
          <ul className="mt-1.5 space-y-1">
            {skippedDetails.map((s, i) => (
              <li key={i}>{s.name} · {s.phone} · sheet: {s.role} — {s.reason}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="fade-slide-in mb-5">
        <PipelinePulse candidates={candidates} />
      </div>

      <div className="mb-5 inline-flex flex-wrap gap-1 rounded-full p-1" style={{ background: "var(--surface)" }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className="rounded-full px-4 py-1.5 text-sm font-medium transition-all duration-200 active:scale-90"
            style={
              tab === t.key
                ? { background: "var(--signal)", color: "#ffffff" }
                : { color: "var(--text-mid)" }
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div key={tab} className="fade-slide-in">
      {tab === "overview" && <OverviewView candidates={candidates} />}
      {tab === "pipeline" && (
        <PipelineBoardView candidates={candidates} justMoved={justMoved} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "roles" && (
        <RoleSummaryView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "recruiters" && (
        <RecruiterSummaryView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "messages" && (
        <MessagesView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "no-whatsapp" && (
        <NoWhatsappView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "add" && <AddCandidatesView addCandidates={addCandidates} />}
      {tab === "settings" && <SettingsView candidates={candidates} />}

      </div>

      <CandidateDetailModal candidate={selectedCandidate} onClose={() => setSelectedId(null)} onMarkReviewed={markReviewed} />
    </div>
  );
}