// Form components: money input, lookup combobox, voucher line grid, invoice grid.
// Convention: the visible input has NO name; the value sent to the server lives in a normalized <input type="hidden">
// (amounts: integer dong; quantity/unit price: dot as decimal separator, e.g. "1234.5").

/* ------------------------------------------------------------------ Money input */
// x-data='moneyInput(12000000)' ; the visible input uses @input="onInput($event)" @blur="onBlur($event)"
function moneyInput(initial) {
  return {
    display: initial !== null && initial !== undefined && initial !== "" ? formatVnd(initial) : "",
    value: initial !== null && initial !== undefined && initial !== "" ? Number(initial) : null,
    onInput(e) {
      const el = e.target;
      const text = el.value;
      if (/^-?[\d.\s]*$/.test(text)) {
        // Only reformat when typing plain digits (no k/tr unit), keeping the caret position.
        const neg = text.trim().startsWith("-");
        const digits = text.replace(/\D/g, "");
        const pos = el.selectionStart || 0;
        const digitsBefore = text.slice(0, pos).replace(/\D/g, "").length;
        const formatted = digits ? (neg ? "-" : "") + formatVnd(parseInt(digits, 10)) : neg ? "-" : "";
        el.value = formatted;
        let count = 0;
        let p = 0;
        while (p < formatted.length && count < digitsBefore) {
          if (/\d/.test(formatted[p])) count++;
          p++;
        }
        try { el.setSelectionRange(p, p); } catch (err) { /* type=number */ }
      }
      this.display = el.value;
      this.value = parseVnd(el.value);
      this.$dispatch("money-change", { value: this.value });
    },
    onBlur(e) {
      const v = parseVnd(e.target.value);
      this.value = v;
      this.display = v === null ? "" : formatVnd(v);
      e.target.value = this.display;
    },
  };
}

