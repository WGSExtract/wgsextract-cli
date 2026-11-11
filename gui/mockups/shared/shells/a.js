/* Direction A, "Guided": task-first home and step-by-step flows, with a
 * plain top navigation instead of a sidebar. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var A = "directions/a/";

  WGSM.defineShell({
    layout: "topnav",
    brand: "WGS Extract",
    logo: "logo-b-ring-slice.svg",
    home: A + "home.html",
    jobsHref: "directions/c/jobs.html",
    groups: [
      {
        items: [
          { id: "home", label: "Start", icon: "house", href: A + "home.html" },
          { id: "files", label: "My files", icon: "folder", href: "directions/c/home.html" },
          { id: "microarray", label: "DNA-test files", icon: "chip", href: A + "microarray.html" },
          { id: "results", label: "Results", icon: "haplotree", href: "directions/c/ancestry.html" },
        ],
      },
    ],
    footer: [],
    tabs: [
      { id: "home", label: "Start", icon: "house", href: A + "home.html", also: ["microarray"] },
      { id: "files", label: "Files", icon: "folder", href: "directions/c/home.html" },
      { id: "jobs", label: "Jobs", icon: "jobs", href: "directions/c/jobs.html", badge: "2" },
      { id: "results", label: "Results", icon: "haplotree", href: "directions/c/ancestry.html" },
      { id: "more", label: "Help", icon: "circle-help", href: A + "home.html" },
    ],
    topbar: function () {
      return [WGSM.shellParts.jobsRing("directions/c/jobs.html"), h("button.btn.btn--ghost.btn--sm.hide-phone", { type: "button", "data-toast": "Help: guides for each task" }, WGSM.icon("circle-help"), "Help")];
    },
  });
})();
