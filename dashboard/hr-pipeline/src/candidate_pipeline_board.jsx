import { useState, useEffect, useRef, useCallback } from "react";
import Papa from "papaparse";
import { supabase } from "./supabaseClient";

// ---------------------------------------------------------------------------
// STATUS CONFIG
// This is the single source of truth for columns. Must stay in sync with
// app/status.py's VALID_STATUSES on the backend -- if a status exists there
// but not here, candidates in that status fall back to "Pending" below via
// normalizeStatus(), which will look wrong on the board even though the DB
// data is correct.
// ---------------------------------------------------------------------------
const STATUSES = [
  { key: "pending",              label: "Pending",              dot: "bg-slate-400",   border: "border-slate-300",  chip: "bg-slate-100 text-slate-700" },
  { key: "messaged",             label: "Messaged",             dot: "bg-blue-400",    border: "border-blue-300",   chip: "bg-blue-50 text-blue-700" },
  { key: "follow_up_sent",       label: "Follow-up sent",       dot: "bg-indigo-400",  border: "border-indigo-300", chip: "bg-indigo-50 text-indigo-700" },
  { key: "final_reminder_sent",  label: "Final reminder sent",  dot: "bg-fuchsia-400", border: "border-fuchsia-300",chip: "bg-fuchsia-50 text-fuchsia-700" },
  { key: "replied",              label: "Replied",              dot: "bg-cyan-400",    border: "border-cyan-300",   chip: "bg-cyan-50 text-cyan-700" },
  { key: "awaiting_interview",   label: "Awaiting interview",   dot: "bg-teal-400",    border: "border-teal-300",   chip: "bg-teal-50 text-teal-700" },
  { key: "qualified",            label: "Qualified",            dot: "bg-violet-400",  border: "border-violet-300", chip: "bg-violet-50 text-violet-700" },
  { key: "escalated",            label: "Escalated",            dot: "bg-amber-400",   border: "border-amber-300",  chip: "bg-amber-50 text-amber-700" },
  { key: "booked",               label: "Booked",               dot: "bg-emerald-400", border: "border-emerald-300",chip: "bg-emerald-50 text-emerald-700" },
  { key: "declined",             label: "Declined",             dot: "bg-rose-400",    border: "border-rose-300",   chip: "bg-rose-50 text-rose-700" },
];

const STATUS_KEYS = STATUSES.map((s) => s.key);

