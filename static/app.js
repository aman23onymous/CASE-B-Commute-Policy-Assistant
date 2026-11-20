(function () {
  "use strict";

  // In-memory token storage (Spec §13 - avoid persistent localStorage for security)
  let currentToken = null;
  let currentPrincipal = null;

  // DOM Elements
  const authSection = document.getElementById("auth-section");
  const workspaceSection = document.getElementById("workspace-section");
  const userStatusBar = document.getElementById("user-status-bar");
  const tenantBadge = document.getElementById("tenant-badge");
  const roleBadge = document.getElementById("role-badge");
  const userDisplay = document.getElementById("user-display");
  const logoutBtn = document.getElementById("logout-btn");

  const loginForm = document.getElementById("login-form");
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  const loginBtn = document.getElementById("login-btn");
  const loginError = document.getElementById("login-error");

  const askForm = document.getElementById("ask-form");
  const questionInput = document.getElementById("question-input");
  const askBtn = document.getElementById("ask-btn");
  const askLoading = document.getElementById("ask-loading");
  const chatHistory = document.getElementById("chat-history");

  const adminPanel = document.getElementById("admin-panel");
  const activePolicyDisplay = document.getElementById("active-policy-display");
  const policyUploadForm = document.getElementById("policy-upload-form");
  const policyVersionInput = document.getElementById("policy-version");
  const policyTitleInput = document.getElementById("policy-title");
  const policyFileInput = document.getElementById("policy-file");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadStatus = document.getElementById("upload-status");

  // Quick login helper
  document.querySelectorAll(".quick-login-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const user = btn.getAttribute("data-user");
      const pass = btn.getAttribute("data-pass");
      usernameInput.value = user;
      passwordInput.value = pass;
      performLogin(user, pass);
    });
  });

  // Example prompts
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-q");
      questionInput.value = q;
      questionInput.focus();
    });
  });

  // Login handler
  loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    performLogin(usernameInput.value.trim(), passwordInput.value);
  });

  async function performLogin(username, password) {
    loginError.classList.add("hidden");
    loginBtn.disabled = true;

    try {
      const resp = await fetch("/api/v1/demo/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Authentication failed");
      }

      const data = await resp.json();
      currentToken = data.access_token;
      currentPrincipal = {
        username: username,
        company_id: data.company_id,
        role: data.role,
        display_name: data.display_name,
      };

      renderAuthenticatedState();
    } catch (err) {
      loginError.textContent = err.message;
      loginError.classList.remove("hidden");
    } finally {
      loginBtn.disabled = false;
    }
  }

  function renderAuthenticatedState() {
    authSection.classList.add("hidden");
    workspaceSection.classList.remove("hidden");
    userStatusBar.classList.remove("hidden");

    tenantBadge.textContent = currentPrincipal.company_id;
    roleBadge.textContent = currentPrincipal.role;
    userDisplay.textContent = currentPrincipal.display_name;

    if (currentPrincipal.role === "admin") {
      adminPanel.classList.remove("hidden");
      workspaceSection.classList.add("with-admin");
      fetchActivePolicy();
    } else {
      adminPanel.classList.add("hidden");
      workspaceSection.classList.remove("with-admin");
    }

    questionInput.focus();
  }

  // Logout handler
  logoutBtn.addEventListener("click", () => {
    currentToken = null;
    currentPrincipal = null;
    chatHistory.innerHTML = "";
    userStatusBar.classList.add("hidden");
    workspaceSection.classList.add("hidden");
    adminPanel.classList.add("hidden");
    authSection.classList.remove("hidden");
    usernameInput.value = "";
    passwordInput.value = "demo123";
  });

  // Question Ask handler
  askForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = questionInput.value.trim();
    if (!question || !currentToken) return;

    askBtn.disabled = true;
    askLoading.classList.remove("hidden");

    try {
      const resp = await fetch("/api/v1/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${currentToken}`,
        },
        body: JSON.stringify({ question: question }),
      });

      const data = await resp.json();
      if (!resp.ok) {
        renderMessageItem(question, {
          status: "retryable_error",
          answer: data.message || "We couldn't check the policy right now. Please try again shortly.",
          citations: [],
          timing_ms: { retrieval: 0, llm: 0, total: 0 },
        });
      } else {
        renderMessageItem(question, data);
      }
      questionInput.value = "";
    } catch (err) {
      renderMessageItem(question, {
        status: "retryable_error",
        answer: "Network error contacting server. Please retry.",
        citations: [],
        timing_ms: { retrieval: 0, llm: 0, total: 0 },
      });
    } finally {
      askBtn.disabled = false;
      askLoading.classList.add("hidden");
      questionInput.focus();
    }
  });

  function renderMessageItem(question, data) {
    const item = document.createElement("div");
    item.className = `message-item status-${data.status}`;

    // Header
    const header = document.createElement("div");
    header.className = "message-header";

    const statusTag = document.createElement("span");
    statusTag.className = `status-tag ${data.status}`;
    statusTag.textContent = data.status === "answered" ? "ANSWERED" : data.status === "not_covered" ? "NOT COVERED" : "TRY AGAIN";
    header.appendChild(statusTag);

    if (data.timing_ms) {
      const timingTag = document.createElement("span");
      timingTag.className = "timing-tag";
      timingTag.textContent = `Total: ${data.timing_ms.total}ms (Retrieval: ${data.timing_ms.retrieval}ms, LLM: ${data.timing_ms.llm}ms)`;
      header.appendChild(timingTag);
    }
    item.appendChild(header);

    // Question
    const qEl = document.createElement("div");
    qEl.className = "message-question";
    qEl.textContent = `Q: ${question}`;
    item.appendChild(qEl);

    // Body
    const bodyEl = document.createElement("div");
    bodyEl.className = "message-body";
    bodyEl.textContent = data.answer;
    item.appendChild(bodyEl);

    // Citations
    if (data.citations && data.citations.length > 0) {
      const citationsCont = document.createElement("div");
      citationsCont.className = "citations-container";

      const heading = document.createElement("div");
      heading.className = "citations-heading";
      heading.textContent = `Policy Citations (${data.citations.length}):`;
      citationsCont.appendChild(heading);

      data.citations.forEach((cit) => {
        const citCard = document.createElement("div");
        citCard.className = "citation-card";

        const citHeader = document.createElement("div");
        citHeader.className = "citation-header";
        citHeader.textContent = `${cit.document_title} (v${cit.version}) — §${cit.section_id} ${cit.section_title}`;
        citCard.appendChild(citHeader);

        if (cit.excerpt) {
          const citExcerpt = document.createElement("div");
          citExcerpt.className = "citation-excerpt";
          citExcerpt.textContent = `"${cit.excerpt}"`;
          citCard.appendChild(citExcerpt);
        }

        citationsCont.appendChild(citCard);
      });

      item.appendChild(citationsCont);
    }

    chatHistory.prepend(item);
  }

  // Admin: Fetch Active Policy
  async function fetchActivePolicy() {
    if (!currentToken || currentPrincipal.role !== "admin") return;
    activePolicyDisplay.textContent = "Loading active policy...";

    try {
      const resp = await fetch("/api/v1/admin/policies/active", {
        headers: { "Authorization": `Bearer ${currentToken}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        activePolicyDisplay.textContent = `${data.title} (Version ${data.version})\nCollection: ${data.collection_name}`;
      } else {
        activePolicyDisplay.textContent = "No active policy registered.";
      }
    } catch (e) {
      activePolicyDisplay.textContent = "Error fetching active policy.";
    }
  }

  // Admin: Ingest Policy
  policyUploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentToken || currentPrincipal.role !== "admin") return;

    const file = policyFileInput.files[0];
    if (!file) return;

    uploadBtn.disabled = true;
    uploadStatus.className = "status-box hidden";

    const formData = new FormData();
    formData.append("version", policyVersionInput.value);
    formData.append("title", policyTitleInput.value.trim());
    formData.append("file", file);

    try {
      const resp = await fetch("/api/v1/admin/policies", {
        method: "POST",
        headers: { "Authorization": `Bearer ${currentToken}` },
        body: formData,
      });

      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || "Upload failed");
      }

      uploadStatus.textContent = `Success! Policy v${data.version} activated with ${data.chunk_count} chunks indexed.`;
      uploadStatus.className = "status-box success";
      policyUploadForm.reset();
      fetchActivePolicy();
    } catch (err) {
      uploadStatus.textContent = `Error: ${err.message}`;
      uploadStatus.className = "status-box error";
    } finally {
      uploadBtn.disabled = false;
    }
  });
})();
