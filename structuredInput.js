import { LitElement, html } from '/lib/lit-element/2.2.0/lit-element-custom.js';

class StructuredInput extends LitElement {
    static get properties() {
        return {
            config: { type: Object },
            botname: { type: String },
        };
    }
    _readConfig(config) {
        const leaves = [];
        const walk = (n, labels, kv) => {
            let kvs2 = {...kv, ...(n.kv || {})};
            if (!n.choices) {
                leaves.push({labels: n.label ? labels.concat(n.label) : labels, kv: kvs2});
            } else {
                for (let ch of n.choices) {
                    walk(ch, n.label ? labels.concat(n.label) : labels, kvs2);
                }
            }
        };

        walk(this.config, [], {});
        return leaves;
    }
    render() {
        if (!this.config || !this.config.choices) {
            return html`loading...`;
        }

        const leaves = this._readConfig(this.config);
        const path = (row) => {
            return html`<div>
              <form is="iron-form" method="POST"
                    action="${this.botname}/structuredInput"
                    on-iron-form-response="onResponse">
                <input type="hidden" name="kv" value="${JSON.stringify(row.kv)}">
                <button type="submit">${row.labels.join(' + ')}</button> ${JSON.stringify(row.kv)}
              </form>
            </div>`;
        };
        return html`
          <style>
            button {
              min-height: 40px;
              min-width: 60px;
            }
          </style>
          ${leaves.map(path)}
        `;
    }
}

customElements.define('structured-input', StructuredInput);