const SOURCES_FALLBACK = ["LinkedIn", "Referral", "Careers page", "Indeed", "Sourced"];

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
                1600
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
// SHARED UI
// ---------------------------------------------------------------------------
function ConnectionBadge({ connection, lastSyncedAt }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const label =
    connection === "syncing" ? "Syncing…" : connection === "offline" ? "Offline" : "Live";
  const dotColor =
    connection === "syncing" ? "bg-amber-400" : connection === "offline" ? "bg-rose-400" : "bg-emerald-500";

  return (
    <div className="flex items-center gap-2 text-sm text-gray-500">
      <span className="relative flex h-2 w-2">
        {connection === "connected" && (
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${dotColor} opacity-60`} />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${dotColor}`} />
      </span>
      <span className="font-medium text-gray-600">{label}</span>
      <span className="text-gray-400">· synced {timeAgo(lastSyncedAt.toISOString())}</span>
    </div>
  );
}

function CandidateCard({ candidate, highlighted, onOpen }) {
  const status = STATUSES.find((s) => s.key === candidate.status) || STATUSES[0];
  return (
    <button
      type="button"
      onClick={() => onOpen(candidate)}
      className={`w-full rounded-lg border bg-white p-3 text-left shadow-sm transition-all duration-700 hover:shadow-md ${
        highlighted ? "ring-2 ring-offset-1 ring-blue-300 -translate-y-0.5" : ""
      } ${status.border}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-100 text-xs font-medium text-gray-600">
            {initials(candidate.name)}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-gray-900">{candidate.name}</div>
            <div className="truncate text-xs text-gray-500">{candidate.role}</div>
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-gray-400">
        <span className={`rounded px-1.5 py-0.5 ${status.chip}`}>{candidate.source}</span>
        <span>{timeAgo(candidate.last_activity_at)}</span>
      </div>
    </button>
  );
}

function Column({ status, candidates, justMoved, onOpenCandidate }) {
  return (
    <div className="flex w-64 shrink-0 flex-col">
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${status.dot}`} />
          <span className="text-sm font-medium text-gray-700">{status.label}</span>
        </div>
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
          {candidates.length}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 rounded-xl bg-gray-50 p-2 min-h-[120px]">
        {candidates.length === 0 && (
          <div className="flex flex-1 items-center justify-center py-8 text-xs text-gray-400">No candidates</div>
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
  const status = STATUSES.find((s) => s.key === candidate.status) || STATUSES[0];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gray-100 text-sm font-medium text-gray-600">
              {initials(candidate.name)}
            </div>
            <div>
              <div className="text-base font-medium text-gray-900">{candidate.name}</div>
              <div className="text-sm text-gray-500">{candidate.role}</div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          >
            Close
          </button>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${status.chip}`}>{status.label}</span>
          <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">{candidate.source}</span>
          <span className="text-xs text-gray-400">last activity {timeAgo(candidate.last_activity_at)}</span>
        </div>

        <dl className="mt-5 space-y-3 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">Email</dt>
            <dd className="text-right text-gray-700">{candidate.email || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">Phone</dt>
            <dd className="text-right text-gray-700">{candidate.phone || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">Location</dt>
            <dd className="text-right text-gray-700">{candidate.location || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">Current company</dt>
            <dd className="text-right text-gray-700">{candidate.current_company || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">College</dt>
            <dd className="text-right text-gray-700">{candidate.college_name || "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-gray-400">Recruiter</dt>
            <dd className="text-right text-gray-700">{candidate.hiring_recruiter_name || "—"}</dd>
          </div>
          {candidate.interview_score != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-gray-400">Interview score</dt>
              <dd className="text-right text-gray-700">{candidate.interview_score}</dd>
            </div>
          )}
        </dl>

        {candidate.remarks && (
          <div className="mt-5">
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">Remarks</div>
            <p className="mt-1 text-sm leading-relaxed text-gray-600">{candidate.remarks}</p>
          </div>
        )}

        {candidate.overall_feedback && (
          <div className="mt-5">
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">Overall feedback</div>
            <p className="mt-1 text-sm leading-relaxed text-gray-600">{candidate.overall_feedback}</p>
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
    <div className="flex gap-4 overflow-x-auto pb-2">
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
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-gray-900">{role}</div>
          <div className="text-xs text-gray-500">{total} total · {active} active</div>
        </div>
        {escalated > 0 && (
          <span className="rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
            {escalated} escalated
          </span>
        )}
      </div>

      <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-gray-100">
        {STATUSES.map((status) => {
          const count = candidates.filter((c) => c.status === status.key).length;
          if (count === 0 || total === 0) return null;
          const pct = (count / total) * 100;
          return (
            <div
              key={status.key}
              className={status.dot}
              style={{ width: `${pct}%` }}
              title={`${status.label}: ${count}`}
            />
          );
        })}
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
        <span>Top source: {topSource ? `${topSource[0]} (${topSource[1]})` : "—"}</span>
        <span>{booked} booked</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {candidates.slice(0, 6).map((c) => (
          <button
            key={c.id}
            onClick={() => onOpenCandidate(c)}
            className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100 text-[10px] font-medium text-gray-600 hover:bg-gray-200"
            title={c.name}
          >
            {initials(c.name)}
          </button>
        ))}
        {candidates.length > 6 && (
          <span className="flex h-6 items-center px-1 text-[10px] text-gray-400">+{candidates.length - 6}</span>
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
// ADD CANDIDATES VIEW
// ---------------------------------------------------------------------------
function blankRow() {
  return { name: "", email: "", phone: "", role: "", source: SOURCES_FALLBACK[0], status: "pending", notes: "" };
}

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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-gray-300 bg-gray-50 p-4">
        <div>
          <div className="text-sm font-medium text-gray-700">Import from a spreadsheet</div>
          <div className="text-xs text-gray-500">
            CSV columns: name, email, phone, role, source, status, notes
          </div>
        </div>
        <label className="cursor-pointer rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50">
          Upload CSV
          <input ref={fileInputRef} type="file" accept=".csv" className="hidden" onChange={handleCsvUpload} />
        </label>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200">
        <table className="w-full min-w-[900px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
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
              <tr key={idx} className="border-b border-gray-100 last:border-0">
                <td className="px-1 py-1">
                  <input value={row.name} onChange={(e) => updateCell(idx, "name", e.target.value)} placeholder="Jane Doe" className="w-36 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1">
                  <input value={row.email} onChange={(e) => updateCell(idx, "email", e.target.value)} placeholder="jane@example.com" className="w-44 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1">
                  <input value={row.phone} onChange={(e) => updateCell(idx, "phone", e.target.value)} placeholder="9876543210" className="w-36 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1">
                  <input value={row.role} onChange={(e) => updateCell(idx, "role", e.target.value)} placeholder="Backend Engineer" className="w-40 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1">
                  <input value={row.source} onChange={(e) => updateCell(idx, "source", e.target.value)} placeholder="Referral" className="w-32 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1">
                  <select value={row.status} onChange={(e) => updateCell(idx, "status", e.target.value)} className="w-40 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none">
                    {STATUSES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                  </select>
                </td>
                <td className="px-1 py-1">
                  <input value={row.notes} onChange={(e) => updateCell(idx, "notes", e.target.value)} placeholder="Optional note" className="w-44 rounded border border-transparent px-2 py-1 hover:border-gray-200 focus:border-blue-300 focus:outline-none" />
                </td>
                <td className="px-1 py-1 text-right">
                  <button onClick={() => removeRow(idx)} className="px-2 text-xs text-gray-400 hover:text-rose-500">✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <button onClick={addRow} className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-50">
          + Add row
        </button>
        <div className="flex items-center gap-3">
          {message && <span className="text-xs text-gray-500">{message}</span>}
          <button onClick={submitRows} className="rounded-md bg-gray-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-gray-800">
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
  { key: "pipeline", label: "Pipeline board" },
  { key: "roles", label: "By role" },
  { key: "add", label: "Add candidates" },
];

export default function CandidatePipelineBoard() {
  const { candidates, connection, lastSyncedAt, refresh, justMoved, addCandidates } = usePipelineData();
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState("pipeline");

  const selectedCandidate = candidates.find((c) => c.id === selectedId) || null;

  return (
    <div className="w-full">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-medium text-gray-900">Candidate pipeline</h1>
          <p className="text-sm text-gray-500">
            {candidates.length} candidates · live from Supabase · click a card for full details
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ConnectionBadge connection={connection} lastSyncedAt={lastSyncedAt} />
          <button
            onClick={refresh}
            className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="mb-4 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${
              tab === t.key ? "border-gray-900 text-gray-900" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "pipeline" && (
        <PipelineBoardView candidates={candidates} justMoved={justMoved} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "roles" && (
        <RoleSummaryView candidates={candidates} onOpenCandidate={(c) => setSelectedId(c.id)} />
      )}
      {tab === "add" && <AddCandidatesView addCandidates={addCandidates} />}

      <CandidateDetailModal candidate={selectedCandidate} onClose={() => setSelectedId(null)} />
    </div>
  );
}
