/*
 * Mock content for the Knowledge Assistant mockup.
 * All content is generic sample data — no customer references.
 * Scenarios are described declaratively as "turn scripts" that mock-api.js converts
 * into real AG-UI event sequences.
 */
(function () {
  "use strict";

  const CHUNKS = {
    expensePolicy: [
      {
        document_title: "Travel and Expense Policy",
        category: "policies",
        page_number: 3,
        content: "Expense reports must be submitted within 30 days of the expense date through the Expenses portal.",
        permalink: "https://docs.example.com/policies/travel-expense#page=3",
      },
      {
        document_title: "Travel and Expense Policy",
        category: "policies",
        page_number: 7,
        content: "Receipts are mandatory for any single expense above 25 EUR. Scanned copies are accepted.",
        permalink: "https://docs.example.com/policies/travel-expense#page=7",
      },
      {
        document_title: "Expenses Portal — User Guide",
        category: "procedures",
        page_number: 2,
        content: "Open the portal, select New report, attach the receipts and choose the cost center.",
        permalink: "https://docs.example.com/procedures/expenses-portal#page=2",
      },
      {
        document_title: "Expenses Portal — User Guide",
        category: "procedures",
        page_number: 5,
        content: "Reports are approved by the line manager; reimbursement follows in the next payroll cycle.",
        permalink: "https://docs.example.com/procedures/expenses-portal#page=5",
      },
      {
        document_title: "Finance FAQ",
        category: "faq",
        page_number: 1,
        content: "Rejected reports can be edited and resubmitted from the Drafts section.",
        permalink: "https://docs.example.com/faq/finance#page=1",
      },
      {
        document_title: "Travel and Expense Policy",
        category: "policies",
        page_number: 7,
        content: "Lost receipts require a signed declaration approved by the line manager.",
        permalink: "https://docs.example.com/policies/travel-expense#page=7",
      },
    ],
    vpnAccess: [
      {
        document_title: "Remote Access Setup Guide",
        category: "technical-manuals",
        page_number: 4,
        content: "Request VPN access through the Service Portal under Access > Remote access.",
        permalink: "https://docs.example.com/manuals/remote-access#page=4",
      },
      {
        document_title: "Remote Access Setup Guide",
        category: "technical-manuals",
        page_number: 6,
        content: "Install the VPN client, sign in with your corporate account and approve the MFA prompt.",
        permalink: "https://docs.example.com/manuals/remote-access#page=6",
      },
      {
        document_title: "Information Security Standard",
        category: "technical-manuals",
        page_number: 12,
        content: "Remote access is granted only on managed devices with disk encryption enabled.",
        permalink: "https://docs.example.com/manuals/security-standard#page=12",
      },
    ],
  };

  const ANSWERS = {
    expense: [
      "To submit an expense report:",
      "",
      "1. Open the **Expenses portal** and select **New report**.",
      "2. Attach the receipts — they are **mandatory for any expense above 25 EUR** (scanned copies are fine).",
      "3. Choose the correct **cost center** and submit.",
      "",
      "Keep in mind:",
      "",
      "- Reports must be submitted **within 30 days** of the expense date.",
      "- Your line manager approves the report; reimbursement arrives with the **next payroll cycle**.",
      "- If a report is rejected you can edit and resubmit it from the *Drafts* section.",
      "- A lost receipt requires a signed declaration approved by your manager.",
    ].join("\n"),
    vpn: [
      "Here is how to get remote access via VPN:",
      "",
      "1. Open the **Service Portal** and go to `Access > Remote access` to submit the request.",
      "2. Once approved, install the **VPN client** and sign in with your corporate account.",
      "3. Approve the **MFA prompt** on your phone.",
      "",
      "> Note: remote access is granted only on **managed devices** with disk encryption enabled.",
    ].join("\n"),
    generic: [
      "Based on the documentation I found, the general rule is:",
      "",
      "- Requests of this kind are handled through the **Service Portal**.",
      "- Approval is given by your **line manager**.",
      "- Supporting documents must be attached when the request is submitted.",
      "",
      "If you need the exact procedure, open the sources below.",
    ].join("\n"),
    chitChat:
      "Hi! 👋 I'm your Knowledge Assistant. I can answer questions about internal **policies**, **procedures**, **technical manuals** and **FAQs**, always pointing you to the source documents. What would you like to know?",
    outOfScope:
      "I'm sorry, but that's outside what I can help with. I can only answer questions about the internal documentation — policies, procedures, technical manuals and FAQs. Is there anything along those lines I can help you with?",
  };

  /*
   * A turn script is a list of agent iterations. Each iteration may contain interim text
   * (LLM text emitted together with a tool call), a tool call and its result, or the
   * final answer text.
   */
  const SCENARIOS = {
    kbSingle: (question) => {
      const isExpense = /expense|receipt|reimburs/i.test(question);
      return [
        {
          interim: "Let me look that up in the documentation…",
          tool: {
            name: "search_knowledge_base",
            args: { query: isExpense ? "submit expense report procedure" : question, category: isExpense ? "policies" : undefined },
            result: { count: CHUNKS.expensePolicy.length, chunks: CHUNKS.expensePolicy },
          },
        },
        { answer: isExpense ? ANSWERS.expense : ANSWERS.generic },
      ];
    },
    kbRefined: () => [
      {
        interim: "I'll search the procedures for the VPN access request.",
        tool: {
          name: "search_knowledge_base",
          args: { query: "VPN remote access request", category: "procedures" },
          result: {
            count: 0,
            chunks: [],
            hint: "No relevant content found. Try a broader query or a different category filter.",
          },
        },
      },
      {
        interim: "Nothing in the procedures — let me try the technical manuals with a broader query.",
        tool: {
          name: "search_knowledge_base",
          args: { query: "remote access VPN setup", category: "technical-manuals" },
          result: { count: CHUNKS.vpnAccess.length, chunks: CHUNKS.vpnAccess },
        },
      },
      { answer: ANSWERS.vpn },
    ],
    chitChat: () => [
      {
        tool: {
          name: "answer_without_search",
          args: { reason: "chit_chat" },
          rawResult: "No search was performed. Reply briefly and conversationally in English.",
        },
      },
      { answer: ANSWERS.chitChat },
    ],
    outOfScope: () => [
      {
        tool: {
          name: "answer_without_search",
          args: { reason: "out_of_scope" },
          rawResult: "No search was performed. Tell the user this request is outside the supported scope.",
        },
      },
      { answer: ANSWERS.outOfScope },
    ],
    error: () => [
      {
        interim: "Let me search the documentation…",
        tool: {
          name: "search_knowledge_base",
          args: { query: "requested topic" },
          fail: true,
        },
      },
    ],
  };

  function pickScenario(question) {
    const q = question.toLowerCase();
    if (/\berror\b|\bfail/.test(q)) return "error";
    if (/^(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|bye)\b|what can you do/.test(q)) return "chitChat";
    if (/weather|football|recipe|stock price|joke|movie|holiday destination/.test(q)) return "outOfScope";
    if (/vpn|remote access/.test(q)) return "kbRefined";
    return "kbSingle";
  }

  const SUGGESTIONS = [
    { icon: "📄", title: "Expense reports", question: "How do I submit an expense report?", hint: "1 search · sources" },
    { icon: "🔁", title: "Remote access", question: "What is the procedure for requesting VPN remote access?", hint: "Refined search" },
    { icon: "👋", title: "Say hello", question: "Hi! What can you do?", hint: "Chit-chat" },
    { icon: "🌦️", title: "Off topic", question: "What's the weather like in Rome tomorrow?", hint: "Out of scope" },
  ];

  const USERS = {
    admin: { name: "Jane", surname: "Doe", email: "jane.doe@example.com", groups: ["okd_admin", "okd_editor"] },
    editor: { name: "John", surname: "Smith", email: "john.smith@example.com", groups: ["okd_editor"] },
  };

  const SAMPLE_TESTBOOK = [
    "question,expected_answer",
    '"How do I submit an expense report?","Through the Expenses portal within 30 days, with receipts above 25 EUR"',
    '"How do I request VPN access?","Via the Service Portal under Access > Remote access"',
    '"Are receipts mandatory?","Yes, for expenses above 25 EUR"',
    '"Who approves expense reports?","The line manager"',
    '"Can I use VPN on a personal laptop?","No, only managed devices with disk encryption"',
    '"What is the weather tomorrow?","Out of scope"',
  ].join("\n");

  window.MockData = { CHUNKS, ANSWERS, SCENARIOS, SUGGESTIONS, USERS, SAMPLE_TESTBOOK, pickScenario };
})();
