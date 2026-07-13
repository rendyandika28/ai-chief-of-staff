// Service worker — satu-satunya yang boleh fetch ke agent (CORS: content script keblok).

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.kind !== "ingest") return;
  (async () => {
    const { agentUrl, apiToken } = await chrome.storage.sync.get(["agentUrl", "apiToken"]);
    if (!agentUrl || !apiToken) {
      sendResponse({ error: "belum di-setup — buka Options extension" });
      return;
    }
    try {
      const r = await fetch(`${agentUrl}/api/jobs/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Token": apiToken },
        body: JSON.stringify({ items: msg.items, auto_draft: !!msg.auto_draft }),
      });
      if (!r.ok) {
        sendResponse({ error: `HTTP ${r.status}${r.status === 401 ? " (token salah?)" : ""}` });
        return;
      }
      const data = await r.json();
      const drafted = data.results.filter((x) => x.drafted).length;
      chrome.action.setBadgeText({ text: String(drafted) });
      chrome.action.setBadgeBackgroundColor({ color: "#6EE7C7" });
      sendResponse({ results: data.results });
    } catch (e) {
      sendResponse({ error: String(e) });
    }
  })();
  return true; // sendResponse async
});
