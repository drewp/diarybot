import { LitElement, html } from 'lit-element';
import '@polymer/iron-form/iron-form.js';

class StructuredInput extends LitElement {
    static get properties() {
        return {
            config: { type: Object },
            botname: { type: String },
            disableAllButtons: { type: Boolean },
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

        const leavesFeatured = leaves.filter((r) => { return r.labels[0] == 'dose'; });
        const leavesHidden = leaves.filter((r) => { return r.labels[0] != 'dose'; });

        const onSubmit = function(ev) {
            ev.preventDefault();
            this.disableAllButtons = true;
        };
        const path = (row) => {
            return html`<div class="siForm">
                <iron-form @submit="${onSubmit}">
              <form method="POST"
                    action="${this.botname}/structuredInput"
                    >
                <input type="hidden" name="kv" value="${JSON.stringify(row.kv)}">
                <button type="submit" ?disabled=${this.disableAllButtons}>${row.labels.join(' + ')}</button>
              </form>
                </iron-form>
            </div>`;
        };
        return html`
          <style>
.siForm {
display: inline-block;
margin: 3px;
}
            button {
              min-height: 40px;
              min-width: 60px;
            }
.kv {
font-size: 50%;
   word-break: break-all;
}
          </style>
<details>
<summary>
          ${leavesFeatured.map(path)}
</summary>
          ${leavesHidden.map(path)}
</details>
        `;
    }
}

customElements.define('structured-input', StructuredInput);
