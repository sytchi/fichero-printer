class FicheroPrinterCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._text = "";
    this._copies = 1;
    this._busy = false;
    this._hiddenFavorites = new Set();
    this._built = false;
    this._favoritesKey = null;
  }

  static getStubConfig() { return {}; }
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", selector: { entity: { domain: "sensor", integration: "fichero_printer" } } },
        { name: "margin_mm", selector: { number: { min: 0, max: 5, step: 0.5, mode: "box" } } },
        { name: "offset_mm", selector: { number: { min: -10, max: 10, step: 0.5, mode: "box" } } },
      ],
      computeLabel: (field) => ({
        entity: "Printer status entity",
        margin_mm: "Margin (mm)",
        offset_mm: "Offset along the label (mm)",
      }[field.name] || field.name),
    };
  }

  setConfig(config) { this._config = config || {}; }
  set hass(hass) {
    this._hass = hass;
    const configured = this._config?.entity;
    this._entityId = configured || Object.keys(hass.states).find((id) =>
      id.startsWith("sensor.") && hass.states[id].attributes.config_entry_id
    );
    this._render();
  }

  getCardSize() { return 5; }

  async _call(service, data = {}) {
    const state = this._hass.states[this._entityId];
    if (!state) return false;
    this._busy = true;
    this._render();
    try {
      await this._hass.callService("fichero_printer", service, {
        config_entry_id: state.attributes.config_entry_id, ...data,
      });
      return true;
    } catch (error) {
      const event = new Event("hass-notification", { bubbles: true, composed: true });
      event.detail = { message: error?.message || String(error) };
      this.dispatchEvent(event);
      return false;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _today() {
    const now = new Date();
    return `${String(now.getDate()).padStart(2, "0")}-${String(now.getMonth() + 1).padStart(2, "0")}-${now.getFullYear()}`;
  }

  _printData(text) {
    const data = { text, copies: this._copies };
    const margin = Number(this._config?.margin_mm);
    const offset = Number(this._config?.offset_mm);
    if (Number.isFinite(margin)) data.margin_mm = margin;
    if (Number.isFinite(offset)) data.offset_mm = offset;
    return data;
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const state = this._entityId && this._hass.states[this._entityId];
    if (!state) {
      this.shadowRoot.innerHTML = `<ha-card><div class="missing">No Fichero printer status entity found.</div></ha-card>`;
      this._built = false;
      return;
    }
    // The card is built once and then updated in place. Replacing innerHTML on
    // every state change destroyed the textarea while the user was typing in
    // it, which dropped focus and every keystroke after the next update.
    if (!this._built) this._build();
    this._update(state);
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 18px; }
        .heading { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
        h2 { font-size:1.25rem; margin:0; }
        .status { display:flex; align-items:center; gap:7px; color:var(--secondary-text-color); text-transform:capitalize; }
        .dot { width:10px; height:10px; border-radius:50%; background:var(--error-color,#db4437); }
        .dot.connected { background:var(--success-color,#43a047); }
        textarea { box-sizing:border-box; width:100%; min-height:76px; resize:vertical; border:1px solid var(--divider-color); border-radius:10px; padding:12px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; }
        .row { display:flex; align-items:center; gap:10px; margin-top:12px; flex-wrap:wrap; }
        button { cursor:pointer; border:0; border-radius:10px; padding:10px 14px; color:var(--primary-text-color); background:var(--secondary-background-color); font:inherit; font-weight:500; }
        button.primary { color:var(--text-primary-color); background:var(--primary-color); }
        button:disabled { opacity:.55; cursor:wait; }
        label { display:flex; align-items:center; gap:8px; color:var(--secondary-text-color); }
        input[type=number] { width:64px; padding:8px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); color:var(--primary-text-color); }
        .favorites { margin-top:16px; border-top:1px solid var(--divider-color); padding-top:14px; }
        .favorites-title { font-size:.9rem; color:var(--secondary-text-color); margin-bottom:8px; }
        .error { margin:12px 0; padding:10px 12px; color:var(--error-color,#db4437); background:color-mix(in srgb,var(--error-color,#db4437) 10%,transparent); border-radius:8px; }
        .favorite { display:inline-flex; align-items:stretch; margin:0 8px 8px 0; background:var(--secondary-background-color); border-radius:10px; overflow:hidden; }
        .favorite button { border-radius:0; margin:0; }
        .favorite .remove { padding:8px; color:var(--secondary-text-color); }
        .missing { padding:20px; }
      </style>
      <ha-card>
        <div class="heading">
          <div><h2>Fichero label printer</h2><div class="status"><span class="dot"></span><span id="status"></span></div></div>
          <button id="connection"></button>
        </div>
        <div class="error" id="error" hidden></div>
        <textarea id="text" maxlength="500" placeholder="Text for your label"></textarea>
        <div class="row">
          <label>Labels <input id="copies" type="number" min="1" max="100" value="1"></label>
          <button class="primary" id="print">Print</button>
          <button class="date" id="today"></button>
          <button id="favorite">&#9734; Save favorite</button>
        </div>
        <div class="favorites" id="favorites" hidden>
          <div class="favorites-title">Favorites &mdash; click to print</div>
          <div id="favorite-list"></div>
        </div>
      </ha-card>`;

    const root = this.shadowRoot;
    root.getElementById("text").addEventListener("input", (event) => { this._text = event.target.value; });
    root.getElementById("copies").addEventListener("input", (event) => {
      this._copies = Math.max(1, Math.min(100, Number(event.target.value) || 1));
    });
    root.getElementById("connection").onclick = () => this._call(this._connected ? "disconnect" : "connect");
    root.getElementById("print").onclick = () => this._call("print_label", this._printData(this._text));
    root.getElementById("favorite").onclick = () => this._call("save_favorite", { text: this._text });
    root.getElementById("today").onclick = () => {
      const today = this._today();
      this._text = today;
      root.getElementById("text").value = today;
      this._call("print_label", this._printData(today));
    };
    this._built = true;
  }

  _update(state) {
    const root = this.shadowRoot;
    const connected = state.attributes.connected === true;
    this._connected = connected;
    root.getElementById("status").textContent = state.state;
    root.querySelector(".dot").classList.toggle("connected", connected);
    root.getElementById("connection").textContent = connected ? "Disconnect" : "Connect";

    const error = root.getElementById("error");
    error.textContent = state.attributes.last_error || "";
    error.hidden = !state.attributes.last_error;

    root.getElementById("today").textContent = `\u{1F4C5} Print ${this._today()}`;

    const storedFavorites = Array.isArray(state.attributes.favorites) ? state.attributes.favorites : [];
    // Once HA publishes the shorter list, the optimistic hiding is no longer
    // needed. Until then it prevents a deleted favorite flashing back onscreen.
    for (const favorite of this._hiddenFavorites) {
      if (!storedFavorites.includes(favorite)) this._hiddenFavorites.delete(favorite);
    }
    const favorites = storedFavorites
      .map((text, index) => ({ text, index }))
      .filter(({ text }) => !this._hiddenFavorites.has(text));
    const key = JSON.stringify(favorites);
    if (key !== this._favoritesKey) {
      this._favoritesKey = key;
      this._buildFavorites(favorites, storedFavorites);
    }

    for (const button of root.querySelectorAll("button")) button.disabled = this._busy;
  }

  _buildFavorites(favorites, storedFavorites) {
    const root = this.shadowRoot;
    const list = root.getElementById("favorite-list");
    root.getElementById("favorites").hidden = favorites.length === 0;
    list.replaceChildren();
    for (const { text, index } of favorites) {
      const wrapper = document.createElement("span");
      wrapper.className = "favorite";

      const print = document.createElement("button");
      print.className = "favorite-print";
      print.textContent = text;
      print.onclick = () => this._call("print_label", this._printData(storedFavorites[index]));

      const remove = document.createElement("button");
      remove.className = "remove";
      remove.title = "Remove favorite";
      remove.setAttribute("aria-label", `Remove ${text}`);
      remove.textContent = "×";
      remove.onclick = async (event) => {
        event.stopPropagation();
        if (await this._call("delete_favorite", { index })) {
          this._hiddenFavorites.add(text);
          this._render();
        }
      };

      wrapper.append(print, remove);
      list.append(wrapper);
    }
  }
}

if (!customElements.get("fichero-printer-card")) customElements.define("fichero-printer-card", FicheroPrinterCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "fichero-printer-card",
  name: "Fichero Label Printer",
  description: "Connect, fit text, print copies, and use favorite label shortcuts.",
  preview: true,
});
