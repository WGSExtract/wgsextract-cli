/* WGSM.v2clock: the simulated clock and network the mock server runs on,
 * and the control for the mockup-settings dialog. In shared mode they are
 * the shared server's, so changing them changes them for every reviewer.
 *
 *   now()  simulated ms since the epoch     speed()  sim s per real s
 *   setSpeed(x)  0 paused, 1, 60 (default), 600       -> Promise
 *   network() / setNetwork(bps)  bytes per simulated second, 0 unlimited
 *   format(ms) "09:58" · when(ms) "Today 06:14" · ago(ms) · dur(ms) "1 h 52 min"
 *   reset()  the whole simulation back to its start (jobs included)
 *   control()  element: speed, network, which server, reset */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var F = window.WGSMMock.fmt;
  var api = WGSM.v2api;
  var h = WGSM.h;
  var SPEEDS = [[0, "Paused"], [1, "1×"], [60, "1 min/s"], [600, "10 min/s"]];
  var NETWORK = [[1e7, "10 MB/s"], [1e8, "100 MB/s"], [1e9, "1 GB/s"], [0, "Unlimited"]];

  function now() {
    return api.clock().now();
  }

  function post(path, body) {
    return api.post(path, body).then(function (res) {
      if (path === "/v1/clock") api.syncClock(res.body);
      return api.refresh().then(function () {
        return res;
      });
    });
  }

  function seg(label, options, current, pick) {
    return h("div.v2-seg", { role: "group", "aria-label": label }, options.map(function (o) {
      return h("button", { type: "button", value: String(o[0]), "aria-pressed": String(o[0] === current()), onclick: function () {
        pick(o[0]);
      } }, o[1]);
    }));
  }

  function field(label, control, note) {
    return h("div.v2-field", h("span.v2-field__label", label), h("div.v2-field__control", control, note || null));
  }

  function press(group, value) {
    WGSM.qsa("button", group).forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.value === String(value)));
    });
  }

  /* Speed, network, which mock server, reset */
  function control() {
    var time = h("span.v2-small.v2-muted");
    var opened = false;
    var speed = seg("Simulated time", SPEEDS, clock.speed, function (x) {
      clock.setSpeed(x);
    });
    var net = seg("Network speed", NETWORK, clock.network, function (x) {
      clock.setNetwork(x);
    });
    var url = h("input.v2-input.v2-input--mono", { type: "url", placeholder: "http://127.0.0.1:8766", "aria-label": "Shared mock server address", value: api.baseUrl() });
    var where = h("p.v2-small.v2-muted");
    var shared = h("div.v2-row.v2-row--wrap", url, h("button.v2-btn.v2-btn--sm", { type: "button", onclick: function () {
      connect("shared", url.value);
    } }, "Connect"));
    var mode = seg("Mock server", [["browser", "This browser"], ["shared", "Shared server"]], api.mode, function (m) {
      opened = m === "shared";
      if (m === "browser") connect("browser");
      else update();
    });
    var reset = h("button.v2-btn.v2-btn--sm.v2-btn--danger", { type: "button", onclick: function () {
      var everyone = api.mode() === "shared";
      if (everyone && !window.confirm("Clear the shared mock state for everyone using " + api.baseUrl() + "?")) return;
      clock.reset().then(function () {
        WGSM.toast(everyone ? "Cleared the shared mock state" : "The simulation starts again");
      });
    } });

    function connect(m, address) {
      if (m === "shared" && !/^https?:\/\/./.test(address || "")) {
        WGSM.toast("Type the server’s address, like http://127.0.0.1:8766", "circle-alert");
        return;
      }
      api.setMode(m, address).then(function (r) {
        WGSM.toast(r.ok ? (m === "shared" ? "Connected to " + api.baseUrl() + (r.mock ? "" : " (not a mock server)") : "Using this browser’s simulation") : "Can’t connect: " + r.message, r.ok ? null : "circle-alert");
        update();
      });
    }

    function update() {
      if (!el.isConnected) return;
      time.textContent = F.when(now(), now()) + " · " + (clock.speed() ? SPEEDS.filter(function (s) {
        return s[0] === clock.speed();
      }).map(function (s) {
        return s[1];
      })[0] || clock.speed() + "×" : "paused");
      press(speed, clock.speed());
      press(net, clock.network());
      press(mode, api.mode());
      shared.hidden = api.mode() !== "shared" && !opened;
      where.textContent = api.mode() === "shared"
        ? "Jobs, people and the clock are shared by everyone using " + api.baseUrl() + "."
        : "Jobs and the clock are kept in this browser only.";
      reset.textContent = api.mode() === "shared" ? "Clear the shared mock state" : "Reset the simulation";
    }

    shared.hidden = true;
    var el = h("div.v2-section.v2-simctl", { dataset: { v2Keep: "off", annotId: "sim-control" } },
      field("Simulated time", speed, time),
      field("Network", net, h("span.v2-small.v2-muted", "Downloads run at this speed, in simulated time")),
      field("Mock server", h("div.v2-section", mode, shared, where)),
      h("div.v2-row", reset));
    api.ready.then(update);
    api.on(update);
    return el;
  }

  var clock = (WGSM.v2clock = {
    now: now,
    speed: function () {
      return api.clock().speed();
    },
    setSpeed: function (x) {
      return post("/v1/clock", { speed: x });
    },
    network: function () {
      return api.network();
    },
    setNetwork: function (bps) {
      return post("/v1/mock/network", { bps: bps });
    },
    /* Jump ahead (mock-only), e.g. advance(30 * 60) */
    advance: function (seconds) {
      return post("/v1/clock", { advance_s: seconds });
    },
    reset: function () {
      return api.reset();
    },
    format: function (ms) {
      return F.hm(ms === undefined ? now() : ms);
    },
    when: function (ms) {
      return F.when(ms, now());
    },
    ago: function (ms) {
      return F.ago(ms, now());
    },
    dur: F.dur,
    gb: F.gb,
    rate: F.rate,
    speeds: SPEEDS,
    networks: NETWORK,
    control: control,
  });
})();
