import { useState, useRef, useEffect } from "react";
import { AlertTriangle, ArrowLeft, Send, Flag, Mail, Phone, MapPin, Briefcase, Clock } from "lucide-react";
import "./App.css";

// ---- Mock data (swap for real data from your backend) ----------------------

const candidate = {
  name: "Priya Nair",
  role: "Senior Product Designer",
  requisition: "REQ-2291",
  email: "priya.nair@example.com",
  phone: "+91 98765 43210",
  location: "Hyderabad, IN",
  stage: "Onsite scheduling",
  avatarInitials: "PN",
};

const escalation = {
  reason:
    "Candidate disputed the salary band communicated by the sourcing assistant and requested written confirmation before proceeding. Automated assistant could not resolve — flagged for compensation policy exception.",
  triggeredAt: "Jul 6, 2026 · 4:12 PM",
  triggeredBy: "Screening Assistant",
  severity: "Needs response today",
};

const initialMessages = [
  {
    id: "m1",
    sender: "candidate",
    author: "Priya Nair",
    text: "Hi, following up on the Senior Product Designer role — can you confirm the next steps after my phone screen?",
    time: "Jul 5, 10:02 AM",
  },
  {
    id: "m2",
    sender: "assistant",
    author: "Screening Assistant",
    text: "Thanks for following up, Priya! You're moving to the onsite round. The recruiting team will send a scheduling link shortly. For this level, the target band is ₹28–32L fixed.",
    time: "Jul 5, 10:05 AM",
  },
  {
    id: "m3",
    sender: "candidate",
    author: "Priya Nair",
    text: "That's lower than what was discussed in my initial call with your recruiter — I was told ₹34–38L for this level. Can someone confirm which figure is accurate before I schedule the onsite?",
    time: "Jul 6, 4:09 PM",
  },
  {
    id: "m4",
    sender: "assistant",
    author: "Screening Assistant",
    text: "I'm not able to confirm compensation exceptions — I've flagged this conversation for a member of the HR team to follow up with the correct figure.",
    time: "Jul 6, 4:12 PM",
    escalationTrigger: true,
  },
];

// ---- Small building blocks ---------------------------------------------------

function Avatar({ initials, tone = "candidate" }) {
  const bg = tone === "candidate" ? "var(--accent-teal)" : "var(--accent-navy)";
  return (
    <div
      style={{
        width: 34,
        height: 34,
        borderRadius: 8,
        background: bg,
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 13,
        fontWeight: 600,
        flexShrink: 0,
        letterSpacing: 0.2,
      }}
    >
      {initials}
    </div>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "8px 0" }}>
      <Icon size={15} style={{ color: "var(--text-tertiary)", marginTop: 2, flexShrink: 0 }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: 0.4 }}>
          {label}
        </div>
        <div style={{ fontSize: 13.5, color: "var(--text-primary)", fontWeight: 500, wordBreak: "break-word" }}>
          {value}
        </div>
      </div>
    </div>
  );
}

// ---- Main component -----------------------------------------------------------

