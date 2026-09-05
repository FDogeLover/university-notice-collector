/* AI 助手：配置表单 + SSE 流式对话 */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };

  var SYSTEM_KEY = "uni_ai_system";
  var USEDB_KEY = "uni_ai_use_db";
  var HISTORY_KEY = "uni_ai_chat_history";
  var state = {
    messages: [],        // 多轮对话消息 [{role, content}]
    generating: false,   // 是否正在生成
    abort: null,         // AbortController，用于停止生成
    providers: {},       // 供应商预设
    hasKey: false,       // 是否已保存 Key（决定留空时是否允许）
    thinkTimer: null,    // 思考中秒数计时器
  };

  /* ---------- 小工具 ---------- */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function scrollBottom() {
    var m = $("#aiMessages");
    m.scrollTop = m.scrollHeight;
  }

  function hideWelcome() {
    var w = $("#aiMessages").querySelector(".ai-welcome");
    if (w) w.remove();
  }

  function renderMessage(role, text) {
    var row = document.createElement("div");
    row.className = "ai-msg " + (role === "user" ? "ai-msg-user" : "ai-msg-assistant");
    var bubble = document.createElement("div");
    bubble.className = "ai-bubble";
    bubble.textContent = text;
    row.appendChild(bubble);
    $("#aiMessages").appendChild(row);
    scrollBottom();
    return bubble;
  }

  /* 思考中指示器：等待首字期间显示，含已等待秒数 */
  function showThinking() {
    var row = document.createElement("div");
    row.className = "ai-msg ai-msg-assistant";
    row.id = "aiThinking";
    row.innerHTML =
      '<div class="ai-bubble ai-bubble-thinking">' +
      '<span class="ai-dots"><i></i><i></i><i></i></span>' +
      '<span class="ai-thinking-text">思考中</span>' +
      "</div>";
    $("#aiMessages").appendChild(row);
    var sec = 0;
    var label = row.querySelector(".ai-thinking-text");
    state.thinkTimer = setInterval(function () {
      sec += 1;
      if (label) label.textContent = "思考中（" + sec + "s）";
    }, 1000);
    scrollBottom();
  }

  function hideThinking() {
    clearInterval(state.thinkTimer);
    state.thinkTimer = null;
    var t = $("#aiThinking");
    if (t) t.remove();
  }

  function renderError(text) {
    var row = document.createElement("div");
    row.className = "ai-msg ai-msg-error";
    row.textContent = text;
    $("#aiMessages").appendChild(row);
    scrollBottom();
  }

  function autoResize() {
    var t = $("#aiInput");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 120) + "px";
  }

  /* 库内通知开关偏好 */
  function loadUseDb() {
    var on = localStorage.getItem(USEDB_KEY);
    if (on !== null) $("#aiUseDb").checked = on === "1";
  }

  /* ---------- 会话持久化（localStorage，刷新不丢） ---------- */
  function persistMessages() {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(state.messages));
    } catch (e) {
      // 超出存储限额：丢弃最旧一半再试一次
      var kept = state.messages.slice(Math.floor(state.messages.length / 2));
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(kept));
      } catch (e2) { /* 忽略 */ }
    }
  }

  function loadHistory() {
    var raw;
    try {
      raw = localStorage.getItem(HISTORY_KEY);
    } catch (e) {
      return;
    }
    if (!raw) return;
    var msgs;
    try {
      msgs = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(msgs) || !msgs.length) return;
    state.messages = msgs.filter(function (m) {
      return m && (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string" && m.content;
    });
    if (!state.messages.length) return;
    hideWelcome();
    state.messages.forEach(function (m) {
      renderMessage(m.role, m.content);
    });
  }

  /* ---------- 面板开关 ---------- */
  function openPanel() {
    $("#aiPanel").hidden = false;
    $("#btnAI").hidden = true;
    loadSystemPrompt();
    loadUseDb();
    $("#aiInput").focus();
  }

  function closePanel() {
    if (state.generating && state.abort) state.abort.abort();
    $("#aiPanel").hidden = true;
    $("#btnAI").hidden = false;
  }

  /* ---------- System Prompt ---------- */
  function loadSystemPrompt() {
    $("#aiSystem").value = localStorage.getItem(SYSTEM_KEY) || "";
  }

  /* ---------- 配置表单 ---------- */
  function openSettings() {
    $("#aiSettings").hidden = false;
    $("#aiTestResult").hidden = true;
    loadConfigForm();
  }

  function closeSettings() {
    $("#aiSettings").hidden = true;
  }

  function loadConfigForm() {
    fetch("/api/ai/config")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.providers = d.providers || {};
        var sel = $("#aiProvider");
        var html = '<option value="">自定义</option>';
        Object.keys(state.providers).forEach(function (k) {
          html += '<option value="' + k + '">' + esc(state.providers[k].name) + "</option>";
        });
        sel.innerHTML = html;

        var c = d.config || {};
        state.hasKey = !!c.has_key;
        if (c.provider && state.providers[c.provider]) sel.value = c.provider;
        else sel.value = "";

        // 用已保存配置回填（预设自动带出 Base URL / 模型 / 协议）
        $("#aiBaseUrl").value = c.base_url || "";
        $("#aiModel").value = c.model || "";
        $("#aiProtocol").value = c.protocol || "openai";
        if (sel.value) applyProviderPreset(sel.value);

        $("#aiApiKey").value = "";
        $("#aiApiKey").placeholder = c.has_key
          ? c.api_key_masked + "（已保存，留空则不修改）"
          : "sk-...";
      })
      .catch(function () { /* 忽略 */ });
  }

  function applyProviderPreset(key) {
    var p = state.providers[key];
    if (!p) return;
    $("#aiBaseUrl").value = p.base_url;
    $("#aiModel").value = p.model;
    $("#aiProtocol").value = p.protocol;
  }

  function formConfig() {
    return {
      provider: $("#aiProvider").value,
      base_url: $("#aiBaseUrl").value.trim(),
      api_key: $("#aiApiKey").value.trim(),
      model: $("#aiModel").value.trim(),
      protocol: $("#aiProtocol").value,
    };
  }

  function showTestResult(text, ok) {
    var box = $("#aiTestResult");
    box.hidden = false;
    box.className = "ai-test-result " + (ok ? "ok" : "fail");
    box.textContent = text;
  }

  /* ---------- 流式对话 ---------- */
  function handleFrame(frame, onDelta) {
    var eventType = "message";
    var dataLines = [];
    frame.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) eventType = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return null;
    var payload;
    try {
      payload = JSON.parse(dataLines.join("\n"));
    } catch (e) {
      return null;
    }
    if (eventType === "error") {
      return { error: payload.message || "请求出错，请稍后重试" };
    }
    if (payload.meta) {
      // 后端告知"已结合库内 N 条通知"：在气泡上方展示提示条
      var tip = document.createElement("div");
      tip.className = "ai-meta";
      tip.textContent = "📚 " + payload.meta;
      $("#aiMessages").appendChild(tip);
      scrollBottom();
      return null;
    }
    if (payload.delta && onDelta) onDelta(payload.delta);
    return null;
  }

  function readSSE(body, handlers) {
    var reader = body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    var fatal = null;
    function pump() {
      return reader.read().then(function (res) {
        if (fatal) {
          reader.cancel();
          throw { friendly: fatal };
        }
        if (res.done) return;
        buffer += decoder.decode(res.value, { stream: true });
        var frames = buffer.split("\n\n");
        buffer = frames.pop();
        for (var i = 0; i < frames.length; i++) {
          var err = handleFrame(frames[i], handlers.onDelta);
          if (err) {
            fatal = err.error;
            reader.cancel();
            throw { friendly: fatal };
          }
        }
        return pump();
      });
    }
    return pump();
  }

  function send() {
    if (state.generating) return;
    var text = $("#aiInput").value.trim();
    if (!text) return;
    hideWelcome();
    state.messages.push({ role: "user", content: text });
    renderMessage("user", text);
    persistMessages();
    $("#aiInput").value = "";
    autoResize();

    state.generating = true;
    $("#btnAISend").hidden = true;
    $("#btnAIStop").hidden = false;
    showThinking();

    var bubble = null;
    var received = "";
    state.abort = new AbortController();
    var system = $("#aiSystem").value.trim();
    var useDb = $("#aiUseDb").checked;
    localStorage.setItem(SYSTEM_KEY, system);
    localStorage.setItem(USEDB_KEY, useDb ? "1" : "0");

    function firstDelta() {
      // 收到首个增量：撤掉思考指示器，露出正文气泡
      if (bubble) return;
      hideThinking();
      bubble = renderMessage("assistant", "");
    }

    fetch("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.messages, system: system, use_db: useDb }),
      signal: state.abort.signal,
    })
      .then(function (r) {
        if (r.status === 400) {
          return r.json().then(function (d) {
            throw { friendly: d.error || "请求失败" };
          });
        }
        if (!r.ok) {
          throw { friendly: "服务异常（HTTP " + r.status + "），请稍后重试" };
        }
        if (!r.body) {
          throw { friendly: "当前浏览器不支持流式读取，请更换现代浏览器" };
        }
        return readSSE(r.body, {
          onDelta: function (chunk) {
            firstDelta();
            received += chunk;
            bubble.textContent += chunk;
            scrollBottom();
          },
        });
      })
      .then(function () {
        firstDelta();
        if (!received) {
          throw { friendly: "供应商返回了空回复，请稍后重试或换个问题" };
        }
        state.messages.push({ role: "assistant", content: received });
        persistMessages();
      })
      .catch(function (e) {
        if (e && e.name === "AbortError") {
          // 用户主动停止：保留已生成内容
          if (received) {
            state.messages.push({ role: "assistant", content: received });
            persistMessages();
          }
        } else if (e && e.friendly) {
          renderError(e.friendly);
        } else {
          renderError("请求失败：无法连接到后端服务，请检查服务是否运行");
        }
      })
      .finally(function () {
        hideThinking();
        state.generating = false;
        $("#btnAISend").hidden = false;
        $("#btnAIStop").hidden = true;
      });
  }

  /* ---------- 事件绑定 ---------- */
  $("#btnAI").addEventListener("click", openPanel);
  $("#btnAIClose").addEventListener("click", closePanel);
  $("#btnAISettings").addEventListener("click", openSettings);
  $("#btnAIClear").addEventListener("click", function () {
    if (state.generating && state.abort) state.abort.abort();
    state.messages = [];
    try {
      localStorage.removeItem(HISTORY_KEY);
    } catch (e) { /* 忽略 */ }
    $("#aiMessages").innerHTML = '<div class="ai-welcome">会话已清空，开始新的对话吧。</div>';
  });
  $("#aiSettingsClose").addEventListener("click", closeSettings);
  $("#aiSettings").addEventListener("click", function (e) {
    if (e.target === this) closeSettings();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#aiSettings").hidden) closeSettings();
  });

  $("#aiProvider").addEventListener("change", function () {
    applyProviderPreset(this.value);
  });

  $("#aiKeyToggle").addEventListener("click", function () {
    var input = $("#aiApiKey");
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    this.textContent = show ? "隐藏" : "显示";
  });

  $("#aiSystem").addEventListener("change", function () {
    localStorage.setItem(SYSTEM_KEY, this.value.trim());
  });

  $("#aiUseDb").addEventListener("change", function () {
    localStorage.setItem(USEDB_KEY, this.checked ? "1" : "0");
  });

  $("#aiInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("#aiInput").addEventListener("input", autoResize);
  $("#btnAISend").addEventListener("click", send);
  $("#btnAIStop").addEventListener("click", function () {
    if (state.abort) state.abort.abort();
  });

  $("#btnAITest").addEventListener("click", function () {
    var btn = this;
    var cfg = formConfig();
    if (!cfg.base_url || !cfg.model) {
      showTestResult("请先填写 Base URL 与模型名", false);
      return;
    }
    if (!cfg.api_key && !state.hasKey) {
      showTestResult("请先填写 API Key", false);
      return;
    }
    btn.disabled = true;
    btn.textContent = "测试中…";
    showTestResult("正在测试连接，请稍候…", true);
    fetch("/api/ai/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.errors) showTestResult(d.errors.join("；"), false);
        else showTestResult(d.message, d.ok);
      })
      .catch(function () {
        showTestResult("测试请求失败，请检查网络或后端服务", false);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "测试连接";
      });
  });

  $("#btnAISave").addEventListener("click", function () {
    var btn = this;
    var cfg = formConfig();
    if (!cfg.base_url || !cfg.model) {
      showTestResult("请填写 Base URL 与模型名", false);
      return;
    }
    if (!cfg.api_key && !state.hasKey) {
      showTestResult("请填写 API Key", false);
      return;
    }
    btn.disabled = true;
    btn.textContent = "保存中…";
    fetch("/api/ai/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.errors) {
          showTestResult(d.errors.join("；"), false);
        } else {
          state.hasKey = true;
          closeSettings();
          if (!state.messages.length) {
            var w = document.createElement("div");
            w.className = "ai-welcome";
            w.textContent = "配置完成，可以开始对话了。";
            $("#aiMessages").innerHTML = "";
            $("#aiMessages").appendChild(w);
          }
        }
      })
      .catch(function () {
        showTestResult("保存失败，请检查后端服务", false);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "保存";
      });
  });

  /* ---------- 初始化：恢复历史会话 ---------- */
  loadHistory();
})();
