/* Versions of the design through time, so the same screen can be compared
 * across them (the review bar's v1 / v2 / … switch and compare.html).
 * To add one: put its pages in versions/<id>/, add an entry to `list` with
 * the screens it has, and link it from index.html. Screens share a key
 * across versions; a version may lack a screen or add new ones. */
(function () {
  "use strict";
  var WGSM = (window.WGSM = window.WGSM || {});

  var SCREENS = [
    ["home", "Library (home)"],
    ["person", "Genome / person"],
    ["person-alex", "Person with a problem"],
    ["person-gordon", "Person with a job running"],
    ["person-helen", "Person with only a VCF"],
    ["add", "Add files"],
    ["dna-test", "DNA-test files"],
    ["result", "Finished job"],
    ["ancestry", "Ancestry & lineage"],
    ["ancestry-alex", "Ancestry from a kit"],
    ["convert", "Convert"],
    ["jobs", "Jobs"],
    ["tools", "All tools"],
    ["tool", "One tool (Call variants)"],
    ["references", "Reference data"],
    ["settings", "Settings"],
    ["install", "Install tools"],
    ["phone", "Phone access"],
    ["setup", "First-run setup"],
    ["states", "Empty, loading, errors"],
  ];

  var LIST = [
    {
      id: "v1",
      name: "Genome hub",
      date: "2026-11-11",
      base: "directions/c/",
      summary: "Direction C of the first exploration: a file library as home, task pages in the sidebar, a global Basic / Advanced switch.",
      screens: {
        home: "home.html",
        person: "file.html",
        add: "add-file.html",
        "dna-test": "microarray.html",
        ancestry: "ancestry.html",
        convert: "convert.html",
        jobs: "jobs.html",
        tools: "advanced.html?depth=advanced",
        references: "references.html",
        settings: "settings.html",
        phone: "remote.html",
        setup: "setup.html",
        states: "states.html",
      },
    },
    {
      id: "v2",
      name: "People first",
      date: "2026-11-16",
      base: "versions/v2/",
      summary: "People with their files underneath; every task starts from a person; results belong to the person; no global Advanced mode; a finished-job page; a new visual system.",
      screens: {
        home: "home.html",
        person: "person.html",
        "person-alex": "person-alex.html",
        "person-gordon": "person-gordon.html",
        "person-helen": "person-helen.html",
        add: "add.html",
        "dna-test": "dna-test.html",
        result: "result.html",
        ancestry: "ancestry.html",
        "ancestry-alex": "ancestry-alex.html",
        convert: "convert.html",
        jobs: "jobs.html",
        tools: "tools.html",
        tool: "tool-call-variants.html",
        references: "references.html",
        settings: "settings.html",
        install: "install.html",
        phone: "phone.html",
        setup: "setup.html",
        states: "states.html",
      },
    },
  ];

  function file(path) {
    return path.split("?")[0];
  }

  WGSM.versions = {
    list: LIST,
    screens: SCREENS,
    get: function (id) {
      return LIST.find(function (v) {
        return v.id === id;
      });
    },
    label: function (screen) {
      var found = SCREENS.find(function (s) {
        return s[0] === screen;
      });
      return found ? found[1] : screen;
    },
    /* Path of a screen in a version, relative to the mockups root, or null */
    path: function (id, screen) {
      var v = this.get(id);
      return v && v.screens[screen] ? v.base + v.screens[screen] : null;
    },
    /* Which version and screen a URL path shows, or null */
    locate: function (pathname) {
      var found = null;
      LIST.forEach(function (v) {
        Object.keys(v.screens).forEach(function (screen) {
          var tail = "/" + v.base + file(v.screens[screen]);
          if (pathname.slice(-tail.length) === tail) found = { version: v.id, screen: screen };
        });
      });
      return found;
    },
  };
})();
