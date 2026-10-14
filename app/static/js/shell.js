// App shell: sidebar, light/dark theme, keyboard shortcuts, Ctrl+K, AI assistant drawer, toasts, confirm dialog.

// Parse a shortcut string "Alt+T", "Ctrl+Shift+K" → a KeyboardEvent matcher.
function parseShortcut(text) {
  const parts = String(text || "").split("+").map((p) => p.trim().toLowerCase());
  const key = parts.pop();
  return { alt: parts.includes("alt"), ctrl: parts.includes("ctrl"), shift: parts.includes("shift"), meta: parts.includes("meta"), key };
}

function matchShortcut(sc, e) {
  if (!sc || !sc.key) return false;
  if (sc.alt !== e.altKey || sc.shift !== e.shiftKey) return false;
  const mod = e.ctrlKey || e.metaKey;
  if (sc.ctrl !== mod) return false;
  if (sc.key.length === 1 && /[a-z]/.test(sc.key)) return e.code === `Key${sc.key.toUpperCase()}`;
  if (sc.key.length === 1 && /[0-9]/.test(sc.key)) return e.code === `Digit${sc.key}`;
  return e.key.toLowerCase() === sc.key;
}

function isTyping(e) {
  const t = e.target;
  const tag = (t.tagName || "").toLowerCase();
  return ["input", "textarea", "select"].includes(tag) || t.isContentEditable;
}

function appShell() {
  return {
    navOpen: false,
    collapsed: false,
    helpOpen: false,
    paletteOpen: false,
    createOpen: false,
    aiOpen: false,
    aiLoaded: false,
    theme: "system",
    shortcuts: [],

    init() {
      this.collapsed = uiStore.get("ui.sidebar.collapsed") === "1";
      this.theme = (window.__theme && window.__theme.get()) || "system";
      this.shortcuts = (readJSON("shortcut-data", []) || [])
        .filter((a) => a.shortcut)
        .map((a) => ({ ...a, sc: parseShortcut(a.shortcut) }));
      // Server flash → toast
      (readJSON("flash-data", []) || []).forEach((f) => toast(f.message, f.kind || f.level || "info"));
      this.$watch("navOpen", (v) => document.body.classList.toggle("overflow-hidden", v));
    },

    toggleSidebar() {
      this.collapsed = !this.collapsed;
      uiStore.set("ui.sidebar.collapsed", this.collapsed ? "1" : "0");
      if (this.collapsed) document.documentElement.setAttribute("data-sidebar", "collapsed");
      else document.documentElement.removeAttribute("data-sidebar");
    },

    setTheme(t) {
      this.theme = t;
      setCookie("theme", t);
      if (window.__theme) window.__theme.set(t);
    },

    openPalette() { this.createOpen = false; this.paletteOpen = true; },

    openAi() {
      this.aiOpen = true;
      if (!this.aiLoaded && window.htmx) {
        this.aiLoaded = true;
        const panel = document.getElementById("ai-panel");
        if (panel && panel.dataset.url) {
          htmx.ajax("GET", panel.dataset.url, { target: "#ai-panel", swap: "innerHTML" }).catch(() => { this.aiLoaded = false; });
        }
      }
      this.$nextTick(() => {
        const box = document.querySelector("#ai-panel textarea");
        if (box) box.focus();
      });
    },

    closeOverlays() {
      this.paletteOpen = false;
      this.helpOpen = false;
      this.createOpen = false;
      this.navOpen = false;
      this.aiOpen = false;
    },

    globalKeys(e) {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && !e.altKey && e.code === "KeyK") { e.preventDefault(); this.paletteOpen ? (this.paletteOpen = false) : this.openPalette(); return; }
      if (mod && !e.altKey && e.code === "KeyJ" && document.getElementById("ai-panel")) {
        e.preventDefault();
        this.aiOpen ? (this.aiOpen = false) : this.openAi();
        return;
      }
      if (e.key === "Escape") {
        if (this.paletteOpen || this.helpOpen || this.createOpen || this.navOpen) { this.closeOverlays(); return; }
        if (this.aiOpen) { this.aiOpen = false; return; }
      }
      for (const a of this.shortcuts) {
        if (matchShortcut(a.sc, e)) { e.preventDefault(); window.location.href = a.href; return; }
      }
      if (isTyping(e) || mod || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); this.openPalette(); }
      else if (e.key === "?") { e.preventDefault(); this.helpOpen = true; }
    },
  };
}

