/* Dark-theme calendar date-picker for every date field in the app.
 *
 * Plain inputs (type="date") used to show the browser/OS's own calendar
 * popup -- which on most platforms ignores this app's dark theme entirely
 * and pops up as a stark white box. This replaces that with a small,
 * dependency-free calendar dropdown that matches the rest of the UI,
 * styled in base.html (.cal-*).
 *
 * Any <input class="date-input"> is auto-enhanced on page load -- no
 * per-field wiring needed, so new date fields just need that one class.
 * Submitted value stays a plain "YYYY-MM-DD" string, so nothing on the
 * server needed to change. If JavaScript is unavailable, the input is a
 * normal text field the date can still be typed into by hand.
 */
(function () {
  "use strict";

  function pad2(n) { return String(n).padStart(2, "0"); }
  function toISO(year, month, day) { return year + "-" + pad2(month + 1) + "-" + pad2(day); }
  function parseISO(s) {
    if (!s) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s).trim());
    if (!m) return null;
    var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return isNaN(d.getTime()) ? null : d;
  }

  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"];
  var DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

  var activePopup = null;
  var activeInput = null;

  function closePopup() {
    if (activePopup && activePopup.parentNode) activePopup.parentNode.removeChild(activePopup);
    activePopup = null;
    activeInput = null;
    document.removeEventListener("mousedown", onDocMouseDown, true);
    document.removeEventListener("keydown", onDocKeyDown, true);
    window.removeEventListener("resize", closePopup);
    window.removeEventListener("scroll", closePopup, true);
  }

  function onDocMouseDown(e) {
    if (activePopup && !activePopup.contains(e.target) && e.target !== activeInput) closePopup();
  }
  function onDocKeyDown(e) {
    if (e.key === "Escape") closePopup();
  }

  function buildPopup(input) {
    var selected = parseISO(input.value);
    var today = new Date();
    var view = selected
      ? new Date(selected.getFullYear(), selected.getMonth(), 1)
      : new Date(today.getFullYear(), today.getMonth(), 1);

    var pop = document.createElement("div");
    pop.className = "cal-popup";
    pop.setAttribute("role", "dialog");

    function render() {
      pop.innerHTML = "";

      var header = document.createElement("div");
      header.className = "cal-header";

      var prev = document.createElement("button");
      prev.type = "button";
      prev.className = "cal-nav";
      prev.textContent = "‹";
      prev.setAttribute("aria-label", "Previous month");
      prev.onclick = function () { view.setMonth(view.getMonth() - 1); render(); };

      var label = document.createElement("span");
      label.className = "cal-label";
      label.textContent = MONTH_NAMES[view.getMonth()] + " " + view.getFullYear();

      var next = document.createElement("button");
      next.type = "button";
      next.className = "cal-nav";
      next.textContent = "›";
      next.setAttribute("aria-label", "Next month");
      next.onclick = function () { view.setMonth(view.getMonth() + 1); render(); };

      header.appendChild(prev);
      header.appendChild(label);
      header.appendChild(next);
      pop.appendChild(header);

      var grid = document.createElement("div");
      grid.className = "cal-grid";
      DOW.forEach(function (d) {
        var el = document.createElement("div");
        el.className = "cal-dow";
        el.textContent = d;
        grid.appendChild(el);
      });

      var firstOfMonth = new Date(view.getFullYear(), view.getMonth(), 1);
      var startOffset = (firstOfMonth.getDay() + 6) % 7; // Monday-first week
      var daysInMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();

      for (var i = 0; i < startOffset; i++) grid.appendChild(document.createElement("div"));

      for (var day = 1; day <= daysInMonth; day++) {
        (function (day) {
          var cell = document.createElement("button");
          cell.type = "button";
          cell.className = "cal-day";
          cell.textContent = String(day);

          var isSelected = selected &&
            view.getFullYear() === selected.getFullYear() &&
            view.getMonth() === selected.getMonth() &&
            day === selected.getDate();
          var isToday =
            view.getFullYear() === today.getFullYear() &&
            view.getMonth() === today.getMonth() &&
            day === today.getDate();

          if (isSelected) cell.classList.add("cal-selected");
          if (isToday) cell.classList.add("cal-today");

          cell.onclick = function () {
            input.value = toISO(view.getFullYear(), view.getMonth(), day);
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            closePopup();
          };
          grid.appendChild(cell);
        })(day);
      }
      pop.appendChild(grid);

      var footer = document.createElement("div");
      footer.className = "cal-footer";

      var todayBtn = document.createElement("button");
      todayBtn.type = "button";
      todayBtn.className = "cal-link";
      todayBtn.textContent = "Today";
      todayBtn.onclick = function () {
        var t = new Date();
        input.value = toISO(t.getFullYear(), t.getMonth(), t.getDate());
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        closePopup();
      };

      var clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "cal-link";
      clearBtn.textContent = "Clear";
      clearBtn.onclick = function () {
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        closePopup();
      };

      footer.appendChild(todayBtn);
      footer.appendChild(clearBtn);
      pop.appendChild(footer);
    }

    render();
    return pop;
  }

  function positionPopup(pop, input) {
    var rect = input.getBoundingClientRect();
    pop.style.position = "absolute";
    pop.style.top = (rect.bottom + window.scrollY + 4) + "px";
    pop.style.left = (rect.left + window.scrollX) + "px";
    requestAnimationFrame(function () {
      var popRect = pop.getBoundingClientRect();
      var maxLeft = window.innerWidth - popRect.width - 8;
      if (popRect.left > maxLeft) pop.style.left = Math.max(8, maxLeft) + "px";
      if (popRect.bottom > window.innerHeight) {
        pop.style.top = (rect.top + window.scrollY - popRect.height - 4) + "px";
      }
    });
  }

  function openPopup(input) {
    if (activeInput === input) return;
    closePopup();
    var pop = buildPopup(input);
    document.body.appendChild(pop);
    activePopup = pop;
    activeInput = input;
    positionPopup(pop, input);

    document.addEventListener("mousedown", onDocMouseDown, true);
    document.addEventListener("keydown", onDocKeyDown, true);
    window.addEventListener("resize", closePopup);
    window.addEventListener("scroll", closePopup, true);
  }

  function attach(input) {
    if (input.dataset.calAttached) return;
    input.dataset.calAttached = "1";
    input.setAttribute("autocomplete", "off");
    if (!input.placeholder) input.placeholder = "YYYY-MM-DD";

    var wrap = document.createElement("span");
    wrap.className = "cal-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var icon = document.createElement("span");
    icon.className = "cal-icon";
    icon.textContent = "📅"; // calendar emoji
    icon.setAttribute("aria-hidden", "true");
    wrap.appendChild(icon);

    input.addEventListener("focus", function () { openPopup(input); });
    input.addEventListener("click", function () { openPopup(input); });
  }

  function init() {
    document.querySelectorAll("input.date-input").forEach(attach);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
