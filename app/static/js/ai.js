// AI assistant chat panel – reads SSE from POST /ai/chat per docs/CONTRACTS.md section 3.
// Events: meta {conversation_id} · token {text} · tool {id, name, label, status, summary, link} · done {message_id, html} · error {message}
//
// x-data='aiChat({"postUrl": "/ai/chat", "conversationId": 12, "historyCount": 4})'
function aiChat(opts = {}) {
  return {
    postUrl: opts.postUrl || "/ai/chat",
    conversationId: opts.conversationId || null,
    historyCount: opts.historyCount || 0,
    messages: [],
    input: "",
    busy: false,
    _ctrl: null,

    init() {
      this.$nextTick(() => this.scroll());
      // When htmx loads another conversation's history into #ai-history
      this.$root.addEventListener("htmx:afterSwap", (e) => {
        if (e.target && e.target.id === "ai-history") {
          this.historyCount = e.target.querySelectorAll("[data-ai-message]").length;
          this.$nextTick(() => this.scroll());
        }
      });
    },

    get empty() { return !this.historyCount && !this.messages.length; },

    scroll() {
      const s = this.$refs.scroller;
      if (s) s.scrollTop = s.scrollHeight;
    },

    autosize(e) {
      const el = e.target;
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    },

    onKey(e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); }
    },

    ask(text) { this.input = text; this.send(); },

    newConversation() {
      if (this.busy) this.stop();
      this.conversationId = null;
      this.messages = [];
      this.historyCount = 0;
      const h = document.getElementById("ai-history");
      if (h) h.replaceChildren();
      this.$nextTick(() => this.$refs.box && this.$refs.box.focus());
    },

    switchTo(id) {
      if (this.busy) this.stop();
      this.conversationId = id;
      this.messages = [];
    },

    stop() {
      if (this._ctrl) this._ctrl.abort();
      const last = this.messages[this.messages.length - 1];
      if (last && last.pending) { last.pending = false; last.stopped = true; }
      this.busy = false;
    },

    toolIcon(t) { return t.status === "running" ? "loader" : t.status === "error" ? "x" : "check"; },

    async send() {
      const content = this.input.trim();
      if (!content || this.busy) return;
      this.input = "";
      if (this.$refs.box) this.$refs.box.style.height = "auto";
      this.messages.push({ role: "user", text: content });
      const msg = { role: "assistant", text: "", html: null, tools: [], pending: true, error: "", stopped: false };
      this.messages.push(msg);
      const m = this.messages[this.messages.length - 1]; // reactive proxy
      this.busy = true;
      this.$nextTick(() => this.scroll());

      const fd = new FormData();
      fd.append("content", content);
      if (this.conversationId) fd.append("conversation_id", this.conversationId);
      fd.append("csrf_token", csrfToken());
      this._ctrl = new AbortController();
      try {
        const res = await fetch(this.postUrl, {
          method: "POST",
          body: fd,
          headers: { Accept: "text/event-stream", "X-CSRF-Token": csrfToken() },
          signal: this._ctrl.signal,
        });
        if (!res.ok || !res.body) {
          let text = "";
          try { text = (await res.text()).slice(0, 300); } catch (e) { /* ignore */ }
          throw new Error(text && !text.trim().startsWith("<") ? text : `HTTP ${res.status}`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let sep;
          while ((sep = buf.search(/\r?\n\r?\n/)) >= 0) {
            const raw = buf.slice(0, sep);
            buf = buf.slice(sep).replace(/^\r?\n\r?\n/, "");
            this.handle(m, raw);
          }
        }
        if (buf.trim()) this.handle(m, buf);
      } catch (err) {
        if (err.name !== "AbortError") m.error = err.message || "Lỗi kết nối";
      } finally {
        m.pending = false;
        this.busy = false;
        this._ctrl = null;
        this.$nextTick(() => this.scroll());
      }
    },

    handle(m, raw) {
      let event = "message";
      const data = [];
      raw.split(/\r?\n/).forEach((line) => {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      });
      if (!data.length) return;
      let d;
      try { d = JSON.parse(data.join("\n")); } catch (e) { d = { text: data.join("\n") }; }
      if (event === "meta") {
        if (d.conversation_id) this.conversationId = d.conversation_id;
      } else if (event === "token") {
        m.text += d.text || "";
      } else if (event === "tool") {
        const i = m.tools.findIndex((t) => t.id === d.id);
        if (i >= 0) m.tools.splice(i, 1, { ...m.tools[i], ...d });
        else m.tools.push(d);
      } else if (event === "done") {
        if (d.html) m.html = d.html; // HTML already sanitized by the server (CONTRACTS section 3)
        m.pending = false;
      } else if (event === "error") {
        m.error = d.message || "Lỗi";
        m.pending = false;
      }
      this.$nextTick(() => this.scroll());
    },
  };
}
