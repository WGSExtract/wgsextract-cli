/* Supplemental-track controls use only the tiny public demo fixtures.
 * User paths are illustrative, never uploads or network lookup keys. */
(function () {
  "use strict";
  var W = window.WGSM, h = W.h;
  var STRINGS = {
    en: {
      limit: "At most 16 optional tracks can be opened. Remove a path and try again; the primary reads do not count toward this limit.",
      index: "The track index is missing. Select the adjacent BAI/CRAI/TBI/CSI index; for BED/bedGraph, sort, BGZF-compress and run tabix -p bed before reopening.",
      build: "This reference or track does not match the genome. Select the exact alignment FASTA and its indexes; chromosome names, lengths and CRAM checksums must match.",
      invalid: "The track data is invalid or belongs to another build. Check chromosome names, interval bounds and finite bedGraph values, and rebuild the index.",
      file: "The file is no longer at its recorded path. Add it again from its new location.",
      genes: "Gene spans are not installed: downloads/genes_hg38.tsv. Run wgsextract ref gene-map explicitly, then reopen. Reads and coordinate navigation still work.",
      bands: "Cytobands are not installed: downloads/cytobands_hg38.tsv. Run wgsextract ref cytobands --build hg38 --download to confirm the public download, then reopen.",
      noref: "The exact hs38 FASTA and indexes are not installed. Reopen Reference genomes and confirm its --download flow. No reference URL from the CRAM header is fetched.",
    },
    de: {
      limit: "Höchstens 16 optionale Spuren. Entfernen Sie einen Pfad und versuchen Sie es erneut; die primären Reads zählen nicht mit.",
      index: "Der Spurindex fehlt. Wählen Sie den passenden BAI/CRAI/TBI/CSI-Index. BED/bedGraph zuerst sortieren, mit BGZF komprimieren und mit tabix -p bed indexieren.",
      build: "Referenz oder Spur passt nicht zum Genom. Wählen Sie die exakte Alignment-FASTA mit Indizes; Chromosomennamen, Längen und CRAM-Prüfsummen müssen übereinstimmen.",
      invalid: "Ungültige Spurdaten oder anderer Build. Prüfen Sie Chromosomennamen, Intervallgrenzen und endliche bedGraph-Werte; erstellen Sie den Index neu.",
      file: "Die Datei fehlt am gespeicherten Pfad. Fügen Sie sie am neuen Speicherort erneut hinzu.",
      genes: "Genspannen fehlen: downloads/genes_hg38.tsv. Führen Sie wgsextract ref gene-map ausdrücklich aus und öffnen Sie erneut. Reads und Koordinatennavigation bleiben verfügbar.",
      bands: "Zytobänder fehlen: downloads/cytobands_hg38.tsv. Bestätigen Sie den öffentlichen Download mit wgsextract ref cytobands --build hg38 --download und öffnen Sie erneut.",
      noref: "Die exakte hs38-FASTA und Indizes fehlen. Bestätigen Sie --download in Referenzgenome. CRAM-Header-URLs werden nicht abgerufen.",
    },
    fr: {
      limit: "16 pistes facultatives au maximum. Retirez un chemin puis réessayez ; les lectures principales ne comptent pas.",
      index: "L’index de la piste manque. Sélectionnez l’index BAI/CRAI/TBI/CSI correspondant. Pour BED/bedGraph, triez, compressez en BGZF puis lancez tabix -p bed.",
      build: "La référence ou la piste ne correspond pas au génome. Choisissez le FASTA exact et ses index ; noms, longueurs et sommes de contrôle CRAM doivent correspondre.",
      invalid: "Données invalides ou autre assemblage. Vérifiez les chromosomes, les bornes et les valeurs bedGraph finies, puis recréez l’index.",
      file: "Le fichier n’est plus au chemin enregistré. Ajoutez-le depuis son nouvel emplacement.",
      genes: "Positions des gènes absentes : downloads/genes_hg38.tsv. Lancez explicitement wgsextract ref gene-map puis rouvrez. Les lectures et coordonnées restent disponibles.",
      bands: "Cytobandes absentes : downloads/cytobands_hg38.tsv. Confirmez le téléchargement public avec wgsextract ref cytobands --build hg38 --download puis rouvrez.",
      noref: "Le FASTA hs38 exact et ses index manquent. Confirmez --download dans Génomes de référence. Aucune URL de l’en-tête CRAM n’est consultée.",
    },
    "pt-BR": {
      limit: "No máximo 16 trilhas opcionais. Remova um caminho e tente novamente; as leituras principais não contam.",
      index: "Falta o índice da trilha. Selecione o BAI/CRAI/TBI/CSI correspondente. Para BED/bedGraph, ordene, comprima com BGZF e execute tabix -p bed.",
      build: "A referência ou trilha não corresponde ao genoma. Selecione o FASTA exato e seus índices; nomes, comprimentos e checksums CRAM devem coincidir.",
      invalid: "Dados inválidos ou outra montagem. Confira os cromossomos, limites e valores bedGraph finitos e recrie o índice.",
      file: "O arquivo não está mais no caminho salvo. Adicione-o novamente no novo local.",
      genes: "Faltam os intervalos gênicos: downloads/genes_hg38.tsv. Execute explicitamente wgsextract ref gene-map e reabra. Leituras e coordenadas continuam disponíveis.",
      bands: "Faltam citobandas: downloads/cytobands_hg38.tsv. Confirme o download público com wgsextract ref cytobands --build hg38 --download e reabra.",
      noref: "Faltam o FASTA hs38 exato e seus índices. Confirme --download em Genomas de referência. Nenhuma URL do cabeçalho CRAM é acessada.",
    },
  };
  function text(key) {
    var lang = W.params.get("lang") || document.documentElement.lang;
    return (STRINGS[lang] || STRINGS.en)[key];
  }
  function notice(root, key) {
    root.appendChild(h("p.v2-notice", { role: "status", "data-tone": "warn" }, text(key)));
  }
  function mount(root) {
    var state = W.params.get("demo");
    var box = h("details.v2-more", h("summary", "Optional panels and coverage · 16-track limit"),
      h("div.v2-more__body.v2-section",
        h("p.v2-small.v2-muted", "Files must be in My Genomes and use the same assembly. Plain BED/bedGraph loads in full (32 MiB maximum); BGZF .bed.gz/.bedgraph.gz needs TBI/CSI. bigWig uses its own range index. Names alone cannot prove a BED’s assembly."),
        h("label.v2-field", h("span.v2-field__label", "One full track path per line"),
          h("textarea.v2-input", { rows: 3, "data-viewer-extra": "", placeholder: "/demo/panel.bed\n/demo/coverage.bedgraph\n/demo/coverage.bw" })),
        h("button.v2-btn", { type: "button", "data-viewer-load-extra": "" }, "Open demo tracks"),
        h("p.v2-small.v2-dim", "Review fixtures only: the paths above select tiny simulated data, not files on your computer.")));
    root.querySelector(".v2-viewer").before(box);
    root.querySelector(".v2-viewer").before(h("p.v2-small.v2-muted",
      "Local exact reference selected automatically · authenticated Range reads · no whole-genome copy. RefSeq combined gene spans, not exon models. Cytobands here are illustrative; the app uses installed UCSC tables. rsID spots are demo shortcuts, not production lookup."));
    if (state === "genemap") notice(root, "genes");
    if (state === "cytobands") notice(root, "bands");
    if (state === "wrongbuild") notice(root, "build");
    if (state === "noref") notice(root, "noref");
    if (state === "tracks-limit") notice(root, "limit");
    var recovery = h("details.v2-more", h("summary", "Review missing data and track errors"),
      h("div.v2-more__body.v2-row.v2-row--wrap",
        [["noref", "Missing reference"], ["wrongbuild", "Wrong build"], ["index", "Missing index"], ["tracks-limit", "16-track limit"], ["genemap", "Missing genes"], ["cytobands", "Missing cytobands"]].map(function (s) {
          return h("a.v2-btn.v2-btn--sm", { href: "viewer.html?demo=" + s[0] }, s[1]);
        })));
    root.appendChild(recovery);
  }
  function attach(root, browser, showError) {
    var loaded = [];
    var refresh = Promise.resolve();
    var lengths = { chr1: 248956422, chr2: 242193529, chr5: 181538259, chr6: 170805979, chr17: 83257441, chr19: 58617616, chr20: 64444167, chrY: 57227415 };
    if (W.params.get("demo") !== "cytobands") {
      browser.genome.cytobandSource = { getCytobands: function (chr) {
        var end = lengths[chr];
        return end ? [{ start: 0, end: Math.floor(end / 2), name: "p (simulated)", type: "n", stain: 0 }, { start: Math.floor(end / 2), end: end, name: "q (simulated)", type: "p", stain: 50 }] : [];
      }};
      var frame = browser.referenceFrameList[0];
      refresh = browser.search(frame.chr + ":" + Math.max(1, Math.floor(frame.start) + 1) + "-" + Math.floor(frame.end));
    }
    root.querySelector("[data-viewer-load-extra]").addEventListener("click", async function () {
      var paths = root.querySelector("[data-viewer-extra]").value.split(/\r?\n/).map(function (p) { return p.trim(); }).filter(Boolean);
      showError("");
      if (paths.length > 16) return showError(text("limit"));
      if (paths.some(function (p) { return /missing-index/i.test(p); })) return showError(text("index"));
      if (paths.some(function (p) { return /hg19|grch37|wrongbuild/i.test(p); })) return showError(text("build"));
      if (paths.some(function (p) { return !/^\/demo\/(panel\.bed(?:\.gz)?|coverage\.bedgraph(?:\.gz)?|coverage\.bw)$/.test(p); })) return showError(text(/\.gz$/i.test(paths.join("")) ? "index" : "invalid"));
      loaded.forEach(function (name) { browser.removeTrackByName(name); });
      loaded = [];
      for (var i = 0; i < paths.length; i++) {
        var file = paths[i].split("/").pop();
        var bed = /\.bed(?:\.gz)?$/.test(file);
        var name = "Demo " + file + " " + (i + 1);
        var url = W.link("shared/data/igv/" + file);
        if (file === "coverage.bedgraph") url = new File([window.WGSM_COVERAGE_DEMO.bedgraph], file);
        if (file === "coverage.bw") url = new File([Uint8Array.from(atob(window.WGSM_COVERAGE_DEMO.bigwig), function (c) { return c.charCodeAt(0); })], file);
        try {
          await browser.loadTrack({ name: name, url: url,
            indexURL: file.endsWith(".gz") ? W.link("shared/data/igv/" + file + ".tbi") : undefined,
            format: bed ? "bed" : file.endsWith(".bw") ? "bigwig" : "bedgraph", type: bed ? "annotation" : "wig", height: 50, order: 20 + i });
          loaded.push(name);
        } catch (e) { showError(text("invalid")); }
      }
    });
    return refresh;
  }
  W.v2viewerTracks = { text: text, mount: mount, attach: attach };
})();
