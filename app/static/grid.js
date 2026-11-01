/* The design's table behaviour (Deployment_Tracker.html's `kt` table), shared by every
   list table: drag a header to reorder, drag its right edge to resize, the gear's
   show / hide columns and Reset table layout, the header menu (sort, "contains"
   filter, move, hide), and empty filler rows down to the card's foot.

   Layout (order, hidden, widths) is per browser in localStorage, under the table's
   data-column-order key — the server always renders the default order, so nothing
   else depends on it. Sort and filter are not kept, as in the reference.

   A table that pages on the server (Requests) passes `server` hooks, and sort and
   filter reload the page instead of rearranging the rows shown.

   Cells are tagged data-col="<key>" (the header's text, slugged), which is what the
   CSS sizes by. */
(function () {
  "use strict";

  function slug(text) { return text.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }
  function read(key, fallback) {
    try { var v = JSON.parse(localStorage.getItem(key) || "null"); return v == null ? fallback : v; } catch (err) { return fallback; }
  }
  function write(key, value, isDefault) {
    try { if (isDefault) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch (err) {}
  }
  function cellValue(cell) {
    if (!cell) return "";
    if (cell.hasAttribute("data-value")) return cell.getAttribute("data-value");
    var time = cell.querySelector("time[datetime]");
    if (time) return time.getAttribute("datetime");
    return cell.textContent.replace(/\s+/g, " ").trim();
  }
  function isEmpty(value) { return value === "" || value === "—"; }
  var collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  /* Columns from the header. A grouped header (Release Tracker: Test / Live / Main
     Version over Version + Update At) takes its keys from the lower row, prefixed
     with the group. */
  function keyOf(th, text) { return th.getAttribute("data-key") || slug(text); }   // data-key: two headers share a label
  function columnsOf(table) {
    var rows = table.tHead.rows;
    if (rows.length === 1) {
      return Array.prototype.map.call(rows[0].cells, function (th) {
        return { key: keyOf(th, th.textContent), label: th.getAttribute("data-label") || th.textContent.trim(), th: th };
      });
    }
    var cols = [], lower = Array.prototype.slice.call(rows[1].cells), at = 0;
    Array.prototype.forEach.call(rows[0].cells, function (th) {
      if ((th.rowSpan || 1) > 1) { cols.push({ key: keyOf(th, th.textContent), label: th.textContent.trim(), th: th }); return; }
      if (!th.hasAttribute("data-span")) th.setAttribute("data-span", th.colSpan || 1);   // before hiding narrows it
      for (var i = 0; i < +th.getAttribute("data-span"); i++, at++) {
        var sub = lower[at];
        cols.push({ key: slug(th.textContent + " " + sub.textContent), label: th.textContent.trim() + " " + sub.textContent.trim(), th: sub, group: th });
      }
    });
    return cols;
  }

  window.dtGrid = function (tables, opts) {
    tables = Array.prototype.slice.call(tables);
    opts = opts || {};
    if (!tables.length) return null;
    var key = tables[0].getAttribute("data-column-order");
    var pinned = opts.pinned || [];
    var noMenu = opts.noMenu || pinned;
    var notText = opts.notText || [];
    var grouped = tables[0].tHead.rows.length > 1;
    var server = opts.server || null;
    var iconsEl = document.getElementById("grid-icons");   // in base.html
    var icons = iconsEl ? iconsEl.content : null;
    function icon(name) {
      var holder = icons && icons.querySelector('[data-icon="' + name + '"]');
      return holder ? holder.firstElementChild.cloneNode(true) : document.createTextNode("");
    }

    var columns = columnsOf(tables[0]);
    var defaultOrder = columns.map(function (c) { return c.key; });
    var labels = {};
    columns.forEach(function (c) { labels[c.key] = c.label; });
    var movable = defaultOrder.filter(function (k) { return pinned.indexOf(k) === -1; });

    // Tag every cell (and <col>) with its column, in the server's order.
    tables.forEach(function (table) {
      var cols = columnsOf(table);
      cols.forEach(function (c) { c.th.setAttribute("data-col", c.key); });
      var colEls = table.querySelectorAll("colgroup > col");
      if (colEls.length === cols.length) Array.prototype.forEach.call(colEls, function (el, i) { el.setAttribute("data-col", cols[i].key); });
      Array.prototype.forEach.call(table.tBodies, function (body) {
        Array.prototype.forEach.call(body.rows, function (row, index) {
          // Only data rows: a one-cell "No users" / "No terms" row isn't sorted or counted.
          if (row.cells.length !== cols.length) return;
          row.setAttribute("data-grid-index", index);
          Array.prototype.forEach.call(row.cells, function (cell, i) { cell.setAttribute("data-col", cols[i].key); });
        });
      });
      table.setAttribute("data-cols", "");
    });

    // --- Order (not for grouped headers: a group's columns stay under it) ---------
    var orderKey = key, hiddenKey = key + "-hidden", widthKey = key + "-widths";
    function savedOrder() {
      var saved = read(orderKey, null);
      if (Array.isArray(saved) && saved.length === movable.length &&
          movable.every(function (k) { return saved.indexOf(k) !== -1; })) return saved;
      return movable.slice();
    }
    var order = grouped ? movable.slice() : savedOrder();
    function full() {   // pinned columns keep their own places
      var rest = order.slice();
      return defaultOrder.map(function (k) { return pinned.indexOf(k) !== -1 ? k : rest.shift(); });
    }
    function applyOrder() {
      if (grouped) return;
      var keys = full();
      tables.forEach(function (table) {
        var colgroup = table.querySelector("colgroup");
        if (colgroup) keys.forEach(function (k) { var el = colgroup.querySelector(':scope > [data-col="' + k + '"]'); if (el) colgroup.appendChild(el); });
        Array.prototype.forEach.call(table.rows, function (row) {
          if (row.cells.length !== keys.length) return;
          keys.forEach(function (k) { var cell = row.querySelector(':scope > [data-col="' + k + '"]'); if (cell) row.appendChild(cell); });
        });
      });
    }
    function setOrder(next) {
      order = next;
      applyOrder();
      write(orderKey, order, order.join() === movable.join());
      emit();
    }

    // --- Hidden columns (at least one stays) ----------------------------------------
    var hidden = (read(hiddenKey, []) || []).filter(function (k) { return movable.indexOf(k) !== -1; });
    function applyHidden() {
      if (hidden.length >= movable.length) hidden = hidden.slice(0, movable.length - 1);
      tables.forEach(function (table) {
        table.querySelectorAll("[data-col]").forEach(function (el) {
          el.classList.toggle("col-hidden", hidden.indexOf(el.getAttribute("data-col")) !== -1);
        });
        // A group header spans only its columns still showing, and goes with the last.
        if (grouped) {
          var spans = new Map();
          columnsOf(table).forEach(function (c) {
            if (!c.group) return;
            if (!spans.has(c.group)) spans.set(c.group, 0);
            if (hidden.indexOf(c.key) === -1) spans.set(c.group, spans.get(c.group) + 1);
          });
          spans.forEach(function (n, th) { th.colSpan = Math.max(1, n); th.classList.toggle("col-hidden", n === 0); });
        }
      });
      syncGear();
    }
    function setHidden(next) {
      hidden = next;
      applyHidden();
      write(hiddenKey, hidden, !hidden.length);
      fitRows();
      emit();
    }
    function shown() { return full().filter(function (k) { return pinned.indexOf(k) === -1 && hidden.indexOf(k) === -1; }); }

    // --- Widths ----------------------------------------------------------------------
    var widths = read(widthKey, {}) || {};
    function applyWidths() {
      tables.forEach(function (table) {
        columnsOf(table).forEach(function (c) {
          var w = widths[c.key] ? widths[c.key] + "px" : "";
          var col = table.querySelector('colgroup > col[data-col="' + c.key + '"]');
          if (col) { if (!col.hasAttribute("data-width")) col.setAttribute("data-width", col.style.width); col.style.width = w || col.getAttribute("data-width"); }
          else c.th.style.width = w;
        });
      });
    }
    function setWidths(next) {
      widths = next;
      applyWidths();
      write(widthKey, widths, !Object.keys(widths).length);
    }

    // --- Sort and filter -------------------------------------------------------------
    var state = server ? { sort: server.sort || null, dir: server.dir || "asc", filters: server.filters || {} }
                       : { sort: null, dir: "asc", filters: {} };
    function rowsOf(body) {
      return Array.prototype.filter.call(body.rows, function (row) { return !row.classList.contains("filler-row") && row.hasAttribute("data-grid-index"); });
    }
    function applySortFilter() {
      if (server) return;
      tables.forEach(function (table) {
        Array.prototype.forEach.call(table.tBodies, function (body) {
          var rows = rowsOf(body);
          rows.forEach(function (row) {
            var out = Object.keys(state.filters).some(function (k) {
              var needle = state.filters[k].toLowerCase();
              return cellValue(row.querySelector(':scope > [data-col="' + k + '"]')).toLowerCase().indexOf(needle) === -1;
            });
            row.classList.toggle("grid-out", out);
          });
          var sorted = rows.slice().sort(function (a, b) {
            if (state.sort) {
              var va = cellValue(a.querySelector(':scope > [data-col="' + state.sort + '"]'));
              var vb = cellValue(b.querySelector(':scope > [data-col="' + state.sort + '"]'));
              if (isEmpty(va) !== isEmpty(vb)) return isEmpty(va) ? 1 : -1;   // blanks last, both ways
              var c = collator.compare(va, vb);
              if (c) return state.dir === "desc" ? -c : c;
            }
            return a.getAttribute("data-grid-index") - b.getAttribute("data-grid-index");
          });
          var anchor = body.querySelector("tr:not([data-grid-index])");   // fillers, empty-note rows stay last
          sorted.forEach(function (row) { body.insertBefore(row, anchor); });
        });
      });
      markHeaders();
      fitRows();
      emit();
    }
    function sortBy(k, dir) {
      if (server) return server.onSort(k, dir);
      state.sort = k; state.dir = dir || "asc"; applySortFilter();
    }
    function filterBy(k, value) {
      if (server) return server.onFilter(k, value);
      if (value) state.filters[k] = value; else delete state.filters[k];
      applySortFilter();
    }
    function clearFilters() {
      if (server) return server.onClearFilters();
      state.filters = {}; applySortFilter();
    }
    function clearSort() {
      if (server) return server.onSort(null, null);
      state.sort = null; applySortFilter();
    }
    function markHeaders() {
      tables.forEach(function (table) {
        columnsOf(table).forEach(function (c) {
          var old = c.th.querySelector(".hdr-marks");
          if (old) old.remove();
          var marks = document.createElement("span");
          marks.className = "hdr-marks";
          if (state.sort === c.key) marks.appendChild(icon(state.dir === "desc" ? "desc" : "asc"));
          if (state.filters[c.key]) marks.appendChild(icon("filter"));
          if (marks.childNodes.length) c.th.insertBefore(marks, c.th.querySelector(".col-resizer"));
        });
      });
      syncGear();
    }

    // --- Gear: show / hide columns, clear, reset -------------------------------------
    // The gear (_table_settings.html) is found by the table's key; not every table has one.
    var gearButton = document.querySelector('[data-grid-gear="' + key + '"]');
    var gearMenu = document.querySelector('[data-grid-menu="' + key + '"]');
    var gear = gearButton && gearMenu ? { button: gearButton, menu: gearMenu } : null;
    function syncGear() {
      if (!gear) return;
      var list = gear.menu.querySelector("[data-grid-columns]");
      if (list && !list.childNodes.length) {
        movable.forEach(function (k) {
          var label = document.createElement("label");
          label.className = "menu-item cb";
          var box = document.createElement("input");
          box.type = "checkbox";
          box.setAttribute("data-col-toggle", k);
          box.addEventListener("change", function () {
            var next = hidden.filter(function (h) { return h !== k; });
            if (!box.checked) next.push(k);
            setHidden(next);
          });
          var text = document.createElement("span");
          text.textContent = labels[k];
          label.appendChild(box);
          label.appendChild(text);
          list.appendChild(label);
        });
      }
      gear.menu.querySelectorAll("[data-col-toggle]").forEach(function (box) {
        var k = box.getAttribute("data-col-toggle");
        box.checked = hidden.indexOf(k) === -1;
        box.disabled = box.checked && hidden.length === movable.length - 1;
      });
      var nFilters = Object.keys(state.filters).length;
      var clearF = gear.menu.querySelector("[data-grid-clear-filters]");
      if (clearF) {
        clearF.disabled = !nFilters;
        clearF.lastChild.textContent = nFilters ? "Clear " + nFilters + " filter" + (nFilters > 1 ? "s" : "") : "Clear filters";
      }
      var clearS = gear.menu.querySelector("[data-grid-clear-sort]");
      if (clearS) clearS.disabled = !state.sort;
      var count = gear.menu.querySelector("[data-grid-count]");
      if (count && !server) {
        var all = 0, visible = 0;
        tables.forEach(function (table) {
          if (table.hidden) return;   // another tab's table
          // Of the rows the page's own filters leave (a tab, Active / Retire, All /
          // Active / Inactive), how many the column filters keep — as the reference counts.
          Array.prototype.forEach.call(table.tBodies, function (b) {
            rowsOf(b).forEach(function (r) { if (r.hidden) return; all++; if (!r.classList.contains("grid-out")) visible++; });
          });
        });
        count.textContent = visible + " of " + all + " items";
      }
    }
    function setGear(open) {
      if (!gear) return;
      gear.menu.hidden = !open;
      gear.button.setAttribute("aria-expanded", open ? "true" : "false");
    }
    function reset() {
      order = movable.slice(); applyOrder(); write(orderKey, order, true);
      hidden = []; applyHidden(); write(hiddenKey, hidden, true);
      setWidths({});
      setGear(false);
      if (server && (state.sort || Object.keys(state.filters).length)) return server.onReset();
      state = { sort: null, dir: "asc", filters: {} };
      applySortFilter();
    }
    if (gear) {
      gear.button.addEventListener("click", function (event) { event.stopPropagation(); closeMenu(); setGear(gear.menu.hidden); });
      gear.menu.addEventListener("click", function (event) { event.stopPropagation(); });
      document.addEventListener("click", function () { setGear(false); });
      document.addEventListener("keydown", function (event) { if (event.key === "Escape") setGear(false); });
      var resetButton = gear.menu.querySelector("[data-grid-reset]");
      if (resetButton) resetButton.addEventListener("click", reset);
      var cf = gear.menu.querySelector("[data-grid-clear-filters]");
      if (cf) cf.addEventListener("click", function () { setGear(false); clearFilters(); });
      var cs = gear.menu.querySelector("[data-grid-clear-sort]");
      if (cs) cs.addEventListener("click", function () { setGear(false); clearSort(); });
    }

    // --- Header menu -----------------------------------------------------------------
    var menu = document.createElement("div");
    menu.className = "col-menu col-menu-fixed";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    document.body.appendChild(menu);
    function closeMenu() { menu.hidden = true; menu.innerHTML = ""; menu.dataset.for = ""; }
    function item(name, text, onClick, disabled) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "menu-item";
      button.setAttribute("role", "menuitem");
      button.appendChild(icon(name));
      button.appendChild(document.createTextNode(text));
      button.disabled = !!disabled;
      button.addEventListener("click", function () { closeMenu(); onClick(); });
      return button;
    }
    function sep() { var d = document.createElement("div"); d.className = "menu-sep"; return d; }
    function openMenu(th, k) {
      menu.innerHTML = "";
      menu.appendChild(item("asc", "Sort ascending", function () { sortBy(k, "asc"); }));
      menu.appendChild(item("desc", "Sort descending", function () { sortBy(k, "desc"); }));
      if (state.sort === k) menu.appendChild(item("decline", "Clear sort", clearSort));
      if (notText.indexOf(k) === -1) {
        menu.appendChild(sep());
        var box = document.createElement("div");
        box.className = "menu-filter";
        var title = document.createElement("label");
        title.className = "menu-label";
        title.textContent = "Filter by " + labels[k];
        var field = document.createElement("div");
        field.className = "grid-field";
        var input = document.createElement("input");
        input.type = "text";
        input.placeholder = "Contains…";
        input.value = state.filters[k] || "";
        title.htmlFor = input.id = "grid-filter-input";
        input.addEventListener("keydown", function (event) {
          if (event.key === "Enter") { event.preventDefault(); var v = input.value.trim(); closeMenu(); filterBy(k, v); }
        });
        field.appendChild(input);
        box.appendChild(title);
        box.appendChild(field);
        menu.appendChild(box);
        if (state.filters[k]) menu.appendChild(item("clear", "Clear filter", function () { filterBy(k, ""); }));
      }
      if (pinned.indexOf(k) === -1) {
        menu.appendChild(sep());
        var visible = shown(), at = visible.indexOf(k);
        function move(step) {
          var next = order.slice(), other = visible[at + step];
          var i = next.indexOf(k), j = next.indexOf(other);
          next[i] = other; next[j] = k;
          setOrder(next);
        }
        if (!grouped) {   // a group's columns stay under it
          menu.appendChild(item("left", "Move left", function () { move(-1); }, at <= 0));
          menu.appendChild(item("right", "Move right", function () { move(1); }, at >= visible.length - 1));
        }
        menu.appendChild(item("hide", "Hide column", function () { setHidden(hidden.concat([k])); }, visible.length <= 1));
      }
      var rect = th.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 250)) + "px";
      menu.style.top = (rect.bottom + 2) + "px";
      menu.hidden = false;
      menu.dataset.for = k;
      var focus = menu.querySelector("input");
      if (focus) focus.focus();
    }
    menu.addEventListener("click", function (event) { event.stopPropagation(); });
    document.addEventListener("click", closeMenu);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeMenu(); });
    window.addEventListener("scroll", function (event) { if (!menu.contains(event.target)) closeMenu(); }, true);

    // --- Header wiring: menu on click, drag to reorder, grip to resize ---------------
    var dragKey = null;
    tables.forEach(function (table) {
      columnsOf(table).forEach(function (c) {
        var th = c.th, k = c.key;
        if (noMenu.indexOf(k) === -1) {
          th.classList.add("has-menu");
          th.title = grouped || pinned.indexOf(k) !== -1 ? "Sort or filter" : "Sort, filter, move or hide — or drag to move";
          th.addEventListener("click", function (event) {
            if (event.target.closest(".col-resizer")) return;
            event.stopPropagation();
            setGear(false);
            if (!menu.hidden && menu.dataset.for === k) { closeMenu(); return; }
            openMenu(th, k);
          });
        }
        if (!grouped && pinned.indexOf(k) === -1) {
          th.draggable = true;
          th.addEventListener("dragstart", function (event) {
            dragKey = k;
            th.classList.add("is-dragging");
            event.dataTransfer.effectAllowed = "move";
            try { event.dataTransfer.setData("text/plain", k); } catch (err) {}
          });
          th.addEventListener("dragend", function () {
            dragKey = null;
            document.querySelectorAll(".is-dragging, .drop-before, .drop-after").forEach(function (el) {
              el.classList.remove("is-dragging", "drop-before", "drop-after");
            });
          });
          th.addEventListener("dragover", function (event) {
            if (!dragKey || dragKey === k) return;
            event.preventDefault();
            var after = event.offsetX > th.offsetWidth / 2;
            th.classList.toggle("drop-after", after);
            th.classList.toggle("drop-before", !after);
          });
          th.addEventListener("dragleave", function () { th.classList.remove("drop-before", "drop-after"); });
          th.addEventListener("drop", function (event) {
            event.preventDefault();
            if (!dragKey || dragKey === k) return;
            var after = th.classList.contains("drop-after");
            var next = order.filter(function (o) { return o !== dragKey; });
            next.splice(next.indexOf(k) + (after ? 1 : 0), 0, dragKey);
            setOrder(next);
          });
        }
        if (noMenu.indexOf(k) === -1 || pinned.indexOf(k) !== -1) {
          var grip = document.createElement("span");
          grip.className = "col-resizer";
          grip.setAttribute("aria-hidden", "true");
          grip.addEventListener("click", function (event) { event.stopPropagation(); });
          grip.addEventListener("mousedown", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var startX = event.clientX, startW = th.getBoundingClientRect().width;
            var wasDraggable = th.draggable;
            th.draggable = false;
            document.body.classList.add("is-resizing");
            function move(e) { var next = Object.assign({}, widths); next[k] = Math.max(50, Math.round(startW + e.clientX - startX)); setWidths(next); }
            function up() {
              document.removeEventListener("mousemove", move);
              document.removeEventListener("mouseup", up);
              document.body.classList.remove("is-resizing");
              th.draggable = wasDraggable;
              fitRows();
            }
            document.addEventListener("mousemove", move);
            document.addEventListener("mouseup", up);
          });
          th.appendChild(grip);
        }
      });
    });

    // --- Filler rows: the reference's empty grid lines down to the card's foot -------
    var fillers = [];
    function measure(scroll, table) {
      var sample = table.tBodies[0] && table.tBodies[0].querySelector("tr:not(.filler-row)");
      var rowH = sample ? sample.getBoundingClientRect().height : 33;
      var free = scroll.clientHeight - table.tHead.getBoundingClientRect().height;
      return { rowH: rowH || 33, rows: Math.floor(free / (rowH || 33)) };
    }
    function fitRows() {
      fillers.forEach(function (row) { row.remove(); });
      fillers = [];
      tables.forEach(function (table) {
        var scroll = table.closest(".table-scroll");
        if (!scroll || table.hidden || !table.closest(".card-fill") || !table.tBodies[0]) return;
        var body = table.tBodies[0];
        var real = rowsOf(body).filter(function (r) { return !r.hidden && !r.classList.contains("grid-out"); }).length;
        var keys = grouped ? defaultOrder : full();
        var need = measure(scroll, table).rows;
        // A grouped table's columns are tinted by class (Test / Live / Main): the empty
        // rows carry the same classes so the tint runs to the foot, as in the reference.
        var sample = grouped ? rowsOf(body)[0] : null;
        for (var i = real; i < need; i++) {
          var row = document.createElement("tr");
          row.className = "filler-row";
          row.setAttribute("aria-hidden", "true");
          keys.forEach(function (k) {
            var td = document.createElement("td");
            td.setAttribute("data-col", k);
            var like = sample && sample.querySelector(':scope > [data-col="' + k + '"]');
            if (like) td.className = like.className;
            if (hidden.indexOf(k) !== -1) td.classList.add("col-hidden");
            row.appendChild(td);
          });
          body.appendChild(row);
          fillers.push(row);
        }
      });
    }
    var resizeTimer = null;
    window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(fitRows, 200); });

    // Pages with their own counts or filters (Users' Active filter, the Checklist's
    // tabs and search) listen for this and re-count; they call api.refresh() after
    // changing what's shown so the filler rows follow.
    function emit() {
      tables[0].dispatchEvent(new CustomEvent("dt-grid-change", { bubbles: true, detail: { key: key } }));
    }

    applyOrder();
    applyHidden();
    applyWidths();
    markHeaders();
    applySortFilter();
    fitRows();

    return {
      key: key,
      current: full,
      shown: shown,
      hidden: function () { return hidden.slice(); },
      measure: function () { var t = tables[0]; var s = t.closest(".table-scroll"); return s ? measure(s, t) : null; },
      refresh: function () { syncGear(); fitRows(); },
    };
  };
})();