/* ------------------------------------------------------------------ Ctrl+K */
function commandPalette() {
  return {
    q: "",
    base: [],
    remote: [],
    active: 0,
    loading: false,
    _ctrl: null,

    init() {
      this.base = readJSON("cmdk-data", []) || [];
      this.$watch("q", debounce(() => this.search(), 160));
      this.$watch("paletteOpen", (open) => {
        if (open) {
          this.q = "";
          this.remote = [];
          this.active = 0;
          this.$nextTick(() => this.$refs.q && this.$refs.q.focus());
        }
      });
    },

    get flat() {
      const q = foldVi(this.q.trim());
      let local = this.base;
      if (q) {
        const words = q.split(/\s+/);
        local = this.base.filter((it) => {
          const hay = foldVi(`${it.label} ${it.hint || ""} ${it.group || ""}`);
          return words.every((w) => hay.includes(w));
        });
      } else {
        local = this.base.slice(0, 12);
      }
      return local.concat(this.remote).slice(0, 40).map((it, i) => ({ ...it, idx: i }));
    },

    get groups() {
      const out = [];
      const map = new Map();
      this.flat.forEach((it) => {
        const g = it.group || "";
        if (!map.has(g)) { map.set(g, { label: g, items: [] }); out.push(map.get(g)); }
        map.get(g).items.push(it);
      });
      return out;
    },

    async search() {
      this.active = 0;
      const q = this.q.trim();
      if (q.length < 2) { this.remote = []; return; }
      const url = (this.$root.dataset.searchUrl || "/search") + "?q=" + encodeURIComponent(q);
      try {
        if (this._ctrl) this._ctrl.abort();
        this._ctrl = new AbortController();
        this.loading = true;
        const res = await fetch(url, { headers: { Accept: "application/json" }, signal: this._ctrl.signal });
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        this.remote = (data.results || []).map((r) => ({ ...r, group: r.group || "Kết quả" }));
      } catch (err) {
        if (err.name !== "AbortError") this.remote = [];
      } finally {
        this.loading = false;
      }
    },

    move(d) {
      const n = this.flat.length;
      if (!n) return;
      this.active = (this.active + d + n) % n;
      this.$nextTick(() => {
        const el = this.$root.querySelector(`[data-idx="${this.active}"]`);
        if (el) el.scrollIntoView({ block: "nearest" });
      });
    },

    go(item) {
      const it = item || this.flat[this.active];
      if (it && it.href) window.location.href = it.href;
    },
  };
}

/* ------------------------------------------------------------------ Toast */
function toaster() {
  return {
    toasts: [],
    _id: 0,
    init() {
      window.__toasterReady = true;
      (window.__toastQueue || []).splice(0).forEach((d) => this.add(d));
    },
    add(d) {
      const id = ++this._id;
      const kind = d.kind === "danger" ? "error" : d.kind || "success";
      this.toasts.push({ id, kind, message: d.message, title: d.title || "" });
      const timeout = d.timeout || (kind === "error" ? 9000 : 4500);
      setTimeout(() => this.remove(id), timeout);
    },
    remove(id) { this.toasts = this.toasts.filter((t) => t.id !== id); },
  };
}

/* ------------------------------------------------------------------ Confirm dialog */
function confirmDialog() {
  return {
    open: false,
    title: "",
    message: "",
    danger: false,
    confirmLabel: "",
    _resolve: null,
    ask(d) {
      if (this._resolve) this._resolve(false);
      this.title = d.title || "";
      this.message = d.message || "";
      this.danger = !!d.danger;
      this.confirmLabel = d.confirmLabel || "";
      this._resolve = d.resolve;
      this.open = true;
      this.$nextTick(() => this.$refs.ok && this.$refs.ok.focus());
    },
    close(result) {
      this.open = false;
      if (this._resolve) this._resolve(result);
      this._resolve = null;
    },
  };
}
