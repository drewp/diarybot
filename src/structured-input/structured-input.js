import { LitElement, html, css } from 'lit-element';
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
                leaves.push({labels: n.label ? labels.concat(n.label) :
                             labels,
                             kv: kvs2});
            } else {
                for (let ch of n.choices) {
                    walk(ch, n.label ? labels.concat(n.label) : labels,
                         kvs2);
                }
            }
        };

        walk(this.config, [], {});
        leaves.sort((a, b) => {
            const aj = a.labels.join(''),
                  bj = b.labels.join('');
            return aj < bj ? -1 : (aj === bj ? 0 : 1);
        });
        return leaves;
    }
    onSubmit(ev) {
        this.disableAllButtons = true;
    }
    onResponse(ev) {
        if (ev.detail.status == 200) {
            window.location.href = ev.detail.xhr.responseURL;
        }
    }
    static get styles() {
        return css`
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
        `;
    }
    render() {
        if (!this.config || !this.config.choices) {
            return html`loading...`;
        }

        const leaves = this._readConfig(this.config);

        const isFeatured = (r) => { return r.labels[0] == 'dose' || r.labels[0] == 'activity'; };

        const leavesFeatured = leaves.filter(isFeatured);
        const leavesHidden = leaves.filter((r) => { return !isFeatured(r); });

        const path = (row) => {
            return html`
  <div class="siForm">
    <iron-form @iron-form-submit="${this.onSubmit}"
               @iron-form-response="${this.onResponse}">
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
