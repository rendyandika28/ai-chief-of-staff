const $ = (id) => document.getElementById(id);

chrome.storage.sync.get(["agentUrl", "apiToken", "maxAgeMin"], (v) => {
  $("url").value = v.agentUrl || "";
  $("token").value = v.apiToken || "";
  $("maxage").value = v.maxAgeMin ? Math.round(v.maxAgeMin / 60) : "";
});

$("save").onclick = () => {
  const agentUrl = $("url").value.trim().replace(/\/+$/, "");
  const apiToken = $("token").value.trim();
  const maxAgeMin = (parseInt($("maxage").value, 10) || 24) * 60;
  chrome.storage.sync.set({ agentUrl, apiToken, maxAgeMin }, () => {
    $("msg").textContent = "tersimpan ✓";
    setTimeout(() => ($("msg").textContent = ""), 2000);
  });
};