function App() {
  const [messages, setMessages] = useState(initialMessages);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const threadEndRef = useRef(null);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = () => {
    const trimmed = reply.trim();
    if (!trimmed) return;
    setSending(true);
    const newMessage = {
      id: `m${messages.length + 1}`,
      sender: "hr",
      author: "You (HR)",
      text: trimmed,
      time: "Just now",
    };
    setTimeout(() => {
      setMessages((prev) => [...prev, newMessage]);
      setReply("");
      setSending(false);
    }, 250);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      style={{
        "--bg-page": "#F5F6F8",
        "--bg-panel": "#FFFFFF",
        "--border": "#E3E6EB",
        "--text-primary": "#1B222C",
        "--text-secondary": "#565F6D",
        "--text-tertiary": "#8992A0",
        "--accent-navy": "#2C3B52",
        "--accent-teal": "#1F7A6C",
        "--accent-escalation": "#A33B2E",
        "--accent-escalation-bg": "#FBEEEC",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        background: "var(--bg-page)",
        color: "var(--text-primary)",
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 24px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-panel)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "none",
              border: "none",
              color: "var(--text-secondary)",
              fontSize: 13.5,
              cursor: "pointer",
              padding: "6px 8px",
              borderRadius: 6,
            }}
          >
            <ArrowLeft size={16} />
            Escalation queue
          </button>
          <div style={{ width: 1, height: 20, background: "var(--border)" }} />
          <span style={{ fontSize: 13.5, color: "var(--text-tertiary)" }}>
            Conversation · REQ-2291
          </span>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--accent-escalation)",
            background: "var(--accent-escalation-bg)",
            padding: "5px 10px",
            borderRadius: 999,
          }}
        >
          <Flag size={13} />
          Escalated
        </div>
      </div>

      {/* Body */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* Sidebar: candidate + escalation */}
        <aside
          style={{
            width: 300,
            flexShrink: 0,
            borderRight: "1px solid var(--border)",
            background: "var(--bg-panel)",
            padding: "22px 20px",
            overflowY: "auto",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 10,
                background: "var(--accent-navy)",
                color: "#fff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 16,
                fontWeight: 600,
              }}
            >
              {candidate.avatarInitials}
            </div>
            <div>
              <div style={{ fontSize: 15.5, fontWeight: 600 }}>{candidate.name}</div>
              <div style={{ fontSize: 12.5, color: "var(--text-tertiary)" }}>{candidate.stage}</div>
            </div>
          </div>

          <div style={{ borderTop: "1px solid var(--border)" }}>
            <InfoRow icon={Briefcase} label="Role" value={`${candidate.role} · ${candidate.requisition}`} />
            <InfoRow icon={Mail} label="Email" value={candidate.email} />
            <InfoRow icon={Phone} label="Phone" value={candidate.phone} />
            <InfoRow icon={MapPin} label="Location" value={candidate.location} />
          </div>

          {/* Escalation reason card */}
          <div
            style={{
              marginTop: 20,
              border: "1px solid #E9C7C1",
              background: "var(--accent-escalation-bg)",
              borderRadius: 10,
              padding: "14px 14px 12px",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                color: "var(--accent-escalation)",
                fontSize: 12.5,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 0.3,
                marginBottom: 8,
              }}
            >
              <AlertTriangle size={14} />
              Why this was escalated
            </div>
            <p style={{ fontSize: 13.5, lineHeight: 1.5, color: "var(--text-primary)", margin: 0 }}>
              {escalation.reason}
            </p>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginTop: 10,
                fontSize: 11.5,
                color: "var(--text-secondary)",
              }}
            >
              <Clock size={12} />
              {escalation.triggeredAt} · by {escalation.triggeredBy}
            </div>
            <div
              style={{
                marginTop: 8,
                fontSize: 11.5,
                fontWeight: 600,
                color: "var(--accent-escalation)",
              }}
            >
              {escalation.severity}
            </div>
          </div>
        </aside>

        {/* Conversation thread */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div style={{ flex: 1, overflowY: "auto", padding: "22px 28px" }}>
            <div style={{ maxWidth: 640, margin: "0 auto" }}>
              {messages.map((m) => {
                const isCandidate = m.sender === "candidate";
                const isHr = m.sender === "hr";
                return (
                  <div key={m.id}>
                    {m.escalationTrigger && (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          margin: "18px 0",
                          color: "var(--accent-escalation)",
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      >
                        <div style={{ flex: 1, height: 1, background: "#E9C7C1" }} />
                        <Flag size={12} />
                        Escalation point
                        <div style={{ flex: 1, height: 1, background: "#E9C7C1" }} />
                      </div>
                    )}
                    <div
                      style={{
                        display: "flex",
                        gap: 10,
                        flexDirection: isCandidate ? "row" : "row-reverse",
                        marginBottom: 16,
                      }}
                    >
                      <Avatar
                        initials={isCandidate ? candidate.avatarInitials : isHr ? "HR" : "SA"}
                        tone={isCandidate ? "candidate" : "other"}
                      />
                      <div style={{ maxWidth: "78%" }}>
                        <div
                          style={{
                            display: "flex",
                            gap: 8,
                            alignItems: "baseline",
                            flexDirection: isCandidate ? "row" : "row-reverse",
                            marginBottom: 4,
                          }}
                        >
                          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{m.author}</span>
                          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{m.time}</span>
                        </div>
                        <div
                          style={{
                            background: isCandidate
                              ? "var(--bg-panel)"
                              : isHr
                              ? "var(--accent-teal)"
                              : "#EEF1F5",
                            color: isHr ? "#fff" : "var(--text-primary)",
                            border: isCandidate ? "1px solid var(--border)" : "none",
                            borderRadius: 12,
                            borderTopLeftRadius: isCandidate ? 4 : 12,
                            borderTopRightRadius: isCandidate ? 12 : 4,
                            padding: "10px 13px",
                            fontSize: 13.5,
                            lineHeight: 1.5,
                          }}
                        >
                          {m.text}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
              <div ref={threadEndRef} />
            </div>
          </div>

          {/* Manual reply interface */}
          <div
            style={{
              borderTop: "1px solid var(--border)",
              background: "var(--bg-panel)",
              padding: "14px 28px 18px",
            }}
          >
            <div style={{ maxWidth: 640, margin: "0 auto" }}>
              <div
                style={{
                  fontSize: 11.5,
                  color: "var(--text-tertiary)",
                  marginBottom: 6,
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span>Replying to {candidate.name} as HR — the candidate sees this as coming from your team.</span>
              </div>
              <div
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  overflow: "hidden",
                  background: "var(--bg-page)",
                }}
              >
                <textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Type your reply to the candidate…"
                  rows={3}
                  style={{
                    width: "100%",
                    border: "none",
                    outline: "none",
                    resize: "vertical",
                    background: "transparent",
                    padding: "12px 14px",
                    fontSize: 13.5,
                    fontFamily: "inherit",
                    color: "var(--text-primary)",
                    boxSizing: "border-box",
                  }}
                />
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "8px 12px",
                    borderTop: "1px solid var(--border)",
                  }}
                >
                  <span style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>⌘/Ctrl + Enter to send</span>
                  <button
                    onClick={handleSend}
                    disabled={!reply.trim() || sending}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 7,
                      background: reply.trim() ? "var(--accent-teal)" : "#C7CCD3",
                      color: "#fff",
                      border: "none",
                      borderRadius: 7,
                      padding: "8px 14px",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: reply.trim() ? "pointer" : "not-allowed",
                      transition: "background 0.15s ease",
                    }}
                  >
                    <Send size={13} />
                    {sending ? "Sending…" : "Send reply"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;