/* 高校通知聚合 · 静态站交互（纯展示：筛选 / 搜索 / 详情，无 AI 无采集） */
(function () {
  "use strict";

  var DATA = window.SITE_DATA || {};
  var NOTICES = DATA.notices || [];
  var LIMIT = 30;
  var state = { offset: 0, filtered: [], keyword: "", school: "", type: "", days: 0 };

  var $ = function (sel) { return document.querySelector(sel); };

  var TAG_COLORS = {
    "推免": "#e11d48", "预推免": "#ea580c", "夏令营": "#2563eb",
    "招生": "#16a34a", "复试": "#0891b2", "调剂": "#7c2d12",
    "公示": "#64748b", "通知": "#7c3aed",
  };
  function tagColor(name) { return TAG_COLORS[name] || "#94a3b8"; }
  function tint(color) { return "color-mix(in srgb, " + color + " 12%, #ffffff)"; }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function kwTokens() {
    return state.keyword.trim().split(/[\s,，、;；]+/).filter(function (w) {
      return w.length >= 2;
    });
  }
  function highlight(text) {
    var safe = esc(text);
    kwTokens().forEach(function (w) {
      var pattern = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      safe = safe.replace(new RegExp(pattern, "gi"), function (m) {
        return "<mark>" + m + "</mark>";
      });
    });
    return safe;
  }

  /* ---------- 初始化渲染 ---------- */
  function renderStatic() {
    var stats = DATA.stats || {};
    $("#statSchools").textContent = stats.schools || 0;
    $("#statNotices").textContent = stats.notices || 0;
    $("#statTypes").textContent = (stats.by_type || []).length;
    $("#statLast").textContent = stats.last_fetch ? stats.last_fetch.slice(0, 10) : "暂无";
    $("#siteMeta").textContent = "数据更新于 " + (DATA.generatedAt || "-");
    $("#footerGen").textContent = "生成于 " + (DATA.generatedAt || "-");

    var selSchool = $("#filterSchool");
    (stats.by_school || []).forEach(function (kv) {
      var opt = document.createElement("option");
      opt.value = kv[0];
      opt.textContent = kv[0] + "（" + kv[1] + "）";
      selSchool.appendChild(opt);
    });
    var selType = $("#filterType");
    (stats.by_type || []).forEach(function (kv) {
      var opt = document.createElement("option");
      opt.value = kv[0];
      opt.textContent = kv[0] + "（" + kv[1] + "）";
      selType.appendChild(opt);
    });
  }

  function renderDeadlines() {
    var list = DATA.deadlines || [];
    var strip = $("#deadlineStrip");
    var box = $("#deadlineList");
    box.innerHTML = "";
    if (!list.length) { strip.hidden = true; return; }
    list.slice(0, 12).forEach(function (n) {
      var el = document.createElement("button");
      el.className = "deadline-item";
      el.innerHTML =
        '<span class="deadline-date">' + esc(n.deadline) + "</span>" +
        '<span class="deadline-title">' + esc(n.title) + "</span>" +
        '<span class="deadline-school">' + esc(n.school) + "</span>";
      el.addEventListener("click", function () { openDetail(n.id); });
      box.appendChild(el);
    });
    strip.hidden = false;
  }

  /* ---------- 筛选与列表 ---------- */
  function applyFilter() {
    var kw = state.keyword.trim().toLowerCase();
    var tokens = kwTokens().map(function (w) { return w.toLowerCase(); });
    var today = (DATA.generatedAt || "");
    state.filtered = NOTICES.filter(function (n) {
      if (state.school && n.school !== state.school) return false;
      if (state.type && n.type !== state.type) return false;
      if (state.days) {
        if (!n.date) return false;
        if (new Date(today) - new Date(n.date) > state.days * 86400000) return false;
      }
      if (tokens.length) {
        var hay = (n.title + " " + n.excerpt + " " + n.school).toLowerCase();
        if (!tokens.every(function (t) { return hay.indexOf(t) !== -1; })) return false;
      }
      return true;
    });
    state.offset = 0;
    $("#noticeList").innerHTML = "";
    renderPage();
  }

  function renderPage() {
    var box = $("#noticeList");
    var page = state.filtered.slice(state.offset, state.offset + LIMIT);
    page.forEach(function (n) {
      var color = tagColor(n.type);
      var card = document.createElement("div");
      card.className = "notice-card";
      var chips = (n.highlights || []).map(function (h) {
        return '<span class="chip"><span class="chip-key">' + esc(h.key)
          + "</span>" + esc(h.value) + "</span>";
      }).join("");
      card.innerHTML =
        '<div class="card-meta">' +
        '<span class="tag" style="color:' + color + ";background:" + tint(color) + '">' + esc(n.type) + "</span>" +
        '<span class="card-school">' + esc(n.school) + "</span>" +
        '<span class="card-source">' + esc(n.source) + "</span>" +
        '<span class="card-date">' + (n.published ? "发布 " + esc(n.published) : "时间未知") + "</span>" +
        "</div>" +
        '<div class="card-title">' + highlight(n.title) + "</div>" +
        (chips ? '<div class="card-hl">' + chips + "</div>" : "") +
        (n.excerpt ? '<div class="card-excerpt">' + highlight(n.excerpt) + "</div>" : "") +
        '<div class="card-foot">' +
        '<span class="card-view">查看详情 →</span>' +
        '<a class="btn-source" href="' + esc(n.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">官方原文 ↗</a>' +
        "</div>";
      card.addEventListener("click", function () { openDetail(n.id); });
      box.appendChild(card);
    });
    state.offset += page.length;
    $("#resultTotal").textContent = "共 " + state.filtered.length + " 条";
    $("#btnMore").hidden = state.offset >= state.filtered.length;
    $("#listState").textContent =
      state.filtered.length ? "" : "没有符合条件的通知，换个筛选条件试试";
  }

  /* ---------- 详情 ---------- */
  function loadContentScript(id, onOk, onFail) {
    var s = document.createElement("script");
    s.src = "data/content/" + id + ".js";
    s.onload = onOk;
    s.onerror = onFail;
    document.body.appendChild(s);
  }

  function openDetail(id) {
    var n = null;
    for (var i = 0; i < NOTICES.length; i++) {
      if (NOTICES[i].id === id) { n = NOTICES[i]; break; }
    }
    if (!n) return;
    var color = tagColor(n.type);
    var chips = (n.highlights || []).map(function (h) {
      return '<span class="chip"><span class="chip-key">' + esc(h.key)
        + "</span>" + esc(h.value) + "</span>";
    }).join("");
    $("#modalTitle").textContent = n.title;
    $("#modalMeta").innerHTML =
      '<span class="tag" style="color:' + color + ";background:" + tint(color) + '">' + esc(n.type) + "</span>" +
      "<span>" + esc(n.school) + "</span>" +
      "<span>" + esc(n.source) + "</span>" +
      "<span>发布 " + esc(n.published || "未知") + "</span>" +
      (chips ? '<span class="modal-meta-chips">' + chips + "</span>" : "");
    $("#modalSource").href = n.url;
    var body = $("#modalBody");
    var render = function (content) {
      body.textContent = content || "（未抓取到正文快照，请点击上方官方原文查看）";
      $("#modal").hidden = false;
      document.body.style.overflow = "hidden";
    };
    if (window.SITE_CONTENT && window.SITE_CONTENT[String(id)]) {
      render(window.SITE_CONTENT[String(id)].content);
      return;
    }
    if (!n.has_content) { render(""); return; }
    body.textContent = "正在加载正文快照…";
    $("#modal").hidden = false;
    document.body.style.overflow = "hidden";
    loadContentScript(id, function () {
      var item = (window.SITE_CONTENT || {})[String(id)];
      body.textContent = (item && item.content) || "（未抓取到正文快照，请点击上方官方原文查看）";
    }, function () {
      body.textContent = "（正文快照加载失败，请点击上方官方原文查看）";
    });
  }

  function closeModal() {
    $("#modal").hidden = true;
    document.body.style.overflow = "";
  }

  /* ---------- 事件 ---------- */
  $("#btnSearch").addEventListener("click", applyFilter);
  $("#filterSchool").addEventListener("change", function () {
    state.school = this.value; applyFilter();
  });
  $("#filterType").addEventListener("change", function () {
    state.type = this.value; applyFilter();
  });
  $("#filterKeyword").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { state.keyword = this.value.trim(); applyFilter(); }
  });
  $("#btnMore").addEventListener("click", renderPage);
  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", function (e) {
    if (e.target === this) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#modal").hidden) closeModal();
  });
  document.querySelectorAll(".chip-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var days = Number(this.dataset.days);
      var active = state.days === days;
      state.days = active ? 0 : days;
      document.querySelectorAll(".chip-btn").forEach(function (b) {
        b.classList.toggle("active", !active && Number(b.dataset.days) === days);
      });
      applyFilter();
    });
  });

  /* ---------- 启动 ---------- */
  renderStatic();
  renderDeadlines();
  applyFilter();
})();
