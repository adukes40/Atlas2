// Pop-up menus (<details class="pop">) close on a click outside them or Esc,
// and opening one closes any other.
(function () {
  function closeAll(except) {
    document.querySelectorAll("details.pop[open]").forEach(function (pop) {
      if (!except || !pop.contains(except)) pop.removeAttribute("open");
    });
  }
  document.addEventListener("click", function (event) { closeAll(event.target); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      var open = document.querySelector("details.pop[open]");
      closeAll(null);
      if (open) open.querySelector("summary").focus();
    }
  });
})();

// On "Auto" the page follows the device, so the switch offers the other mode.
(function () {
  var button = document.querySelector(".mode button[data-auto='1']");
  if (button && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    button.value = "light";
    button.setAttribute("aria-label", "Switch to light mode");
    button.title = "Switch to light mode";
  }
})();

// A running sync: fetch its progress every few seconds; reload when it ends
// so the page shows the new data.
(function () {
  function poll() {
    var live = document.querySelector("[data-sync-progress]");
    if (!live) return;
    fetch(live.getAttribute("data-sync-progress"), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (html) {
        var box = document.createElement("div");
        box.innerHTML = html.trim();
        var next = box.firstElementChild;
        if (!next || next.hasAttribute("data-sync-done")) return location.reload();
        live.replaceWith(next);
        setTimeout(poll, 3000);
      })
      .catch(function () { setTimeout(poll, 10000); });
  }
  setTimeout(poll, 3000);
})();
