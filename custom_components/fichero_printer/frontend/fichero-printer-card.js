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
    this._previewTimer = null;
    this._withIcon = false;
    this._icon = "";
    this._iconSide = "left";
    this._artwork = "";
    this._artworkMode = "icon";
    this._artworkPrompt = "";
    this._drawing = "";
    this._lengthMm = null;
    this._offsetMm = null;
    this._withDate = false;
    this._bold = true;
    this._date = this._localDate();
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
        offset_mm: "Offset along the label (mm, the card starts from this)",
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

  _layoutKey() { return `fichero-printer-layout:${this._entityId}`; }

  _number(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  _restoreLayout() {
    // Kept per printer so the tape length survives a dashboard reload; the
    // configured length and the card config are only the starting point.
    let stored = {};
    try {
      stored = JSON.parse(window.localStorage.getItem(this._layoutKey()) || "{}") || {};
    } catch (error) {
      stored = {};
    }
    const attributes = this._hass.states[this._entityId].attributes;
    this._lengthMm = this._number(stored.length_mm, this._number(attributes.label_length_mm, 30));
    this._offsetMm = this._number(stored.offset_mm, this._number(this._config?.offset_mm, 0));
    // Bold is the default, so only an explicit false switches it off.
    this._bold = stored.bold !== false;
  }

  _saveLayout() {
    try {
      window.localStorage.setItem(
        this._layoutKey(),
        JSON.stringify({ length_mm: this._lengthMm, offset_mm: this._offsetMm, bold: this._bold })
      );
    } catch (error) {
      // A private window refuses to store anything; the values still hold for
      // this visit, so there is nothing to report.
    }
  }

  _commit(input, min, max, previous) {
    // Clamping waits for the field to be committed: a half typed "6" on its way
    // to "60" would otherwise snap to the minimum and block the second digit.
    const value = Number(input.value);
    const next = Number.isFinite(value) && input.value !== ""
      ? Math.min(max, Math.max(min, value))
      : previous;
    input.value = next;
    return next;
  }

  _iconSideAvailable() {
    // A generated pictogram is printed without ticking the icon checkbox, so
    // the side switch has to stay usable for it as well.
    return this._withIcon || (Boolean(this._artwork) && this._artworkMode === "icon");
  }

  _syncIconControls() {
    const root = this.shadowRoot;
    const drawing = Boolean(this._drawing);
    root.getElementById("icon").disabled = !this._withIcon;
    root.getElementById("icon-side").disabled = !this._iconSideAvailable();
    // Drawing a pictogram belongs to the icon, so it waits for the checkbox.
    root.getElementById("draw-icon").disabled = drawing || !this._withIcon;
    root.getElementById("draw-artwork").disabled = drawing;
    root.getElementById("suggest-icon").disabled = drawing;

    const notice = root.getElementById("notice");
    notice.textContent = {
      icon: "Drawing the pictogram, this takes about half a minute…",
      full: "Drawing the label picture, this takes about half a minute…",
      suggest: "Picking an icon…",
    }[this._drawing] || "";
    notice.hidden = !drawing;
  }

  _localDate() {
    // toISOString() is UTC, which is yesterday for most of the evening east of
    // Greenwich, so the picker has to be filled from the local date.
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  }

  _printedDate() {
    const [year, month, day] = (this._date || "").split("-");
    return year && month && day ? `${day}-${month}-${year}` : "";
  }

  _printData(text) {
    const data = { text, copies: this._copies, bold: this._bold };
    if (this._withDate) data.date = this._printedDate();
    if (this._withIcon && this._icon.trim()) {
      data.icon = this._icon.trim();
      data.icon_side = this._iconSide;
    }
    if (this._artwork) {
      data.artwork = this._artwork;
      data.artwork_mode = this._artworkMode;
      if (this._artworkMode === "icon") data.icon_side = this._iconSide;
    }
    const margin = Number(this._config?.margin_mm);
    if (Number.isFinite(margin)) data.margin_mm = margin;
    if (Number.isFinite(this._offsetMm)) data.offset_mm = this._offsetMm;
    if (Number.isFinite(this._lengthMm)) data.length_mm = this._lengthMm;
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
        input[type=text] { flex:1; min-width:120px; padding:8px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; }
        select { padding:8px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; }
        select:disabled { opacity:.55; }
        input[type=date] { padding:8px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; }
        input[type=date]:disabled { opacity:.55; }
        .favorites { margin-top:16px; border-top:1px solid var(--divider-color); padding-top:14px; }
        .favorites-title { font-size:.9rem; color:var(--secondary-text-color); margin-bottom:8px; }
        .error { margin:12px 0; padding:10px 12px; color:var(--error-color,#db4437); background:color-mix(in srgb,var(--error-color,#db4437) 10%,transparent); border-radius:8px; }
        .favorite { display:inline-flex; align-items:stretch; margin:0 8px 8px 0; background:var(--secondary-background-color); border-radius:10px; overflow:hidden; }
        .favorite button { border-radius:0; margin:0; }
        .favorite .remove { padding:8px; color:var(--secondary-text-color); }
        #preview[hidden] { display:none; }
        #preview { display:block; width:100%; margin-bottom:12px; border:1px solid var(--divider-color); border-radius:8px; background:#fff; image-rendering:pixelated; }
        .preview-error { margin-bottom:12px; font-size:.9rem; color:var(--secondary-text-color); }
        .notice { margin-bottom:12px; padding:10px 12px; font-size:.9rem; border-radius:8px; color:var(--primary-text-color); background:var(--secondary-background-color); }
        .notice[hidden] { display:none; }
        .missing { padding:20px; }
      </style>
      <ha-card>
        <div class="heading">
          <div><h2>Fichero label printer</h2><div class="status"><span class="dot"></span><span id="status"></span></div></div>
          <button id="connection"></button>
        </div>
        <div class="error" id="error" hidden></div>
        <img id="preview" alt="Label preview" hidden>
        <div class="preview-error" id="preview-error" hidden></div>
        <div class="notice" id="notice" hidden></div>
        <textarea id="text" maxlength="500" placeholder="Text for your label"></textarea>
        <div class="row">
          <label>Labels <input id="copies" type="number" min="1" max="100" value="1"></label>
          <label><input id="with-date" type="checkbox"> Add date</label>
          <input id="date" type="date">
          <label><input id="bold" type="checkbox" checked> Bold</label>
        </div>
        <div class="row">
          <label>Length <input id="length" type="number" min="10" max="100" step="1"> mm</label>
          <label>Offset <input id="offset" type="number" min="-10" max="10" step="0.5"> mm</label>
        </div>
        <div class="row">
          <input id="artwork-prompt" type="text" placeholder="Picture for the whole label">
          <button id="draw-artwork">Draw label</button>
          <button id="clear-artwork" hidden>Clear picture</button>
        </div>
        <div class="row">
          <label><input id="with-icon" type="checkbox"> Icon</label>
          <input id="icon" type="text" placeholder="mdi:pasta">
          <button id="suggest-icon">Suggest</button>
          <button id="draw-icon">Draw icon</button>
          <select id="icon-side" aria-label="Icon side">
            <option value="left">Icon left</option>
            <option value="right">Icon right</option>
          </select>
          <button class="primary" id="print">Print</button>
          <button id="favorite">&#9734; Save favorite</button>
        </div>
        <div class="favorites" id="favorites" hidden>
          <div class="favorites-title">Favorites &mdash; click to print</div>
          <div id="favorite-list"></div>
        </div>
      </ha-card>`;

    const root = this.shadowRoot;
    root.getElementById("text").addEventListener("input", (event) => {
      this._text = event.target.value;
      this._schedulePreview();
    });
    root.getElementById("copies").addEventListener("input", (event) => {
      this._copies = Math.max(1, Math.min(100, Number(event.target.value) || 1));
    });
    root.getElementById("connection").onclick = () => this._call(this._connected ? "disconnect" : "connect");
    root.getElementById("print").onclick = () => this._call("print_label", this._printData(this._text));
    root.getElementById("favorite").onclick = () => this._call("save_favorite", { text: this._text });
    const icon = root.getElementById("icon");
    icon.addEventListener("input", (event) => {
      this._icon = event.target.value;
      this._schedulePreview();
    });
    const iconSide = root.getElementById("icon-side");
    iconSide.value = this._iconSide;
    iconSide.addEventListener("change", (event) => {
      this._iconSide = event.target.value;
      this._schedulePreview();
    });

    const withIcon = root.getElementById("with-icon");
    withIcon.checked = this._withIcon;
    withIcon.addEventListener("change", (event) => {
      this._withIcon = event.target.checked;
      this._syncIconControls();
      this._schedulePreview();
    });

    root.getElementById("suggest-icon").onclick = () => this._suggestIcon();
    root.getElementById("artwork-prompt").addEventListener("input", (event) => {
      this._artworkPrompt = event.target.value;
    });
    root.getElementById("draw-icon").onclick = () => this._drawArtwork("icon");
    root.getElementById("draw-artwork").onclick = () => this._drawArtwork("full");
    root.getElementById("clear-artwork").onclick = () => {
      this._artwork = "";
      root.getElementById("clear-artwork").hidden = true;
      this._syncIconControls();
      this._schedulePreview();
    };

    const date = root.getElementById("date");
    date.value = this._date;
    date.disabled = !this._withDate;
    date.addEventListener("input", (event) => {
      this._date = event.target.value;
      this._schedulePreview();
    });
    const withDate = root.getElementById("with-date");
    withDate.checked = this._withDate;
    withDate.addEventListener("change", (event) => {
      this._withDate = event.target.checked;
      date.disabled = !this._withDate;
      this._schedulePreview();
    });
    this._restoreLayout();
    const boldBox = root.getElementById("bold");
    boldBox.checked = this._bold;
    boldBox.addEventListener("change", (event) => {
      this._bold = event.target.checked;
      this._saveLayout();
      this._schedulePreview();
    });
    const length = root.getElementById("length");
    length.value = this._lengthMm;
    length.addEventListener("change", (event) => {
      this._lengthMm = this._commit(event.target, 10, 100, this._lengthMm);
      this._saveLayout();
      this._schedulePreview();
    });
    const offset = root.getElementById("offset");
    offset.value = this._offsetMm;
    offset.addEventListener("change", (event) => {
      this._offsetMm = this._commit(event.target, -10, 10, this._offsetMm);
      this._saveLayout();
      this._schedulePreview();
    });

    this._syncIconControls();
    this._built = true;
    this._schedulePreview();
  }

  async _drawArtwork(mode) {
    const state = this._entityId && this._hass?.states[this._entityId];
    if (!state) return;
    const root = this.shadowRoot;
    const error = root.getElementById("preview-error");
    const source = mode === "icon" ? this._text : this._artworkPrompt;
    if (!source.trim()) {
      error.textContent = mode === "icon"
        ? "Type the label text first."
        : "Describe the picture first.";
      error.hidden = false;
      return;
    }
    // Only the drawing buttons wait for the model: the text stays editable and
    // the label can still be printed while a picture is on its way.
    this._drawing = mode;
    this._syncIconControls();
    try {
      const result = await this._hass.callWS({
        type: "fichero_printer/generate_artwork",
        config_entry_id: state.attributes.config_entry_id,
        mode,
        text: source,
        ...(Number.isFinite(this._lengthMm) ? { length_mm: this._lengthMm } : {}),
      });
      this._artwork = result.artwork;
      this._artworkMode = mode;
      this._syncIconControls();
      root.getElementById("clear-artwork").hidden = false;
      error.hidden = true;
      this._schedulePreview();
    } catch (err) {
      error.textContent = err?.message || String(err);
      error.hidden = false;
    } finally {
      this._drawing = "";
      this._syncIconControls();
    }
  }

  async _suggestIcon() {
    const state = this._entityId && this._hass?.states[this._entityId];
    if (!state) return;
    const root = this.shadowRoot;
    const error = root.getElementById("preview-error");
    this._drawing = "suggest";
    this._syncIconControls();
    try {
      const result = await this._hass.callWS({
        type: "fichero_printer/suggest_icon",
        config_entry_id: state.attributes.config_entry_id,
        text: this._text,
      });
      this._icon = result.icon;
      this._withIcon = true;
      root.getElementById("icon").value = result.icon;
      root.getElementById("with-icon").checked = true;
      this._syncIconControls();
      error.hidden = true;
      this._schedulePreview();
    } catch (err) {
      error.textContent = err?.message || String(err);
      error.hidden = false;
    } finally {
      this._drawing = "";
      this._syncIconControls();
    }
  }

  _schedulePreview() {
    // Typing should not fire a render per keystroke.
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._refreshPreview(), 300);
  }

  async _refreshPreview() {
    const state = this._entityId && this._hass?.states[this._entityId];
    if (!state || !this.shadowRoot) return;
    const preview = this.shadowRoot.getElementById("preview");
    const error = this.shadowRoot.getElementById("preview-error");
    const data = this._printData(this._text);
    if (!data.artwork && !data.text.trim() && !data.date) {
      preview.hidden = true;
      error.hidden = true;
      return;
    }
    const message = {
      type: "fichero_printer/preview",
      config_entry_id: state.attributes.config_entry_id,
      text: data.text,
      date: data.date || "",
      bold: data.bold,
    };
    if (data.icon) {
      message.icon = data.icon;
      message.icon_side = data.icon_side;
    }
    if (data.artwork) {
      message.artwork = data.artwork;
      message.artwork_mode = data.artwork_mode;
      // Without this the preview always drew a generated pictogram on the left.
      if (data.icon_side) message.icon_side = data.icon_side;
    }
    if (data.margin_mm !== undefined) message.margin_mm = data.margin_mm;
    if (data.offset_mm !== undefined) message.offset_mm = data.offset_mm;
    if (data.length_mm !== undefined) message.length_mm = data.length_mm;
    try {
      const result = await this._hass.callWS(message);
      preview.src = `data:image/png;base64,${result.png}`;
      preview.hidden = false;
      error.hidden = true;
    } catch (err) {
      preview.hidden = true;
      error.textContent = err?.message || String(err);
      error.hidden = false;
    }
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
    // The blanket line above would re-enable the buttons that are waiting for a
    // picture, so the drawing state has the last word.
    this._syncIconControls();
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
