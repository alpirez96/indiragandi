(function () {
  "use strict";

  var SUPPORTED = [
    "youtube.com", "youtu.be", "youtube-nocookie.com", "instagram.com", "instagr.am",
    "tiktok.com", "x.com", "twitter.com", "facebook.com", "fb.watch", "reddit.com",
    "redd.it", "vimeo.com", "threads.net", "threads.com", "pinterest.com", "pin.it",
    "dailymotion.com", "dai.ly", "twitch.tv"
  ];

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var form = $("#form");
  var input = $("#url");
  var submitBtn = $("#submit");
  var pasteBtn = $("#paste");
  var clearBtn = $("#clear");
  var formMsg = $("#formMsg");
  var result = $("#result");
  var hero = $(".hero");

  var ua = navigator.userAgent;
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isTouch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  var canStream = typeof window.ReadableStream === "function" && typeof window.fetch === "function";

  var state = { data: null, item: 0, busy: false, controller: null, objectUrl: null };

  // ---------- yardımcılar ----------
  function extractUrl(text) {
    text = (text || "").trim();
    var m = text.match(/https?:\/\/[^\s<>"']+/i);
    if (m) return m[0].replace(/[).,;!]+$/, "");
    m = text.match(/(?:[a-z0-9-]+\.)+[a-z]{2,}\/[^\s<>"']*/i);
    return m ? "https://" + m[0].replace(/[).,;!]+$/, "") : "";
  }

  function hostOk(url) {
    try {
      var h = new URL(url).hostname.toLowerCase();
      return SUPPORTED.some(function (d) { return h === d || h.slice(-(d.length + 1)) === "." + d; });
    } catch (e) { return false; }
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return "";
    if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + " KB";
    var mb = n / (1024 * 1024);
    return (mb < 10 ? mb.toFixed(1) : Math.round(mb)).toString().replace(".", ",") + " MB";
  }

  function fmtDuration(s) {
    if (!s) return "";
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    var mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
  }

  function showFormMsg(text) {
    formMsg.textContent = text;
    formMsg.hidden = !text;
  }

  function setLoading(on) {
    submitBtn.classList.toggle("is-loading", on);
    submitBtn.disabled = on;
    $(".btn-label", submitBtn).textContent = on ? "Aranıyor…" : "Videoyu bul";
  }

  function syncFieldButtons() {
    var has = input.value.trim().length > 0;
    clearBtn.hidden = !has;
    pasteBtn.hidden = has || !(navigator.clipboard && navigator.clipboard.readText);
  }

  function parseFilename(res, fallback) {
    var cd = res.headers.get("content-disposition") || "";
    var m = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (m) { try { return decodeURIComponent(m[1]); } catch (e) { /* yoksay */ } }
    m = cd.match(/filename="?([^";]+)"?/i);
    return m ? m[1] : fallback;
  }

  async function readError(res) {
    try {
      var j = await res.json();
      return j.error || "Bir şeyler ters gitti.";
    } catch (e) {
      if (res.status === 504) return "İşlem zaman aşımına uğradı. Daha düşük bir çözünürlük deneyin.";
      return "Sunucu hatası (" + res.status + "). Tekrar deneyin.";
    }
  }

  // ---------- arama ----------
  async function lookup(raw) {
    var url = extractUrl(raw);
    showFormMsg("");
    if (!url) { showFormMsg("Önce bir video bağlantısı yapıştır."); input.focus(); return; }
    if (!hostOk(url)) { showFormMsg("Bu site desteklenmiyor. Instagram, YouTube, TikTok veya X bağlantısı dene."); return; }
    if (input.value.trim() !== url) input.value = url;
    syncFieldButtons();

    if (state.controller) state.controller.abort();
    setLoading(true);
    input.blur();
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 90000);
    try {
      var res = await fetch("/api/info?url=" + encodeURIComponent(url), { signal: ctrl.signal });
      if (!res.ok) { showFormMsg(await readError(res)); return; }
      var data = await res.json();
      state.data = data;
      state.item = 0;
      render();
      try { history.replaceState(null, "", "?url=" + encodeURIComponent(url)); } catch (e) { /* yoksay */ }
    } catch (e) {
      showFormMsg(e && e.name === "AbortError"
        ? "Platform yanıt vermedi. Birkaç saniye sonra tekrar dene."
        : "Bağlantı kurulamadı. İnternetini kontrol edip tekrar dene.");
    } finally {
      clearTimeout(timer);
      setLoading(false);
    }
  }

  // ---------- sonuç kartı ----------
  function render() {
    var data = state.data;
    if (!data) return;
    var item = data.items[state.item];
    result.innerHTML = "";
    var node = document.getElementById("tpl-result").content.cloneNode(true);
    var card = $(".card", node);

    var img = $(".thumb img", card);
    if (item.thumbnail) {
      img.src = item.thumbnail;
      img.onerror = function () { img.hidden = true; };
    } else {
      img.hidden = true;
    }
    $(".tag", card).textContent = data.platform;
    $(".title", card).textContent = item.title || "Video";
    var sub = [item.uploader, fmtDuration(item.duration)].filter(Boolean).join(" · ");
    $(".sub", card).textContent = sub;

    if (data.items.length > 1) {
      var tabs = $(".items", card);
      tabs.hidden = false;
      data.items.forEach(function (it, i) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", String(i === state.item));
        b.textContent = (i + 1) + ". video";
        b.addEventListener("click", function () {
          if (state.busy) return;
          state.item = i;
          render();
        });
        tabs.appendChild(b);
      });
    }

    var opts = $(".options", card);
    item.options.forEach(function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "opt" + (o.kind === "audio" ? " audio" : "");
      var title = document.createElement("b");
      title.textContent = o.label;
      var small = document.createElement("small");
      var parts = [o.ext.toUpperCase()];
      if (o.size) parts.push("~" + fmtBytes(o.size));
      if (o.silent) parts.push("sessiz");
      if (o.tooBig) parts = ["çok büyük"];
      small.textContent = parts.join(" · ");
      b.appendChild(title);
      b.appendChild(small);
      if (o.tooBig) b.disabled = true;
      b.addEventListener("click", function () { startDownload(item, o, b); });
      opts.appendChild(b);
    });

    $(".reset", card).addEventListener("click", reset);
    result.appendChild(node);
    result.hidden = false;
    hero.classList.add("has-result");
    requestAnimationFrame(function () {
      result.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function reset() {
    if (state.controller) state.controller.abort();
    revokeUrl();
    state.data = null;
    state.busy = false;
    result.hidden = true;
    result.innerHTML = "";
    hero.classList.remove("has-result");
    input.value = "";
    syncFieldButtons();
    try { history.replaceState(null, "", location.pathname); } catch (e) { /* yoksay */ }
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(function () { input.focus(); }, 300);
  }

  function revokeUrl() {
    if (state.objectUrl) { URL.revokeObjectURL(state.objectUrl); state.objectUrl = null; }
  }

  // ---------- indirme ----------
  function statusEls() {
    var card = $(".card", result);
    return {
      card: card,
      box: $(".status", card),
      bar: $(".bar", card),
      fill: $(".bar span", card),
      text: $(".status-text", card),
      actions: $(".status-actions", card),
      hint: $(".hint", card),
      options: $(".options", card)
    };
  }

  function downloadUrl(item, o) {
    var q = "url=" + encodeURIComponent(state.data.url) + "&q=" + encodeURIComponent(o.q);
    if (item.index) q += "&i=" + item.index;
    return "/api/download?" + q;
  }

  function finish(els, btn) {
    state.busy = false;
    state.controller = null;
    els.options.classList.remove("is-busy");
    btn.classList.remove("is-active");
  }

  async function startDownload(item, o, btn) {
    if (state.busy) return;
    var els = statusEls();
    var url = downloadUrl(item, o);
    revokeUrl();

    state.busy = true;
    els.options.classList.add("is-busy");
    btn.classList.add("is-active");
    els.box.hidden = false;
    els.actions.innerHTML = "";
    els.hint.hidden = true;
    els.text.classList.remove("error");
    els.bar.className = "bar indeterminate";
    els.fill.style.width = "";

    // Akış desteklenmiyorsa tarayıcının kendi indiricisine bırak
    if (!canStream) {
      els.text.textContent = "İndirme tarayıcında başlayacak. Hazırlanması 10–60 sn sürebilir.";
      var a = document.createElement("a");
      a.href = url;
      a.setAttribute("download", "");
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { finish(els, btn); els.bar.className = "bar done"; }, 4000);
      return;
    }

    var started = Date.now();
    var tick = setInterval(function () {
      var s = Math.round((Date.now() - started) / 1000);
      els.text.textContent = "Sunucuda hazırlanıyor… " + s + " sn";
    }, 1000);
    els.text.textContent = "Sunucuda hazırlanıyor…";

    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-light";
    cancel.textContent = "İptal";
    els.actions.appendChild(cancel);

    var ctrl = new AbortController();
    state.controller = ctrl;
    cancel.addEventListener("click", function () { ctrl.abort(); });

    try {
      var res = await fetch(url, { signal: ctrl.signal });
      clearInterval(tick);
      if (!res.ok) throw new Error(await readError(res));

      var total = parseInt(res.headers.get("content-length") || "0", 10);
      var type = (res.headers.get("content-type") || "video/mp4").split(";")[0];
      var name = parseFilename(res, "video." + o.ext);
      var reader = res.body.getReader();
      var chunks = [];
      var got = 0;
      els.bar.className = "bar";
      for (;;) {
        var step = await reader.read();
        if (step.done) break;
        chunks.push(step.value);
        got += step.value.length;
        if (total) {
          var pct = Math.min(100, Math.round((got / total) * 100));
          els.fill.style.width = pct + "%";
          els.text.textContent = "İndiriliyor… %" + pct + " · " + fmtBytes(got) + " / " + fmtBytes(total);
        } else {
          els.text.textContent = "İndiriliyor… " + fmtBytes(got);
        }
      }

      var blob = new Blob(chunks, { type: type });
      chunks = null;
      els.bar.className = "bar done";
      els.text.textContent = "Hazır: " + name + " (" + fmtBytes(blob.size) + ")";
      showSaveActions(els, blob, name, type);
    } catch (e) {
      clearInterval(tick);
      els.bar.className = "bar";
      els.fill.style.width = "0";
      els.actions.innerHTML = "";
      els.text.classList.add("error");
      els.text.textContent = e && e.name === "AbortError"
        ? "İndirme iptal edildi."
        : (e && e.message && e.message !== "Failed to fetch" && e.message !== "Load failed"
          ? e.message
          : "Bağlantı koptu. Tekrar dene.");
    } finally {
      finish(els, btn);
    }
  }

  function showSaveActions(els, blob, name, type) {
    els.actions.innerHTML = "";
    state.objectUrl = URL.createObjectURL(blob);

    var save = document.createElement("a");
    save.className = "btn btn-dark";
    save.href = state.objectUrl;
    save.download = name;
    save.textContent = "Kaydet";
    els.actions.appendChild(save);

    var file = null;
    try { file = new File([blob], name, { type: type }); } catch (e) { file = null; }
    var canShareFile = !!(file && navigator.canShare && navigator.share && navigator.canShare({ files: [file] }));

    var shareShown = false;
    if (canShareFile && isTouch) {
      shareShown = true;
      var share = document.createElement("button");
      share.type = "button";
      share.className = "btn btn-yellow";
      share.textContent = type.indexOf("video") === 0 ? "Paylaş / Fotoğraflar’a kaydet" : "Paylaş";
      share.addEventListener("click", function () {
        navigator.share({ files: [file], title: name }).catch(function () { /* kullanıcı vazgeçti */ });
      });
      els.actions.appendChild(share);
    }

    if (isIOS) {
      els.hint.hidden = false;
      if (type.indexOf("video") !== 0) {
        els.hint.textContent = "“Kaydet” dosyayı Dosyalar › İndirilenler’e kaydeder.";
      } else if (shareShown) {
        els.hint.textContent = "“Kaydet” dosyayı Dosyalar › İndirilenler’e kaydeder. Fotoğraflar’a eklemek için “Paylaş”a dokunup “Videoyu Kaydet”i seç.";
      } else {
        els.hint.textContent = "“Kaydet” dosyayı Dosyalar › İndirilenler’e kaydeder. Fotoğraflar’a eklemek için Dosyalar’da videoyu aç, “Paylaş” → “Videoyu Kaydet”.";
      }
    } else if (!isTouch) {
      // Masaüstünde dosyayı kendiliğinden kaydet
      save.click();
    }
  }

  // ---------- olaylar ----------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    lookup(input.value);
  });

  input.addEventListener("input", function () { showFormMsg(""); syncFieldButtons(); });
  input.addEventListener("paste", function () {
    setTimeout(function () {
      syncFieldButtons();
      var u = extractUrl(input.value);
      if (u && hostOk(u)) lookup(u);
    }, 0);
  });

  clearBtn.addEventListener("click", function () {
    input.value = "";
    syncFieldButtons();
    input.focus();
  });

  pasteBtn.addEventListener("click", async function () {
    try {
      var text = await navigator.clipboard.readText();
      if (text) {
        input.value = extractUrl(text) || text.trim();
        syncFieldButtons();
        lookup(input.value);
      }
    } catch (e) {
      input.focus();
      showFormMsg("Panoya erişilemedi. Bağlantıyı kutuya uzun basıp yapıştır.");
    }
  });

  syncFieldButtons();

  // ?url=... ile gelindiyse doğrudan ara (iOS Kestirmeler / paylaşım için)
  try {
    var preset = new URLSearchParams(location.search).get("url");
    if (preset) { input.value = preset; syncFieldButtons(); lookup(preset); }
  } catch (e) { /* yoksay */ }
})();
