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
