/* 大学信息收集 - 操作界面交互 */
(function () {
  "use strict";

  var LIMIT = 30;
  var state = {
    offset: 0,
    total: 0,
    loading: false,
    school: "",
    type: "",
    keyword: "",
    days: 0,
    crawlTimer: null,
  };

  var $ = function (sel) { return document.querySelector(sel); };

  var TAG_COLORS = {
    "推免": "#e11d48",
    "预推免": "#ea580c",
    "夏令营": "#2563eb",
    "招生": "#16a34a",
    "复试": "#0891b2",
    "调剂": "#7c2d12",
    "公示": "#64748b",
    "通知": "#7c3aed",
  };

  function tagColor(name) {
    return TAG_COLORS[name] || "#94a3b8";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* 关键词高亮：先整体转义，再把命中的词包上 <mark> */
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

  /* ---------- 数据加载 ---------- */
  function loadStats() {
    fetch("/api/stats").then(function (r) { return r.json(); }).then(function (d) {
      $("#statSchools").textContent = d.schools;
      $("#statNotices").textContent = d.notices;
      $("#statTypes").textContent = (d.by_type || []).length;
      $("#statLast").textContent = d.last_fetch ? d.last_fetch.slice(0, 16) : "暂无";
    }).catch(function () { /* 忽略 */ });
  }

  function loadSchools() {
    fetch("/api/schools").then(function (r) { return r.json(); }).then(function (list) {
      var sel = $("#filterSchool");
      var cur = sel.value;
      sel.innerHTML = '<option value="">全部学校</option>';
      // 停用学校不出现在筛选下拉里
      list.filter(function (s) { return s.enabled; }).forEach(function (s) {
        var opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.name + "（" + s.notice_count + "）";
        sel.appendChild(opt);
      });
      if (cur) sel.value = cur;
    }).catch(function () { /* 忽略 */ });
  }

  function loadTypes() {
    fetch("/api/types").then(function (r) { return r.json(); }).then(function (list) {
      var sel = $("#filterType");
      var cur = sel.value;
      sel.innerHTML = '<option value="">全部类型</option>';
      list.forEach(function (t) {
        var opt = document.createElement("option");
        opt.value = t.name;
        opt.textContent = t.name + "（" + t.count + "）";
        sel.appendChild(opt);
      });
      if (cur) sel.value = cur;
    }).catch(function () { /* 忽略 */ });
  }

  /* ---------- 通知列表 ---------- */
  function loadNotices(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) {
      state.offset = 0;
      $("#noticeList").innerHTML = "";
    }
    var params = new URLSearchParams({
      school: state.school,
      type: state.type,
      keyword: state.keyword,
      days: state.days,
      limit: LIMIT,
      offset: state.offset,
    });
    $("#listState").textContent = "加载中…";
    fetch("/api/notices?" + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.loading = false;
        state.total = d.total;
        $("#resultTotal").textContent = "共 " + d.total + " 条";
        $("#listState").textContent = "";
        renderCards(d.items);
        state.offset += d.items.length;
        var more = state.offset < d.total;
        $("#btnMore").hidden = !more;
        if (!d.items.length && state.offset === 0) {
          $("#listState").textContent = "没有符合条件的通知，换个筛选条件试试";
        }
      })
      .catch(function () {
        state.loading = false;
        $("#listState").textContent = "加载失败，请刷新重试";
      });
  }

  /* 把 hex 类型色转成柔和徽章底色（12% 透明度叠加在白底上） */
  function tint(color) {
    return "color-mix(in srgb, " + color + " 12%, #ffffff)";
  }

  function renderCards(items) {
    var box = $("#noticeList");
    items.forEach(function (n) {
      var type = n.type_tag || "未分类";
      var color = tagColor(type);
      var card = document.createElement("div");
      card.className = "notice-card";

      var hl = (n.highlights || []).map(function (h) {
        return '<span class="chip">'
          + '<span class="chip-key">' + esc(h.key) + "</span>" + esc(h.value)
          + "</span>";
      }).join("");
      var excerpt = n.excerpt
        ? '<div class="card-excerpt">' + highlight(n.excerpt) + "</div>" : "";

      card.innerHTML =
        '<div class="card-meta">' +
        '<span class="tag" style="color:' + color + ";background:" + tint(color) + '">' + esc(type) + "</span>" +
        '<span class="card-school">' + esc(n.school_name) + "</span>" +
        '<span class="card-source">' + esc(n.source_name || "") + "</span>" +
        '<span class="card-date">' + (n.published_at ? "发布 " + esc(n.published_at) : "时间未知") + "</span>" +
        "</div>" +
        '<div class="card-title">' + highlight(n.title) + "</div>" +
        (hl ? '<div class="card-hl">' + hl + "</div>" : "") +
        excerpt +
        '<div class="card-foot">' +
        '<span class="card-view">查看详情 →</span>' +
        '<a class="btn-source" href="' + esc(n.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">官方原文 ↗</a>' +
        "</div>";
      card.addEventListener("click", function () { openDetail(n.id); });
      box.appendChild(card);
    });
  }

  /* ---------- 即将截止 ---------- */
  function loadDeadlines() {
    fetch("/api/deadlines?days=30").then(function (r) { return r.json(); })
      .then(function (list) {
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
            '<span class="deadline-school">' + esc(n.school_name) + "</span>";
          el.addEventListener("click", function () { openDetail(n.id); });
          box.appendChild(el);
        });
        strip.hidden = false;
      }).catch(function () { /* 忽略 */ });
  }

  /* ---------- 详情 ---------- */
  function openDetail(id) {
    fetch("/api/notices/" + id).then(function (r) { return r.json(); }).then(function (n) {
      if (!n || n.error) return;
      var color = tagColor(n.type_tag || "未分类");
      var metaChips = (n.meta || []).map(function (h) {
        return '<span class="chip"><span class="chip-key">' + esc(h.key)
          + "</span>" + esc(h.value) + "</span>";
      }).join("");
      $("#modalTitle").textContent = n.title;
      $("#modalMeta").innerHTML =
        '<span class="tag" style="color:' + color + ";background:" + tint(color) + '">' + esc(n.type_tag || "未分类") + "</span>" +
        "<span>" + esc(n.school_name) + "</span>" +
        "<span>" + esc(n.source_name || "") + "</span>" +
        "<span>发布 " + esc(n.published_at || "未知") + "</span>" +
        "<span>抓取 " + esc((n.fetched_at || "").slice(0, 16)) + "</span>" +
        (metaChips ? '<span class="modal-meta-chips">' + metaChips + "</span>" : "");
      var src = $("#modalSource");
      src.href = n.url;
      src.textContent = "打开官方原文 ↗";
      $("#modalBody").textContent = n.content_md || "（未抓取到正文快照，请点击上方官方原文查看）";
      $("#modal").hidden = false;
      document.body.style.overflow = "hidden";
    }).catch(function () { /* 忽略 */ });
  }

  function closeModal() {
    $("#modal").hidden = true;
    document.body.style.overflow = "";
  }

  /* ---------- 采集（含实时进度） ---------- */
  function triggerCrawl() {
    var btn = $("#btnCrawl");
    btn.disabled = true;
    fetch("/api/crawl?max_items=60", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $("#crawlState").textContent = d.message;
        if (d.started) {
          $("#crawlPanel").hidden = false;
          $("#crawlLog").textContent = "正在启动采集任务…";
          pollCrawl();
        } else {
          btn.disabled = false;
        }
      })
      .catch(function () {
        btn.disabled = false;
        $("#crawlState").textContent = "启动失败";
      });
  }

  function pollCrawl() {
    clearInterval(state.crawlTimer);
    state.crawlTimer = setInterval(function () {
      fetch("/api/crawl/status").then(function (r) { return r.json(); }).then(function (s) {
        var log = s.log || [];
        $("#crawlLog").textContent = log.join("\n");
        $("#crawlLog").scrollTop = $("#crawlLog").scrollHeight;
        if (s.running) {
          $("#crawlState").textContent = "采集中…";
          $("#crawlPanelStatus").textContent =
            "运行中" + (s.started_at ? "（" + s.started_at + "）" : "");
          $("#btnCrawl").disabled = true;
        } else {
          clearInterval(state.crawlTimer);
          $("#crawlState").textContent = "采集完成 " + (s.last || "");
          $("#crawlPanelStatus").textContent = "已完成 " + (s.last || "");
          $("#btnCrawl").disabled = false;
          loadStats(); loadSchools(); loadTypes(); loadNotices(true);
        }
      }).catch(function () { /* 忽略 */ });
    }, 1500);
  }

  /* ---------- 事件绑定 ---------- */
  $("#btnSearch").addEventListener("click", function () { applyFilter(); });
  $("#filterSchool").addEventListener("change", function () {
    state.school = this.value; loadNotices(true);
  });
  $("#filterType").addEventListener("change", function () {
    state.type = this.value; loadNotices(true);
  });
  $("#filterKeyword").addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyFilter();
  });
  $("#btnMore").addEventListener("click", function () { loadNotices(false); });
  $("#btnCrawl").addEventListener("click", triggerCrawl);
  document.querySelectorAll(".chip-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var days = Number(this.dataset.days);
      var active = state.days === days;
      state.days = active ? 0 : days;
      document.querySelectorAll(".chip-btn").forEach(function (b) {
        b.classList.toggle("active",
          !active && Number(b.dataset.days) === days);
      });
      loadNotices(true);
    });
  });
  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", function (e) {
    if (e.target === this) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#modal").hidden) closeModal();
  });

  function applyFilter() {
    state.school = $("#filterSchool").value;
    state.type = $("#filterType").value;
    state.keyword = $("#filterKeyword").value.trim();
    loadNotices(true);
  }

  /* ---------- 学校管理（停用 / 启用 / 删除） ---------- */
  function refreshAll() {
    loadStats(); loadSchools(); loadTypes(); loadNotices(true);
  }

  function renderManageList() {
    fetch("/api/schools").then(function (r) { return r.json(); }).then(function (list) {
      var box = $("#msList");
      box.innerHTML = "";
      list.forEach(function (s) {
        var row = document.createElement("div");
        row.className = "ms-row" + (s.enabled ? "" : " ms-disabled");
        row.innerHTML =
          '<div class="ms-info">' +
          '<span class="ms-name">' + esc(s.name) + "</span>" +
          '<span class="ms-sub">' + s.notice_count + " 条通知 · " + s.source_count
            + " 个栏目" + (s.enabled ? "" : " · 已停用") + "</span></div>" +
          '<div class="ms-actions">' +
          '<button class="btn btn-ghost ms-toggle">' + (s.enabled ? "停用" : "启用") + "</button>" +
          '<button class="btn btn-ghost ms-del">删除</button></div>';
        row.querySelector(".ms-toggle").addEventListener("click", function () {
          fetch("/api/schools/" + s.id + "/enabled", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: !s.enabled }),
          }).then(function () { renderManageList(); refreshAll(); });
        });
        row.querySelector(".ms-del").addEventListener("click", function () {
          var tip = "确定彻底删除「" + s.name + "」？其 " + s.notice_count
            + " 条通知与栏目配置将一并移除，不可恢复。";
          if (!window.confirm(tip)) return;
          fetch("/api/schools/" + s.id, { method: "DELETE" })
            .then(function () { renderManageList(); refreshAll(); });
        });
        box.appendChild(row);
      });
    }).catch(function () { /* 忽略 */ });
  }

  function openManage() {
    renderManageList();
    $("#manageSchoolsModal").hidden = false;
  }

  function closeManage() {
    $("#manageSchoolsModal").hidden = true;
  }

  $("#btnManageSchools").addEventListener("click", openManage);
  $("#manageSchoolsClose").addEventListener("click", closeManage);
  $("#manageSchoolsModal").addEventListener("click", function (e) {
    if (e.target === this) closeManage();
  });

  /* ---------- 添加学校 ---------- */
  var CATEGORIES = ["招生", "通知公告", "信息公开"];

  function addSourceRow(src) {
    var box = $("#asSources");
    var row = document.createElement("div");
    row.className = "as-source-row";
    row.innerHTML =
      '<input class="input as-src-name" type="text" placeholder="栏目名称（如 研究生招生网）" autocomplete="off">' +
      '<input class="input as-src-url" type="text" placeholder="列表页 URL（https://...）" autocomplete="off">' +
      '<select class="select as-src-cat">' +
      CATEGORIES.map(function (c) {
        return '<option value="' + c + '">' + c + "</option>";
      }).join("") +
      "</select>" +
      '<button class="btn btn-ghost as-src-del" type="button" title="删除栏目">删除</button>';
    row.querySelector(".as-src-del").addEventListener("click", function () {
      row.remove();
    });
    if (src) {
      row.querySelector(".as-src-name").value = src.name || "";
      row.querySelector(".as-src-url").value = src.url || "";
      if (src.category) row.querySelector(".as-src-cat").value = src.category;
    }
    box.appendChild(row);
  }

  function openAddSchool() {
    $("#addSchoolModal").hidden = false;
    $("#asAiHint").value = "";
    $("#asName").value = "";
    $("#asDomain").value = "";
    $("#asResult").hidden = true;
    $("#asAiLoading").hidden = true;
    $("#asSources").innerHTML = "";
    addSourceRow();
    $("#asAiHint").focus();
  }

  function closeAddSchool() {
    $("#addSchoolModal").hidden = true;
  }

  function showAsResult(text, ok) {
    var box = $("#asResult");
    box.hidden = false;
    box.className = "ai-test-result " + (ok ? "ok" : "fail");
    box.textContent = text;
  }

  $("#btnAddSchool").addEventListener("click", openAddSchool);
  $("#addSchoolClose").addEventListener("click", closeAddSchool);
  $("#addSchoolModal").addEventListener("click", function (e) {
    if (e.target === this) closeAddSchool();
  });
  $("#asAddSource").addEventListener("click", function () { addSourceRow(); });

  /* AI 智能填写：生成信息并回填表单，用户核对后可改 */
  $("#btnAsAi").addEventListener("click", function () {
    var btn = this;
    var hint = $("#asAiHint").value.trim();
    if (!hint) { showAsResult("请先输入学校名称（如：四川大学）", false); return; }
    $("#asResult").hidden = true;
    $("#asAiLoading").hidden = false;
    btn.disabled = true;
    btn.textContent = "填写中…";
    fetch("/api/schools/ai-hint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hint: hint }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.errors) {
          showAsResult(d.errors.join("；"), false);
          return;
        }
        var s = d.school;
        $("#asName").value = s.name || "";
        $("#asDomain").value = s.domain || "";
        $("#asSources").innerHTML = "";
        (s.sources || []).forEach(function (src) { addSourceRow(src); });
        if (!s.sources || !s.sources.length) addSourceRow();
        showAsResult("AI 已生成，请核对信息后保存；不确定的栏目可修改或删除", true);
      })
      .catch(function () {
        showAsResult("AI 填写失败，请检查后端服务与 AI 配置", false);
      })
      .finally(function () {
        $("#asAiLoading").hidden = true;
        btn.disabled = false;
        btn.textContent = "AI 智能填写";
      });
  });

  $("#btnAsSave").addEventListener("click", function () {
    var btn = this;
    var name = $("#asName").value.trim();
    var domain = $("#asDomain").value.trim();
    var sources = [];
    var rows = $("#asSources").querySelectorAll(".as-source-row");
    rows.forEach(function (row) {
      var sname = row.querySelector(".as-src-name").value.trim();
      var surl = row.querySelector(".as-src-url").value.trim();
      var scat = row.querySelector(".as-src-cat").value;
      if (sname || surl) {
        sources.push({ name: sname, url: surl, category: scat });
      }
    });
    if (!name) { showAsResult("请填写学校名称", false); return; }
    if (!domain) { showAsResult("请填写官方域名", false); return; }
    if (!sources.length) { showAsResult("请至少填写一个栏目", false); return; }
    btn.disabled = true;
    btn.textContent = "保存中…";
    fetch("/api/schools", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, domain: domain, sources: sources }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.errors) {
          showAsResult(d.errors.join("；"), false);
        } else {
          closeAddSchool();
          loadStats();
          loadSchools();
          loadTypes();
          loadNotices(true);
          // 后端已自动启动该校采集：打开进度面板并轮询
          if (d.crawl && d.crawl.started) {
            $("#crawlPanel").hidden = false;
            $("#crawlLog").textContent = "正在启动自动采集…";
            pollCrawl();
          }
        }
      })
      .catch(function () {
        showAsResult("保存失败，请检查后端服务", false);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "保存";
      });
  });

  /* ---------- 初始化 ---------- */
  loadStats();
  loadSchools();
  loadTypes();
  loadNotices(true);
  loadDeadlines();
})();