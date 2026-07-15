import { useState, useEffect, useRef, useCallback, useMemo } from "react";
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

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
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
  return {
    id: row.id,
    name: row.name || "Unknown",
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

  return { candidates, connection, lastSyncedAt, refresh: fetchAll, justMoved, addCandidates };
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

function CandidateDetailModal({ candidate, onClose }) {
  if (!candidate) return null;
  const status = STATUS_MAP[candidate.status] || STATUSES[0];

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
function StatCard({ label, value, sublabel, color, icon: Icon }) {
  return (
    <div
      className="group rounded-xl p-4 transition-all duration-300 hover:-translate-y-0.5"
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
      }}
    >
      <div className="font-mono text-xs uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>{label}</div>
      <div className="mt-2 font-display text-2xl font-semibold" style={{ color: color || "var(--text-hi)" }}>
        {value}
      </div>
      {sublabel && <div className="mt-1 text-xs" style={{ color: "var(--text-mid)" }}>{sublabel}</div>}
    </div>
  );
}

function OverviewView({ candidates }) {
  const stats = useMemo(() => {
    const outreachSent = candidates.filter((c) => c.first_outreach_sent_at).length;
    const followup1Sent = candidates.filter((c) => c.followup_1_sent_at).length;
    const followup2Sent = candidates.filter((c) => c.followup_2_sent_at).length;
    const interviewFollowupSent = candidates.filter((c) => c.interview_followup_sent_at).length;
    const totalMessagesSent = outreachSent + followup1Sent + followup2Sent + interviewFollowupSent;

    const engaged = candidates.filter((c) => ENGAGED_STATUSES.has(c.status)).length;
    const neverResponded = outreachSent - engaged > 0 ? outreachSent - engaged : 0;
    const responseRate = outreachSent > 0 ? Math.round((engaged / outreachSent) * 100) : 0;

    return { outreachSent, followup1Sent, followup2Sent, interviewFollowupSent, totalMessagesSent, engaged, neverResponded, responseRate };
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

  const timeline = useMemo(() => {
    const days = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({ key, label: d.toLocaleDateString("en-IN", { weekday: "short" }), outreach: 0, followup: 0 });
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
  }, [candidates]);

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Total messages sent" value={stats.totalMessagesSent} sublabel="outreach + all follow-ups" color="#0891B2" />
        <StatCard label="Outreach only" value={stats.outreachSent} sublabel="initial message" color="#4C8DFF" />
        <StatCard label="Follow-up 1" value={stats.followup1Sent} color="#9B7BFF" />
        <StatCard label="Follow-up 2 (final)" value={stats.followup2Sent} color="#D97706" />
        <StatCard label="Response rate" value={`${stats.responseRate}%`} sublabel={`${stats.engaged} replied · ${stats.neverResponded} silent`} color="#16A34A" />
      </div>

      <div className="mt-5 rounded-xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--line)" }}>
        <div className="font-display text-sm font-semibold" style={{ color: "var(--text-hi)" }}>
          Messages sent, last 7 days
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
              <Bar dataKey="outreach" name="Outreach" fill="#4C8DFF" radius={[3, 3, 0, 0]} />
              <Bar dataKey="followup" name="Follow-ups" fill="#9B7BFF" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
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
              <tr key={r.role} className="border-b last:border-0" style={{ borderColor: "var(--line)" }}>
                <td className="px-3 py-2 font-medium" style={{ color: "var(--text-hi)" }}>{r.role}</td>
                <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.total}</td>
                <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.outreach}</td>
                <td className="px-3 py-2 font-mono" style={{ color: "var(--text-mid)" }}>{r.engaged}</td>
                <td className="px-3 py-2 font-mono font-medium" style={{ color: "#34D399" }}>{r.rate}%</td>
              </tr>
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
  const noWhatsapp = candidates.filter((c) => c.platform !== "whatsapp");

  return (
    <div>
      <div
        className="mb-4 rounded-xl p-4 font-mono text-xs"
        style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
      >
        Candidates whose number couldn't be reached on WhatsApp. Detection for this isn't wired up on the
        backend yet — every synced candidate currently defaults to "whatsapp" as their platform.
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
  { key: "no-whatsapp", label: "No WhatsApp", icon: PhoneOff },
  { key: "add", label: "Add candidates", icon: UserPlus },
];

export default function CandidatePipelineBoard() {
  const { candidates, connection, lastSyncedAt, refresh, justMoved, addCandidates } = usePipelineData();
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
                className="rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-200"
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
            className="rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-white/5"
            style={{ borderColor: "var(--line)", color: "var(--text-hi)" }}
          >
            Refresh
          </button>
          <button
            onClick={triggerSync}
            disabled={syncing}
            className="rounded-md px-3 py-1.5 text-sm font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
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
            className="rounded-full px-4 py-1.5 text-sm font-medium transition-all duration-300"
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
      {tab === "no-whatsapp" && (
        <NoWhatsappView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "add" && <AddCandidatesView addCandidates={addCandidates} />}

      <CandidateDetailModal candidate={selectedCandidate} onClose={() => setSelectedId(null)} />
    </div>
  );
}