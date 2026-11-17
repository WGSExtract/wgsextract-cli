/* Onboarding uses the same server and controls, without the library shell. */
(function () {
  "use strict";
  var W = window.WGSM;
  W.page.onReady = function () {
    W.v2state.apply();
    W.v2machine.localize();
    W.v2edge.start();
    W.v2edgeViews.start();
  };
})();
