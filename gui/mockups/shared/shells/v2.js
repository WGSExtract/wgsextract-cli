/* Version 2, "People first": the sidebar lists the people in the library;
 * every task starts from one of them. Builds its own shell (not the generic
 * defineShell) because the navigation is data, not a fixed list of pages. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var h = WGSM.h;
  var V = "versions/v2/";

  /* The library by person. Illustrative, like shared/data/genomes.js. */
  var PEOPLE = [
    { id: "reginald", name: "Reginald", note: "son", initials: "RM", state: "ok", href: V + "person.html" },
    { id: "gordon", name: "Gordon", note: "father", initials: "GM", state: "busy", href: V + "person-gordon.html" },
    { id: "helen", name: "Helen", note: "mother", initials: "HM", state: "ok", href: V + "person-helen.html" },
    { id: "alex", name: "Alex", note: "2 files", initials: "AM", state: "attention", href: V + "person-alex.html" },
  ];
  var RUNNING = { title: "Realigning Gordon to hs38", progress: 47, eta: "1 h 52 min left" };
  var PLACES = [
    { id: "jobs", label: "Jobs", icon: "jobs", href: V + "jobs.html", count: "4", also: ["result"] },
    { id: "tools", label: "All tools", icon: "sliders-horizontal", href: V + "tools.html" },
    { id: "references", label: "Reference data", icon: "reference", href: V + "references.html" },
  ];
  var FOOT = [
    { id: "phone", label: "Phone access", icon: "remote", href: V + "phone.html" },
    { id: "settings", label: "Settings", icon: "settings", href: V + "settings.html" },
  ];
  var TABS = [
    { id: "home", label: "Library", icon: "library-big", href: V + "home.html", also: ["add", "p-reginald", "p-gordon", "p-helen", "p-alex"] },
    { id: "jobs", label: "Jobs", icon: "jobs", href: V + "jobs.html", count: "4", also: ["result"] },
    { id: "tools", label: "Tools", icon: "sliders-horizontal", href: V + "tools.html" },
    { id: "settings", label: "More", icon: "menu", href: V + "settings.html", also: ["references", "phone", "states"] },
  ];

  /* All tools: [group, [label, page, server operations it covers]] */
  var CATALOG = [
    ["Align and call", [
      ["Realign", "tools.html", "realign"],
      ["Align FASTQ", "tool-align.html", "align"],
      ["Call SNPs and indels", "tool-call-variants.html", "vcf/snp vcf/indel"],
      ["DeepVariant", "tool-deepvariant.html", "vcf/deepvariant"],
      ["GATK HaplotypeCaller", "tool-gatk.html", "vcf/gatk"],
      ["Structural variants", "tool-structural-variants.html", "vcf/sv"],
      ["Phase variants", "tool-phase.html", "vcf/phase"],
    ]],
    ["Quality", [
      ["Coverage per chromosome", "tool-coverage.html", "info/calculate-coverage"],
      ["Read quality (FastQC)", "tool-read-quality.html", "qc/fastqc qc/fastp"],
      ["Contamination", "tool-contamination.html", "qc/contamination"],
      ["Relatedness", "tool-relatedness.html", "qc/relate"],
      ["VCF quality", "tool-vcf-quality.html", "qc/vcf"],
    ]],
    ["Annotate", [
      ["VEP", "tool-vep.html", "vep/run"],
      ["ClinVar", "tool-clinvar.html", "vcf/clinvar"],
      ["Variant table", "tool-variant-table.html", "vcf/table"],
      ["Pharmacogenomics", "tool-pharmacogenomics.html", "vcf/pharmcat"],
      ["HLA and CYP2D6 typing", "tool-typing.html", "typing/hla typing/cyp2d6"],
    ]],
    ["Extract", [
      ["Region or gene", "tool-region.html", "extract/region extract/custom"],
      ["Mitochondrial DNA", "tool-mtdna.html", "extract/mito-fasta"],
      ["Y chromosome", "tool-y-chromosome.html", "extract/ydna-bam"],
      ["Unmapped reads", "tool-unmapped.html", "extract/unmapped"],
    ]],
    ["Lineage", [
      ["Y haplogroup", "tool-y-haplogroup.html", "lineage/y-haplogroup"],
      ["mtDNA haplogroup", "tool-mt-haplogroup.html", "lineage/mt-haplogroup"],
      ["Y-STR markers", "tool-y-str.html", "lineage/y-str"],
      ["Ancestry proportions", "tool-ancestry-proportions.html", "qc/ancestry"],
    ]],
  ];

  /* The page of the tool that runs a server operation, else All tools */
  function toolPage(op) {
    var page = "tools.html";
    CATALOG.forEach(function (group) {
      group[1].forEach(function (tool) {
        if (tool[2].split(" ").indexOf(op) >= 0) page = tool[1];
      });
    });
    return V + page;
  }

  /* <nav data-render="v2-catalog" data-current="vep">: the All tools list */
  WGSM.render.blocks["v2-catalog"] = function (el, opts) {
    var current = opts.current ? "tool-" + opts.current + ".html" : "tools.html";
    var name = "";
    CATALOG.forEach(function (group) {
      group[1].forEach(function (tool) {
        if (tool[1] === current) name = tool[0];
      });
    });
    /* Narrow screens show only this row until it is opened */
    el.appendChild(h("button.v2-btn.v2-catalog__toggle", { type: "button", "aria-expanded": "false", dataset: { v2CatalogToggle: "" } },
      h("span.v2-grow", h("span.v2-muted", "Tool: "), name), "Change", WGSM.icon("chevron-down", "icon--sm")));
    el.appendChild(h("input.v2-input", { type: "search", placeholder: "Filter tools", "aria-label": "Filter tools", dataset: { v2Filter: "" } }));
    CATALOG.forEach(function (group) {
      el.appendChild(h("h2", group[0]));
      group[1].forEach(function (tool) {
        el.appendChild(h("a", { href: href(V + tool[1]), "aria-current": tool[1] === current ? "page" : null, dataset: { ops: tool[2] } }, tool[0]));
      });
    });
  };

  function filterCatalog(box) {
    var q = box.value.trim().toLowerCase();
    var nav = box.closest(".v2-catalog");
    WGSM.qsa("a", nav).forEach(function (a) {
      a.hidden = !!q && (a.textContent + " " + a.dataset.ops).toLowerCase().indexOf(q) < 0;
    });
    WGSM.qsa("h2", nav).forEach(function (head) {
      var any = false;
      for (var n = head.nextElementSibling; n && n.tagName === "A"; n = n.nextElementSibling) any = any || !n.hidden;
      head.hidden = !any;
    });
  }

  function href(path) {
    return WGSM.keepParams(WGSM.link(path));
  }

  function current(item) {
    var nav = WGSM.page.nav;
    return item.id === nav || (item.also || []).indexOf(nav) >= 0 ? "page" : null;
  }

  function link(item) {
    return h(
      "a.v2-nav",
      { href: href(item.href), "aria-current": current(item), dataset: { annotId: "nav-" + item.id } },
      WGSM.icon(item.icon),
      h("span.v2-nav__text", item.label),
      item.count ? h("span.v2-nav__count", item.count) : null
    );
  }

  function person(p) {
    return h(
      "a.v2-nav",
      { href: href(p.href), "aria-current": WGSM.page.nav === "p-" + p.id ? "page" : null, dataset: { annotId: "nav-" + p.id } },
      h("span.v2-avatar", p.initials),
      h("span.v2-nav__text", p.name, " ", h("span.v2-nav__sub", p.note)),
      p.state !== "ok" ? h("span.v2-pip", { dataset: { state: p.state }, title: p.state === "busy" ? "A job is running" : "Needs attention" }) : null
    );
  }

  function sidebar() {
    return h(
      "aside.v2-side",
      { "aria-label": "Main navigation", dataset: { annotId: "sidebar" } },
      h("a.v2-brand", { href: href(V + "home.html") }, h("img", { src: WGSM.link("icons/logos/logo-a-helix-bracket.svg"), alt: "", width: 26, height: 26 }), "WGS Extract"),
      h("nav", { "aria-label": "Library" },
        link({ id: "home", label: "Library", icon: "library-big", href: V + "home.html" }),
        PEOPLE.map(person),
        link({ id: "add", label: "Add files", icon: "plus", href: V + "add.html" })
      ),
      h("div.v2-side__label", "On this computer"),
      h("nav", { "aria-label": "Work" }, PLACES.map(link)),
      h("div.v2-side__spacer"),
      h(
        "a.v2-sidejob",
        { href: href(V + "jobs.html"), dataset: { annotId: "sidebar-job" } },
        h("strong", RUNNING.title),
        h("div.v2-bar", { role: "progressbar", "aria-valuenow": RUNNING.progress, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": RUNNING.title }, h("span", { style: { "--v": RUNNING.progress + "%" } })),
        h("span", RUNNING.progress + "% · " + RUNNING.eta)
      ),
      h("nav", { "aria-label": "App" }, FOOT.map(link))
    );
  }

  function topbar() {
    var parent = (document.body.dataset.parent || "").split("|");
    return h(
      "header.v2-top",
      { dataset: { annotId: "topbar" } },
      parent[1]
        ? h("a.v2-top__back", { href: href(V + parent[1]) }, WGSM.icon("chevron-left", "icon--sm"), parent[0])
        : h("img.v2-only-phone", { src: WGSM.link("icons/logos/logo-a-helix-bracket.svg"), alt: "WGS Extract", width: 28, height: 28 }),
      h("span.v2-top__grow"),
      h("button.v2-search", { type: "button", "data-palette": "", title: "Search people, files and tools (⌘K)", dataset: { annotId: "search" } },
        WGSM.icon("search", "icon--sm"), h("span.v2-hide-phone", "Search"), h("kbd.v2-hide-phone", "⌘K"), h("span.visually-hidden", "Search")),
      h("span.v2-where.v2-hide-phone", { title: "Connected to the WGS Extract server on this computer", dataset: { annotId: "conn" } }, h("span.v2-pip", { dataset: { state: "ok" } }), "This Mac")
    );
  }

  function tabbar() {
    return h("nav.v2-tabs", { "aria-label": "Main navigation", dataset: { annotId: "tabbar" } }, TABS.map(function (t) {
      return h("a.v2-tab", { href: href(t.href), "aria-current": current(t) }, WGSM.icon(t.icon), h("span", t.label), t.count ? h("span.v2-tab__count", t.count) : null);
    }));
  }

  /* Behaviours this version adds to shared/ui.js:
   *   data-v2-commands             switch: show parameter names and commands
   *   data-v2-count="#form"        text = number of checked boxes in #form
   *   data-v2-need="#form"         disabled while nothing in #form is checked
   *   data-v2-plural="file|files"  word that follows a data-v2-count */
  function commands(on) {
    WGSM.qs(".v2-app").dataset.commands = on ? "on" : "off";
    WGSM.pref("v2-commands", on ? "on" : "off");
    WGSM.qsa("[data-v2-commands]").forEach(function (box) {
      box.checked = on;
    });
  }

  function recount() {
    WGSM.qsa("[data-v2-count]").forEach(function (el) {
      var n = WGSM.qsa("input[type=checkbox]:checked", WGSM.qs(el.dataset.v2Count)).length;
      el.textContent = String(n);
      var word = el.nextElementSibling;
      if (word && word.dataset.v2Plural) word.textContent = word.dataset.v2Plural.split("|")[n === 1 ? 0 : 1];
    });
    WGSM.qsa("[data-v2-need]").forEach(function (button) {
      button.disabled = !WGSM.qs("input[type=checkbox]:checked", WGSM.qs(button.dataset.v2Need));
    });
  }

  WGSM.shell = {
    build: function (nodes) {
      return h("div.v2-app", { dataset: { annotId: "app" } }, sidebar(), topbar(), h("main.v2-main", { id: "main" }, nodes), tabbar());
    },
    palette: function () {
      var people = PEOPLE.map(function (p) {
        return { label: p.name, sub: p.note, icon: "user", group: "People", href: p.href };
      });
      var go = [
        ["Make DNA-test files", "microarray", "chip", "Do", V + "dna-test.html"],
        ["Ancestry & lineage", "lineage, qc/ancestry", "haplotree", "Do", V + "ancestry.html"],
        ["Convert or save space", "bam/to-cram", "convert", "Do", V + "convert.html"],
        ["Add files", "info", "plus", "Do", V + "add.html"],
        ["Jobs", "", "jobs", "Go to", V + "jobs.html"],
        ["Reference data", "ref/library", "reference", "Go to", V + "references.html"],
        ["Settings", "", "settings", "Go to", V + "settings.html"],
      ].map(function (o) {
        return { label: o[0], sub: o[1], icon: o[2], group: o[3], href: o[4] };
      });
      var tools = WGSM.data.ops.map(function (o) {
        return { label: o[0], sub: o[1], icon: o[2], group: "Tools · " + o[3], href: toolPage(o[1]) };
      });
      return people.concat(go, tools);
    },
  };

  WGSM.page.onReady = function () {
    commands((WGSM.params.get("commands") || WGSM.pref("v2-commands")) === "on");
    document.addEventListener("change", function (event) {
      if (event.target.matches("[data-v2-commands]")) commands(event.target.checked);
      recount();
    });
    document.addEventListener("click", function (event) {
      var toggle = event.target.closest("[data-v2-catalog-toggle]");
      if (!toggle) return;
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.parentElement.toggleAttribute("data-open", open);
    });
    document.addEventListener("input", function (event) {
      if (event.target.matches("[data-v2-filter]")) filterCatalog(event.target);
    });
    recount();
  };
})();