/* ------------------------------------------------------------------ Combobox */
// opts: { url, params, value, label, minChars, allowFree, localFilter, items, onSelect }
// Server results: [{value, label, hint, extra}] or {results: [...]}.
function combobox(opts = {}) {
  return {
    open: false,
    query: opts.label || "",
    value: opts.value === undefined || opts.value === null ? "" : String(opts.value),
    label: opts.label || "",
    items: [],
    active: 0,
    loading: false,
    searched: false,
    _ctrl: null,
    _fetch: null,
    _onScroll: null,

    init() {
      this._fetch = debounce(() => this.fetch(), 160);
    },

    // Show "Không tìm thấy" once a search for the user's typed text finished with no results.
    get showEmpty() {
      return !this.loading && this.searched && this.items.length === 0 && !!this.query && this.query !== this.label;
    },

    setInitial(value, label) {
      this.value = value === undefined || value === null ? "" : String(value);
      this.label = label || "";
      this.query = this.label;
    },

    // Fixed-position list (data-fixed) – used in line grids so the table's scroll container does not clip it.
    place() {
      const list = this.$refs.list;
      const input = this.$refs.input;
      if (!list || !input || !("fixed" in list.dataset)) return;
      const r = input.getBoundingClientRect();
      const below = window.innerHeight - r.bottom;
      list.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 320) - 8))}px`;
      list.style.minWidth = `${Math.max(r.width, 320)}px`;
      if (below < 260 && r.top > below) {
        list.style.top = "auto";
        list.style.bottom = `${window.innerHeight - r.top + 4}px`;
      } else {
        list.style.bottom = "auto";
        list.style.top = `${r.bottom + 4}px`;
      }
      if (!this._onScroll) {
        this._onScroll = () => { if (this.open) this.place(); };
        window.addEventListener("scroll", this._onScroll, true);
        window.addEventListener("resize", this._onScroll);
      }
    },

    onFocus() {
      this.open = true;
      this.$nextTick(() => this.place());
      this.fetch();
      this.$nextTick(() => this.$refs.input && this.$refs.input.select());
    },

    onInput() {
      this.open = true;
      this.$nextTick(() => this.place());
      this.active = 0;
      if (!opts.allowFree) this.value = "";
      else this.value = this.query;
      this._fetch();
    },

    async fetch() {
      const q = this.query === this.label ? "" : this.query.trim();
      if (q.length < (opts.minChars || 0)) { this.items = []; return; }
      let list = [];
      if (opts.items) {
        list = opts.items;
      } else if (opts.url) {
        const u = new URL(opts.url, window.location.origin);
        u.searchParams.set("q", q);
        Object.entries(opts.params || {}).forEach(([k, v]) => { if (v !== null && v !== undefined) u.searchParams.set(k, v); });
        try {
          if (this._ctrl) this._ctrl.abort();
          this._ctrl = new AbortController();
          this.loading = true;
          const res = await fetch(u.toString(), { headers: { Accept: "application/json" }, signal: this._ctrl.signal });
          if (!res.ok) throw new Error(String(res.status));
          const data = await res.json();
          list = Array.isArray(data) ? data : data.results || [];
        } catch (err) {
          if (err.name === "AbortError") return;
          list = [];
        } finally {
          this.loading = false;
        }
      }
      if ((opts.items || opts.localFilter) && q) {
        const words = foldVi(q).split(/\s+/);
        list = list.filter((it) => {
          const hay = foldVi(`${it.value} ${it.label} ${it.hint || ""}`);
          return words.every((w) => hay.includes(w));
        });
      }
      this.items = list.slice(0, opts.limit || 30);
      this.searched = true;
      if (this.active >= this.items.length) this.active = 0;
      this.$nextTick(() => this.place());
    },

    select(item) {
      if (!item) return;
      this.value = String(item.value);
      this.label = item.label;
      this.query = item.label;
      this.open = false;
      if (typeof opts.onSelect === "function") opts.onSelect(item);
      this.$dispatch("combobox-select", item);
    },

    clear() {
      this.value = "";
      this.label = "";
      this.query = "";
      if (typeof opts.onSelect === "function") opts.onSelect({ value: "", label: "", extra: {} });
      this.$dispatch("combobox-select", { value: "", label: "", extra: {} });
    },

    onKey(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (!this.open) this.onFocus(); else this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") {
        if (this.open && this.items.length) { e.preventDefault(); this.select(this.items[this.active]); }
      } else if (e.key === "Escape") {
        if (this.open) { e.stopPropagation(); this.open = false; this.query = this.label; }
      } else if (e.key === "Tab") {
        if (this.open && this.items.length && this.query && this.query !== this.label) this.select(this.items[this.active]);
        this.open = false;
      }
    },

    move(d) {
      const n = this.items.length;
      if (!n) return;
      this.active = (this.active + d + n) % n;
      this.$nextTick(() => {
        const el = this.$refs.list && this.$refs.list.querySelector(`[data-i="${this.active}"]`);
        if (el) el.scrollIntoView({ block: "nearest" });
      });
    },

    onBlur() {
      setTimeout(() => {
        this.open = false;
        if (opts.allowFree) { this.value = this.query; this.label = this.query; return; }
        if (!this.value) { this.query = ""; this.label = ""; }
        else this.query = this.label;
      }, 120);
    },
  };
}

/* ------------------------------------------------------------------ Grid keyboard */
// Enter = next cell; on the last cell of the last line it adds a line.  Ctrl+Delete = delete line.  Ctrl+D = duplicate line.
// Grid root: the element with data-grid (set in init). When called from a nested combobox cell, `this.$root` is the
// combobox root, not the grid's – so always search the DOM starting from the cell being edited.
function gridRootOf(grid, el) {
  return (el && el.closest && el.closest("[data-grid]")) || grid.$root;
}

function gridKeys(grid, e, index) {
  const root = gridRootOf(grid, e.target);
  if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
    e.preventDefault();
    const cells = Array.from(root.querySelectorAll("[data-cell]")).filter((el) => !el.disabled && el.offsetParent !== null);
    const i = cells.indexOf(e.target);
    if (i < 0) return;
    if (i < cells.length - 1) { cells[i + 1].focus(); if (cells[i + 1].select) cells[i + 1].select(); }
    else { grid.addLine(); grid.focusRow(grid.lines.length - 1, root); }
  } else if ((e.ctrlKey || e.metaKey) && e.key === "Delete") {
    e.preventDefault();
    grid.removeLine(index);
    grid.focusRow(Math.max(0, index - 1), root);
  } else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.code === "KeyD") {
    e.preventDefault();
    grid.duplicateLine(index);
    grid.focusRow(index + 1, root);
  }
}

let _uid = 0;
const nextUid = () => `r${++_uid}`;

/* ------------------------------------------------------------------ Voucher line grid */
// opts: {
//   mode: "split" (account, debit, credit) | "pair" (debit account, credit account, amount),
//   prefix: "lines", lines: [...], fixed: {debit_account: "1111", debit_account_label: "…"},
//   showPartner: true, minLines: 2, descriptionFrom: "#f-description",
// }
// Submitted inputs: {prefix}-count, {prefix}-{i}-account | debit_account | credit_account, -debit, -credit | -amount,
// -partner_id, -description (amounts are integers).
function lineGrid(opts = {}) {
  const mode = opts.mode || "split";
  const blank = () => ({
    uid: nextUid(), account: "", account_label: "", debit_account: "", debit_account_label: "",
    credit_account: "", credit_account_label: "", debit: null, credit: null, amount: null,
    partner_id: "", partner_label: "", description: "",
  });
  return {
    mode,
    prefix: opts.prefix || "lines",
    fixed: opts.fixed || {},
    lines: [],

    init() {
      this.$el.setAttribute("data-grid", "");
      const src = opts.lines && opts.lines.length ? opts.lines : [];
      this.lines = src.map((l) => ({ ...blank(), ...l, uid: nextUid() }));
      const min = opts.minLines === undefined ? (mode === "split" ? 2 : 1) : opts.minLines;
      while (this.lines.length < min) this.lines.push(blank());
    },

    name(i, field) { return `${this.prefix}-${i}-${field}`; },
    fmt(v) { return formatVnd(v); },

    get totalDebit() { return this.lines.reduce((s, l) => s + (Number(l.debit) || 0), 0); },
    get totalCredit() { return this.lines.reduce((s, l) => s + (Number(l.credit) || 0), 0); },
    get totalAmount() { return this.lines.reduce((s, l) => s + (Number(l.amount) || 0), 0); },
    get diff() { return this.totalDebit - this.totalCredit; },
    get balanced() { return this.mode !== "split" || (this.diff === 0 && this.totalDebit > 0); },

    defaultDescription() {
      if (!opts.descriptionFrom) return "";
      const el = document.querySelector(opts.descriptionFrom);
      return el ? el.value : "";
    },

    addLine() {
      const l = blank();
      l.description = this.defaultDescription() || (this.lines.length ? this.lines[this.lines.length - 1].description : "");
      if (this.mode === "split") {
        const d = this.diff;
        if (d > 0) l.credit = d;
        else if (d < 0) l.debit = -d;
      } else if (this.lines.length) {
        const prev = this.lines[this.lines.length - 1];
        if (!this.fixed.debit_account) { l.debit_account = prev.debit_account; l.debit_account_label = prev.debit_account_label; }
        if (!this.fixed.credit_account) { l.credit_account = prev.credit_account; l.credit_account_label = prev.credit_account_label; }
      }
      this.lines.push(l);
    },

    removeLine(i) {
      if (this.lines.length <= 1) { this.lines.splice(0, 1, blank()); return; }
      this.lines.splice(i, 1);
    },

    duplicateLine(i) {
      const copy = { ...this.lines[i], uid: nextUid() };
      this.lines.splice(i + 1, 0, copy);
    },

    focusRow(i, root) {
      this.$nextTick(() => {
        const row = (root || this.$root).querySelectorAll("[data-row]")[i];
        const cell = row && row.querySelector("[data-cell]");
        if (cell) { cell.focus(); if (cell.select) cell.select(); }
      });
    },

    // Money cell in the grid: show the formatted value when not focused.
    moneyEffect(el, value) { if (document.activeElement !== el) el.value = formatVnd(value); },
    setMoney(line, field, e) { line[field] = parseVnd(e.target.value); },
    moneyBlur(line, field, e) {
      line[field] = parseVnd(e.target.value);
      e.target.value = formatVnd(line[field]);
      // Typing a debit clears the credit of the same line (a line has only one side).
      if (this.mode === "split" && line[field]) line[field === "debit" ? "credit" : "debit"] = null;
    },

    keys(e, i) { gridKeys(this, e, i); },
  };
}

/* ------------------------------------------------------------------ Invoice grid */
// opts: { prefix, lines, priceField: "sale_price" | "purchase_price", vatRates: [{value, label, rate}], defaultVat: "10", showDiscount }
// Submitted inputs: {prefix}-count, -item_id, -description, -unit, -quantity, -unit_price, -amount, -discount, -vat_rate, -vat_amount
function invoiceGrid(opts = {}) {
  const rates = opts.vatRates || [
    { value: "10", label: "10%", rate: 10 }, { value: "8", label: "8%", rate: 8 }, { value: "5", label: "5%", rate: 5 },
    { value: "0", label: "0%", rate: 0 }, { value: "KCT", label: "KCT", rate: 0 }, { value: "KKKNT", label: "KKKNT", rate: 0 },
  ];
  const rateOf = (v) => { const r = rates.find((x) => x.value === String(v)); return r ? r.rate : 0; };
  const blank = () => ({
    uid: nextUid(), item_id: "", item_label: "", description: "", unit: "", quantity: null, unit_price: null,
    amount: null, discount: null, vat_rate: opts.defaultVat || "10", vat_amount: null, amountLocked: false, vatLocked: false,
  });
  return {
    prefix: opts.prefix || "lines",
    rates,
    showDiscount: !!opts.showDiscount,
    lines: [],

    init() {
      this.$el.setAttribute("data-grid", "");
      const src = opts.lines && opts.lines.length ? opts.lines : [];
      this.lines = src.map((l) => ({ ...blank(), ...l, uid: nextUid(), amountLocked: false, vatLocked: false }));
      if (!this.lines.length) this.lines.push(blank());
    },

    name(i, field) { return `${this.prefix}-${i}-${field}`; },
    fmt(v) { return formatVnd(v); },
    fmtQty(v) { return formatQty(v); },
    num(v) { return v === null || v === undefined || v === "" ? "" : String(v); },

    recalc(l) {
      if (!l.amountLocked) {
        const q = Number(l.quantity) || 0;
        const p = Number(l.unit_price) || 0;
        l.amount = l.quantity === null && l.unit_price === null ? null : Math.round(q * p);
      }
      if (!l.vatLocked) {
        const base = (Number(l.amount) || 0) - (Number(l.discount) || 0);
        l.vat_amount = Math.round((base * rateOf(l.vat_rate)) / 100);
      }
    },

    onItem(l, item) {
      const x = item.extra || {};
      l.item_id = item.value;
      l.item_label = item.label;
      if (item.value) {
        l.description = x.name || (item.label || "").replace(/^\S+\s+[–-]\s+/, "");
        if (x.unit) l.unit = x.unit;
        const price = x[opts.priceField || "sale_price"];
        if (price !== undefined && price !== null && price !== "") l.unit_price = Number(price);
        if (x.vat_rate !== undefined && x.vat_rate !== null && x.vat_rate !== "") l.vat_rate = String(x.vat_rate);
        if (l.quantity === null) l.quantity = 1;
      }
      this.recalc(l);
    },

    get subtotal() { return this.lines.reduce((s, l) => s + (Number(l.amount) || 0), 0); },
    get discountTotal() { return this.lines.reduce((s, l) => s + (Number(l.discount) || 0), 0); },
    get vatTotal() { return this.lines.reduce((s, l) => s + (Number(l.vat_amount) || 0), 0); },
    get total() { return this.subtotal - this.discountTotal + this.vatTotal; },
    get vatGroups() {
      const g = {};
      this.lines.forEach((l) => {
        if (!l.amount) return;
        const k = l.vat_rate;
        g[k] = g[k] || { rate: k, base: 0, vat: 0 };
        g[k].base += (Number(l.amount) || 0) - (Number(l.discount) || 0);
        g[k].vat += Number(l.vat_amount) || 0;
      });
      return Object.values(g);
    },
    rateLabel(v) { const r = rates.find((x) => x.value === String(v)); return r ? r.label : v; },

    addLine() { this.lines.push(blank()); },
    removeLine(i) { if (this.lines.length <= 1) this.lines.splice(0, 1, blank()); else this.lines.splice(i, 1); },
    duplicateLine(i) { this.lines.splice(i + 1, 0, { ...this.lines[i], uid: nextUid() }); },
    focusRow(i, root) {
      this.$nextTick(() => {
        const row = (root || this.$root).querySelectorAll("[data-row]")[i];
        const cell = row && row.querySelector("[data-cell]");
        if (cell) cell.focus();
      });
    },

    qtyEffect(el, v) { if (document.activeElement !== el) el.value = formatQty(v); },
    moneyEffect(el, v) { if (document.activeElement !== el) el.value = formatVnd(v); },
    setQty(l, field, e) { l[field] = parseQty(e.target.value); this.recalc(l); },
    setMoney(l, field, e) {
      l[field] = parseVnd(e.target.value);
      if (field === "amount") l.amountLocked = true;
      if (field === "vat_amount") l.vatLocked = true;
      this.recalc(l);
    },
    unlock(l) { l.amountLocked = false; l.vatLocked = false; this.recalc(l); },

    keys(e, i) { gridKeys(this, e, i); },
  };
}

/* ------------------------------------------------------------------ Cash count (from v1) */
function cashCount(denoms, bookBalance, initial) {
  return {
    denoms,
    bookBalance,
    qty: Object.fromEntries(denoms.map((d) => [d, (initial && initial[d]) || 0])),
    lineTotal(d) { return d * (parseInt(this.qty[d], 10) || 0); },
    get total() { return this.denoms.reduce((s, d) => s + this.lineTotal(d), 0); },
    get diff() { return this.total - this.bookBalance; },
    fmt: formatVnd,
  };
}

/* ------------------------------------------------------------------ v1 cash receipt/payment form (kept for old templates) */
function voucherForm(opts, partners) {
  return {
    kind: opts.kind,
    docDate: opts.docDate,
    postDate: opts.postDate,
    address: opts.address,
    syncPost: opts.syncPost,
    partners: partners || {},
    onDocDate() { if (this.syncPost) this.postDate = this.docDate; },
    fillAddress(e) {
      const addr = this.partners[e.target.value];
      if (addr && !this.address) this.address = addr;
    },
    formKeys(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        const btn = this.$refs.postBtn;
        if (btn) this.$refs.form.requestSubmit(btn);
      }
    },
  };
}

/* ------------------------------------------------------------------ Voucher form: Ctrl+Enter = data-primary-submit button */
// <form x-data="docForm()" @keydown="keys($event)"> … <button name="action" value="post" data-hotkey="post">
function docForm() {
  return {
    keys(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        const btn = this.$root.querySelector('[data-hotkey="post"]') || this.$root.querySelector('[data-hotkey="save"]');
        if (btn) this.$root.requestSubmit(btn);
      } else if ((e.ctrlKey || e.metaKey) && e.code === "KeyS") {
        e.preventDefault();
        const btn = this.$root.querySelector('[data-hotkey="save"]');
        if (btn) this.$root.requestSubmit(btn);
      }
    },
  };
}
