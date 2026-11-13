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
