// Paint over writing, labels or glare on a plate photo. Each dab is stored as
// [x, y, r]: x and r as fractions of the photo's width, y of its height. The
// server ignores those areas when it reads the plate and builds the 3D model.
(function () {
  "use strict";
  function setup(wrap) {
    var form = wrap.closest("form");
    var img = wrap.querySelector(".paint-photo");
    var canvas = wrap.querySelector(".paint-canvas");
    var data = form.querySelector(".paint-data");
    var brush = form.querySelector(".paint-brush");
    var ctx = canvas.getContext("2d");
    var dabs = [];
    var strokes = [];        // number of dabs in each stroke, for undo
    try { dabs = JSON.parse(data.value || "[]") || []; } catch (e) { dabs = []; }
    if (dabs.length) strokes.push(dabs.length);
    var drawing = false, last = null;

    function size() {
      canvas.width = img.clientWidth;
      canvas.height = img.clientHeight;
      canvas.style.width = img.clientWidth + "px";
      canvas.style.height = img.clientHeight + "px";
      draw();
    }
    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "rgba(230, 40, 60, 0.45)";
      dabs.forEach(function (d) {
        ctx.beginPath();
        ctx.arc(d[0] * canvas.width, d[1] * canvas.height, d[2] * canvas.width, 0, Math.PI * 2);
        ctx.fill();
      });
      data.value = JSON.stringify(dabs);
    }
    function radius() { return 0.006 * Number(brush.value || 3); }
    function point(ev) {
      var box = canvas.getBoundingClientRect();
      return [(ev.clientX - box.left) / box.width, (ev.clientY - box.top) / box.height];
    }
    function dab(p) {
      var r = radius();
      if (last) {
        // fill the gap between two pointer events so a fast stroke stays solid
        var dx = (p[0] - last[0]), dy = (p[1] - last[1]) * canvas.height / canvas.width;
        var dist = Math.sqrt(dx * dx + dy * dy), step = r * 0.6;
        if (dist < step) return;
        var n = Math.min(40, Math.floor(dist / step));
        for (var i = 1; i < n; i++) {
          var t = i / n;
          dabs.push([round(last[0] + (p[0] - last[0]) * t), round(last[1] + (p[1] - last[1]) * t), round(r)]);
          strokes[strokes.length - 1]++;
        }
      }
      if (dabs.length >= 600) return;
      dabs.push([round(p[0]), round(p[1]), round(r)]);
      strokes[strokes.length - 1]++;
      last = p;
      draw();
    }
    function round(v) { return Math.round(v * 10000) / 10000; }

    canvas.addEventListener("pointerdown", function (ev) {
      ev.preventDefault();
      canvas.setPointerCapture(ev.pointerId);
      drawing = true; last = null; strokes.push(0);
      dab(point(ev));
    });
    canvas.addEventListener("pointermove", function (ev) {
      if (drawing) { ev.preventDefault(); dab(point(ev)); }
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) {
      canvas.addEventListener(t, function () { drawing = false; last = null; });
    });
    form.querySelector(".paint-undo").addEventListener("click", function () {
      var n = strokes.pop() || 0;
      dabs.splice(dabs.length - n, n);
      draw();
    });
    form.querySelector(".paint-clear").addEventListener("click", function () {
      dabs = []; strokes = []; draw();
    });
    if (img.complete) size(); else img.addEventListener("load", size);
    window.addEventListener("resize", size);
    var details = wrap.closest("details");
    if (details) details.addEventListener("toggle", function () { if (details.open) size(); });
  }
  document.querySelectorAll(".paint-wrap").forEach(setup);
})();
