from dotenv import load_dotenv
load_dotenv()

from agent.llm_layer import CONFIDENCE_THRESHOLD, process_message


# Simulated prior conversation turns — candidate is already engaged.
# This reflects real usage where history is always passed in.
ENGAGED_HISTORY = [
    {
        "role": "user",
        "content": "Hi Usha, yes I am actively looking for a new role."
    },
    {
        "role": "assistant",
        "content": '{"reply": "Good to hear. Could you tell me about your current role and how many years of experience you have?", "status": "replied", "confidence": 88, "escalate": false}'
    },
    {
        "role": "user",
        "content": "I am a Senior Data Engineer with 6 years of experience."
    },
    {
        "role": "assistant",
        "content": '{"reply": "Got it, thanks. What is your current notice period or earliest start date?", "status": "replied", "confidence": 88, "escalate": false}'
    },
    {
        "role": "user",
        "content": "My notice period is 30 days."
    },
    {
        "role": "assistant",
        "content": '{"reply": "Perfect. We have a Senior Data Engineer role at Amerisource focused on data pipelines and cloud infrastructure. Does that sound like something you would want to explore further?", "status": "replied", "confidence": 88, "escalate": false}'
    },
    {
        "role": "user",
        "content": "Yes that sounds interesting, tell me more."
    },
    {
        "role": "assistant",
        "content": '{"reply": "Great. Would Tuesday 11am or Wednesday 3pm work for a quick screening call?", "status": "replied", "confidence": 88, "escalate": false}'
    },
]

SAMPLES = [
    # ROLE_DETAILS
    ("ROLE_DETAILS",    "Could you please tell me more about this role?"),
    ("ROLE_DETAILS",    "What are the primary responsibilities of this position?"),
    ("ROLE_DETAILS",    "Why is this position open?"),
    ("ROLE_DETAILS",    "Which team would I be joining, and who would I report to?"),
    ("ROLE_DETAILS",    "What skills or experience are you looking for in the ideal candidate?"),
    ("ROLE_DETAILS",    "Where is the job located? Is it remote, hybrid, or onsite?"),
    ("ROLE_DETAILS",    "What are the working hours or shift timings?"),

    # OFF_LIMITS
    ("OFF_LIMITS",      "What is the salary range or compensation package for this role?"),
    ("OFF_LIMITS",      "Are there any additional benefits (health insurance, bonuses, PTO, etc.)?"),
    ("OFF_LIMITS",      "Is visa sponsorship or work authorization support available?"),

    # SCHEDULING
    ("SCHEDULING",      "What is the expected joining date?"),
    ("SCHEDULING",      "Is there any flexibility regarding the notice period?"),
    ("SCHEDULING",      "What interview dates and time slots are available?"),

    # INTERVIEW
    ("INTERVIEW",       "What does the interview process look like?"),
    ("INTERVIEW",       "How many interview rounds are there?"),
    ("INTERVIEW",       "Will there be any technical assessment or coding test?"),
    ("INTERVIEW",       "Who will be interviewing me?"),
    ("INTERVIEW",       "How should I prepare for the interview?"),
    ("INTERVIEW",       "When can I expect feedback after the interview?"),
    ("ROLE_DETAILS",    "Could you share the job description or any additional details about the role?"),
]

print(f"\n{'='*60}")
print(f"  CANDIDATE QUESTIONS RUN  |  Threshold: {CONFIDENCE_THRESHOLD}")
print(f"{'='*60}\n")

for i, (category, message) in enumerate(SAMPLES, 1):
    print(f"[{i:02d}] {category}")
    print(f"     MSG       : {message}")

    try:
        result = process_message(
            candidate_message=message,
            conversation_history=ENGAGED_HISTORY,
        )
        print(f"     REPLY     : {result.reply}")
        print(f"     STATUS    : {result.status.value}")
        print(f"     CONFIDENCE: {result.confidence}")
        print(f"     ESCALATED : {result.escalated}")
        if result.escalated:
            print(f"     REASON    : {result.reason}")
    except Exception as e:
        print(f"     ERROR     : {e}")

    print()

print(f"{'='*60}")
print(f"  Done. Review the scores above and ask:")
print(f"    - Is Usha no longer asking 'are you still looking?' mid-conversation?")
print(f"    - Do OFF_LIMITS questions escalate via the flag?")
print(f"    - Do ROLE_DETAILS questions get a natural brief answer?")
print(f"    - Do INTERVIEW questions get redirected to the call?")
print(f"    - Do SCHEDULING questions move toward booking a slot?")
print(f"{'='*60}\n")