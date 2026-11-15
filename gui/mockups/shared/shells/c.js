/* Direction C, "Genome hub" (recommended): library-first navigation. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var parts = WGSM.shellParts;
  var C = "directions/c/";

  WGSM.defineShell({
    brand: "WGS Extract",
    logo: "logo-a-helix-bracket.svg",
    home: C + "home.html",
    jobsHref: C + "jobs.html",
    depthToggle: true,
    sidebarJob: true,
    groups: [
      {
        items: [
          { id: "home", label: "My genomes", icon: "library-big", href: C + "home.html", also: ["file", "add-file"] },
          { id: "jobs", label: "Jobs", icon: "jobs", href: C + "jobs.html", badge: "2" },
        ],
      },
      {
        heading: "Do something",
        items: [
          { id: "microarray", label: "DNA-test files", icon: "chip", href: C + "microarray.html" },
          { id: "ancestry", label: "Ancestry & lineage", icon: "haplotree", href: C + "ancestry.html" },
          { id: "convert", label: "Convert", icon: "convert", href: C + "convert.html" },
          { id: "advanced", label: "Advanced tools", icon: "sliders-horizontal", href: C + "advanced.html", adv: true },
        ],
      },
      {
        heading: "Library",
        items: [{ id: "references", label: "Reference genomes", icon: "reference", href: C + "references.html", also: ["setup"] }],
      },
    ],
    footer: [
      { id: "remote", label: "Phone access", icon: "remote", href: C + "remote.html", dot: "success", dotLabel: "On" },
      { id: "settings", label: "Settings", icon: "settings", href: C + "settings.html", also: ["states"] },
    ],
    tabs: [
      { id: "home", label: "Genomes", icon: "library-big", href: C + "home.html", also: ["file", "add-file"] },
      { id: "tasks", label: "Tasks", icon: "layout-grid", href: C + "tasks.html", also: ["microarray", "convert", "advanced"] },
      { id: "jobs", label: "Jobs", icon: "jobs", href: C + "jobs.html", badge: "2" },
      { id: "ancestry", label: "Results", icon: "haplotree", href: C + "ancestry.html" },
      { id: "settings", label: "More", icon: "menu", href: C + "settings.html", also: ["references", "remote", "states", "setup"] },
    ],
    topbar: function () {
      return [parts.search(), parts.jobsRing(C + "jobs.html"), parts.connection()];
    },
  });
})();
