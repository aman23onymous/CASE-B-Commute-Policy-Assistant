# 2-Minute Screen Recording Script & Walkthrough

> **Application:** Commute Policy Assistant  
> **Target Duration:** Exactly 2 minutes (120 seconds)  
> **Reference Video Flow:** Case Study B Demonstration  

---

## Recording Checklist Before Starting
- [ ] Application running locally on `http://localhost:8000` (via Docker Compose or local Uvicorn).
- [ ] Terminal window open alongside browser with test and evaluation commands ready.
- [ ] Browser window open at `http://localhost:8000` showing the Clean Demo UI.

---

## Timeline & Narration Script

### [0:00 – 0:15] Overview & Single-Sign-On
* **Action:**
  1. Show the Commute Policy Assistant UI in browser.
  2. Click the quick login button for **"Aurora Employee"** (`aurora_employee` / `demo123`).
* **Narration:**
  > "Welcome to the Commute Policy Assistant, a multi-tenant retrieval-augmented assistant designed with strict tenant isolation, current-version consistency, and atomic ingestion. I'll sign in as an authenticated employee of Aurora Healthcare. The UI obtains a signed HS256 JWT, fixing my company context securely on the server."

---

### [0:15 – 0:35] Grounded Question Answering & Exact Citations
* **Action:**
  1. Click the prompt chip **"Escort Rules"** ("When is a security escort needed for a woman's drop?").
  2. Press Submit.
  3. Expand the citation card showing: Document title, Version 1, Section 2 ("Night Shift Drops"), and excerpt.
* **Narration:**
  > "I ask when a security escort is required. Within milliseconds, the system returns a grounded factual answer: an escort is mandatory strictly after 20:00. Notice the verifiable citation pointing to Section 2 of Aurora's active Version 1 policy, with an exact source excerpt. Server-side latency is tracked with a single bounded model call."

---

### [0:35 – 0:50] Uncovered Question & Transport Desk Referral
* **Action:**
  1. Click the prompt chip **"Pet Travel (Uncovered)"** ("Can I bring my pet dog in the commute cab?").
  2. Press Submit.
* **Narration:**
  > "Next, I ask an unanswerable question about pet transport. Instead of hallucinating, the assistant identifies that this topic is not covered in the active policy, returns zero citations, and refers the employee directly to their transport desk without wasting LLM budget."

---

### [0:50 – 1:05] Cross-Tenant Isolation Trap
* **Action:**
  1. Click **"Cross-Tenant Trap"** ("What does BluePeak say about escort after 10 PM?").
  2. Press Submit.
* **Narration:**
  > "Now, I attempt a cross-tenant trap: asking about BluePeak's policy while authenticated as an Aurora employee. The server strictly binds retrieval to Aurora's Chroma collection. BluePeak's policy is never retrieved, never cited, and never leaked. We receive a clean not-covered referral."

---

### [1:05 – 1:30] Admin Policy Ingestion & Atomic v2 Cutover
* **Action:**
  1. Click **"Sign Out"**, then click **"Aurora Admin"** (`aurora_admin` / `demo123`).
  2. Admin panel appears. Enter Version `2`, Title `Aurora Employee Transport Policy`, choose `policies/aurora_v2.md`, and click **"Ingest & Activate"**.
  3. Show success banner ("Policy v2 activated with 8 chunks").
  4. Ask: "Can I book a cab this Saturday?"
* **Narration:**
  > "I switch to the Aurora Admin account. I upload Aurora's new Version 2 policy, where Saturday cab rides are changed from manager-approved to completely suspended. Ingestion creates an isolated staging collection, verifies chunks, and atomically activates Version 2 via a SQLite compare-and-swap transaction. Immediately re-asking about Saturday cabs returns the new rule: weekend cabs are not offered, citing Version 2. Version 1 is cleanly excluded."

---

### [1:30 – 1:50] Automated Testing & Fixed JSONL Evaluation
* **Action:**
  1. Switch to terminal window.
  2. Run `pytest -q` (all 36 unit/integration/isolation tests passing).
  3. Run `python -m scripts.evaluate --mode baseline --provider fake`.
* **Narration:**
  > "In the terminal, our 36 automated unit and isolation tests pass completely offline. We run our fixed 20-case evaluation suite: 100% pass rate, 100% citation validity, and zero tenant or version leaks across all test traps."

---

### [1:50 – 2:00] Wrap-up, Retrieval Comparison & Trade-off
* **Action:**
  1. Highlight evaluation summary table showing Baseline vs Hybrid mode.
* **Narration:**
  > "Our hybrid dense-lexical ranker reduced p95 retrieval latency by 30% over pure vector search. Architectural trade-off: an authoritative SQLite pointer snapshot guarantees zero reader downtime without distributed transactions. Real LLM latency is well within our 3-second budget. Thank you!"
