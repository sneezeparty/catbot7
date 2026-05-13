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
