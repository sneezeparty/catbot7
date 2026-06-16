// HTMX config: error toast on non-200 responses + auto-fade for flash messages.
document.body.addEventListener("htmx:responseError", (evt) => {
  const flash = document.getElementById("flash");
  if (!flash) return;
  const body = evt.detail.xhr.responseText || evt.detail.xhr.statusText || "request failed";
  flash.innerHTML = `<div class="flash flash-err">${evt.detail.xhr.status} — ${body}</div>`;
});

// Auto-clear the flash region after a successful swap into it.
document.body.addEventListener("htmx:afterSwap", (evt) => {
  if (evt.target && evt.target.id === "flash" && evt.target.children.length) {
    setTimeout(() => { evt.target.innerHTML = ""; }, 4000);
  }
});

// Briefly highlight rows that were just saved (server sets just_saved flag).
document.body.addEventListener("htmx:afterSwap", (evt) => {
  if (!evt.target) return;
  if (evt.target.tagName === "TR") {
    evt.target.classList.add("just-saved");
    setTimeout(() => evt.target.classList.remove("just-saved"), 1200);
  }
});

// Pagination links (rendered by partials/_pagination.html into .pager) carry an
// hx-get URL that was baked in when their card was rendered. On a page with
// several independent paginators (e.g. /leaderboards has six), a swap of one
// card only refreshes that card — the other cards' links stay stale and don't
// know about query params added since they were rendered. Merge the current
// URL's params into every pager request so unrelated paginator state isn't
// silently dropped from the address bar.
document.body.addEventListener("htmx:configRequest", (evt) => {
  const elt = evt.detail.elt;
  if (!elt || !elt.closest || !elt.closest(".pager")) return;
  const here = new URL(window.location.href);
  const target = new URL(evt.detail.path, window.location.origin);
  for (const [k, v] of here.searchParams) {
    if (!target.searchParams.has(k)) target.searchParams.set(k, v);
  }
  evt.detail.path = target.pathname + (target.search || "");
});
