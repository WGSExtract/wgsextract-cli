/* Mock server core, part 8: MOCK-ONLY admin routes. Not proposed for the
 * real server: they drive the simulation (clock, network speed, reset) and
 * the demo state the mockup keeps (flags, the pretend computer).
 * Environment-agnostic. */
(function (root) {
  "use strict";
  var M = root.WGSMMock;
  var U = M.util;

  M.routes.push(function (mock) {
    var r = mock.router;

    function clockState() {
      var now = mock.clock.now();
      return { now: U.iso(now), now_ms: now, speed: mock.clock.speed(), paused: mock.clock.speed() === 0, network_bps: mock.doc().net.bps };
    }

    /* MOCK-ONLY. The simulated clock: speed is simulated seconds per real
     * second (0 paused, 1, 60, 600) */
    r.add("GET", "/v1/clock", "mock", clockState);
    r.add("POST", "/v1/clock", "mock", function (req) {
      var b = req.body || {};
      if (b.speed !== undefined) mock.clock.setSpeed(b.speed);
      if (b.now !== undefined) mock.clock.set(typeof b.now === "number" ? b.now : U.parseIso(b.now));
      if (b.advance_s) mock.clock.advance(b.advance_s * 1000);
      mock.tick();
      return clockState();
    });

    /* MOCK-ONLY. Simulated network speed in bytes per simulated second;
     * 0 is unlimited (downloads finish at once) */
    r.add("GET", "/v1/mock/network", "mock", function () {
      return { bps: mock.doc().net.bps, label: M.fmt.rate(mock.doc().net.bps) };
    });
    r.add("POST", "/v1/mock/network", "mock", function (req) {
      var bps = (req.body || {}).bps;
      if (typeof bps !== "number" || bps < 0) M.fail(400, "usage", "bps must be a number of bytes per second, 0 for unlimited.");
      mock.setNetwork(bps, mock.clock.now());
      return { bps: mock.doc().net.bps, label: M.fmt.rate(mock.doc().net.bps) };
    });

    /* MOCK-ONLY. Everything back to the seed, the clock included */
    r.add("POST", "/v1/mock/reset", "mock", function () {
      mock.reset();
      return { ok: true, clock: clockState() };
    });

    /* MOCK-ONLY. As if the server stopped: queued and running jobs become
     * interrupted (try Resume) */
    r.add("POST", "/v1/mock/interrupt", "mock", function () {
      mock.interrupt(mock.clock.now());
      return { ok: true };
    });

    /* MOCK-ONLY. The demo's capability flags (mem2-hs38, ref-T2Tv20,
     * panel-1kg, alex-fixed, realign-cancelled, …): derived from finished
     * jobs, then explicit overrides. POST {set: [], unset: [], clear: []};
     * clear drops an override so the jobs decide again. */
    function flags() {
      var on = mock.flags();
      return { flags: Object.keys(on).sort(), explicit: mock.doc().flags };
    }
    r.add("GET", "/v1/mock/flags", "mock", flags);
    r.add("POST", "/v1/mock/flags", "mock", function (req) {
      var b = req.body || {};
      var own = mock.doc().flags;
      (b.set || []).forEach(function (f) {
        own[f] = true;
      });
      (b.unset || []).forEach(function (f) {
        own[f] = false;
      });
      (b.clear || []).forEach(function (f) {
        delete own[f];
      });
      mock.save();
      return flags();
    });

    /* MOCK-ONLY. Which pretend computer (machine.js profile) the server is */
    function machine() {
      var m = mock.ctx().machine;
      return { profile: m.key, label: m.label, profiles: Object.keys(mock.config.profiles) };
    }
    r.add("GET", "/v1/mock/machine", "mock", machine);
    r.add("POST", "/v1/mock/machine", "mock", function (req) {
      var key = (req.body || {}).profile;
      if (key !== null && !mock.config.profiles[key]) M.fail(400, "usage", "Unknown profile " + JSON.stringify(key) + ".");
      mock.doc().machine = key;
      mock.save();
      return machine();
    });

    /* MOCK-ONLY. The whole stored state, for debugging */
    r.add("GET", "/v1/mock/state", "mock", function () {
      return { clock: clockState(), state: mock.doc() };
    });
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
