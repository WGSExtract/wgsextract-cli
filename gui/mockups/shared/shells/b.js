/* Direction B, "Console": dense dashboard with an icon rail, a command bar
 * and an inspector, for people who know the tools. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var B = "directions/b/";

  WGSM.defineShell({
    layout: "rail",
    brand: "WGS Extract",
    logo: "logo-c-pileup.svg",
    home: B + "home.html",
    jobsHref: "directions/c/jobs.html",
    groups: [
      {
        items: [
          { id: "home", label: "Dashboard", icon: "layout-grid", href: B + "home.html" },
          { id: "files", label: "Samples", icon: "library-big", href: "directions/c/home.html" },
          { id: "microarray", label: "Microarray", icon: "chip", href: B + "microarray.html" },
          { id: "jobs", label: "Jobs", icon: "jobs", href: "directions/c/jobs.html" },
          { id: "refs", label: "References", icon: "reference", href: "directions/c/references.html" },
          { id: "tools", label: "All tools", icon: "terminal", href: "directions/c/advanced.html" },
        ],
      },
    ],
    footer: [{ id: "settings", label: "Settings", icon: "settings", href: "directions/c/settings.html" }],
    tabs: [
      { id: "home", label: "Dashboard", icon: "layout-grid", href: B + "home.html" },
      { id: "microarray", label: "Run", icon: "play", href: B + "microarray.html" },
      { id: "jobs", label: "Jobs", icon: "jobs", href: "directions/c/jobs.html", badge: "2" },
      { id: "settings", label: "More", icon: "menu", href: "directions/c/settings.html" },
    ],
    topbar: function () {
      return [
        WGSM.shellParts.search("Run a command or open a sample…"),
        WGSM.shellParts.jobsRing("directions/c/jobs.html"),
        WGSM.shellParts.connection(),
      ];
    },
  });
})();
