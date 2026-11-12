/* Light/dark/system theme. The choice is remembered for all mockup pages;
 * ?theme=dark in the URL overrides it (used for screenshots and links). */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var media = window.matchMedia("(prefers-color-scheme: dark)");

  function resolved(choice) {
    if (choice === "light" || choice === "dark") return choice;
    return media.matches ? "dark" : "light";
  }

  WGSM.theme = {
    choice: WGSM.params.get("theme") || WGSM.pref("theme") || "system",
    apply: function () {
      document.documentElement.dataset.theme = resolved(this.choice);
      WGSM.emit("theme", this.choice);
    },
    set: function (choice) {
      this.choice = choice;
      WGSM.pref("theme", choice);
      this.apply();
    },
    toggle: function () {
      this.set(resolved(this.choice) === "dark" ? "light" : "dark");
    },
    current: function () {
      return resolved(this.choice);
    },
  };

  media.addEventListener("change", function () {
    if (WGSM.theme.choice === "system") WGSM.theme.apply();
  });
  WGSM.theme.apply();
})();
