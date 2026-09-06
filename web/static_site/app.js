/* 高校通知聚合 · 静态站交互（纯展示：筛选 / 搜索 / 详情，无 AI 无采集） */
(function () {
  "use strict";

  var DATA = window.SITE_DATA || {};
  var NOTICES = DATA.notices || [];
  var LIMIT = 30;
  var state = {
    offset: 0, filtered: [], keyword: "", school: "", type: "",
    tag: "", stype: "", days: 0, highlightWords: [],
  };

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
    (state.highlightWords || kwTokens()).forEach(function (w) {
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

  /* ---------- 即将截止（超出可视宽度时自动横向滚动轮播，悬停暂停） ---------- */
  function deadlineItemHtml(n) {
    return '<button class="deadline-item" data-id="' + n.id + '">' +
      '<span class="deadline-date">' + esc(n.deadline) + "</span>" +
      '<span class="deadline-title">' + esc(n.title) + "</span>" +
      '<span class="deadline-school">' + esc(n.school) + "</span>" +
      "</button>";
  }

  function renderDeadlines() {
    var list = DATA.deadlines || [];
    var strip = $("#deadlineStrip");
    var box = $("#deadlineList");
    var track = $("#deadlineTrack");
    if (!list.length) { strip.hidden = true; return; }
    var half = list.map(deadlineItemHtml).join("");
    track.innerHTML = '<div class="deadline-half">' + half + '</div>' +
                      '<div class="deadline-half">' + half + "</div>";
    $("#deadlineLabel").textContent =
      "⏰ 即将截止（" + list.length + " 条）";
    track.querySelectorAll(".deadline-item").forEach(function (el, i) {
      el.addEventListener("click",
        function () { openDetail(list[i % list.length].id); });
    });
    strip.hidden = false;
    requestAnimationFrame(function () {
      var halfEl = track.children[0];
      var overflow = halfEl.scrollWidth > box.clientWidth + 1;
      track.classList.toggle("marquee", overflow);
      if (overflow) {
        var dur = Math.max(15, Math.round(halfEl.scrollWidth / 40));
        track.style.setProperty("--marquee-dur", dur + "s");
      }
    });
  }

  /* ---------- 筛选与列表 ---------- */
  /* 分组搜索（与主站同语义）：学校简称/全名各自成组（组内 OR），
     其余词合并成一组（组内 OR），组间 AND */
  function buildGroups() {
    var abbrs = DATA.abbrs || {};
    var names = (DATA.stats.by_school || []).map(function (kv) { return kv[0]; });
    var rest = state.keyword;
    var groups = [], words = [];
    Object.keys(abbrs).forEach(function (ab) {
      if (rest.indexOf(ab) !== -1) {
        rest = rest.split(ab).join(" ");
        groups.push([ab, abbrs[ab]]);
        words.push(ab, abbrs[ab]);
      }
    });
    names.forEach(function (n) {
      if (rest.indexOf(n) !== -1) {
        rest = rest.split(n).join(" ");
        groups.push([n]);
        words.push(n);
      }
    });
    var other = [];
    rest.split(/[\s,，、;；]+/).forEach(function (w) {
      if (w.length >= 2 && words.indexOf(w) === -1 && other.indexOf(w) === -1) {
        other.push(w);
      }
    });
    if (other.length) {
      groups.push(other);
      words = words.concat(other);
    }
    return { groups: groups, words: words };
  }

  function applyFilter() {
    // 与主站一致：每次筛选都从控件读取当前值
    state.school = $("#filterSchool").value;
    state.type = $("#filterType").value;
    state.tag = $("#filterTag").value;
    state.stype = $("#filterStype").value;
    state.keyword = $("#filterKeyword").value.trim();
    var built = buildGroups();
    var groups = built.groups;
    state.highlightWords = built.words;
    var schoolTags = DATA.schoolTags || {};
    var today = (DATA.generatedAt || "");
    state.filtered = NOTICES.filter(function (n) {
      if (state.school && n.school !== state.school) return false;
      if (state.type && n.type !== state.type) return false;
      if (state.stype && (n.stype || "研究生教育") !== state.stype) return false;
      if (state.tag) {
        var stags = (schoolTags[n.school] || "").split(",");
        if (stags.indexOf(state.tag) === -1) return false;
      }
      if (state.days) {
        if (!n.date) return false;
        if (new Date(today) - new Date(n.date) > state.days * 86400000) return false;
      }
      if (groups.length) {
        var hay = (n.title + " " + n.excerpt + " " + n.school).toLowerCase();
        var allHit = groups.every(function (g) {
          return g.some(function (t) { return hay.indexOf(t.toLowerCase()) !== -1; });
        });
        if (!allHit) return false;
      }
      return true;
    });
    state.offset = 0;
    $("#noticeList").innerHTML = "";
    renderPage();
  }

  function schoolBadges(tags) {
    return (tags || "").split(",").filter(Boolean).map(function (t) {
      return '<span class="school-tag school-tag-' + t + '">' + t + "</span>";
    }).join("");
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
        schoolBadges(n.school_tags) +
        (n.stype && n.stype !== "研究生教育"
          ? '<span class="domain-tag">' + esc(n.stype) + "</span>" : "") +
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
  $("#filterTag").addEventListener("change", applyFilter);
  $("#filterSchool").addEventListener("change", applyFilter);
  $("#filterType").addEventListener("change", applyFilter);
  $("#filterStype").addEventListener("change", applyFilter);
  $("#filterKeyword").addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyFilter();
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
