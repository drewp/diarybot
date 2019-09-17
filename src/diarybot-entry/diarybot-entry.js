import { PolymerElement, html } from '@polymer/polymer';
import '@polymer/iron-form/iron-form.js';
import '../structured-input/structured-input.js';
import '../precious-textarea/precious-textarea.js'

class DiarybotEntry extends PolymerElement {
  static get template() {
    return html`

    <style>
     #status {
         white-space: pre-line;
     }
    </style>
    <h2>{{botName}}</h2>
    <div><a href="{{botName}}/history/recent">history</a></div>
    <div id="status">{{status}}</div>
    <div>New entry:

      <span class="autotext">
        <structured-input botname="{{botName}}" config="{{structuredInput}}"></structured-input>
      </span>

    </div>
    <iron-form on-iron-form-response="onResponse">
    <form class="text-form" method="POST" action="{{botName}}/message">
      <div>
        <precious-textarea name="msg" local-id="unsent-{{botName}}"></precious-textarea>
      </div>
      <div><input type="submit" value="Send"></div>
    </form>
    </iron-form>

`;
  }
  static get properties() {
    return {
      botName: { type: String },
      status: { type: String },
      structuredInput: { type: Object }
    };
  }

  onResponse(ev) {
    if (ev.detail.status == 200) {
      this.shadowRoot.querySelector('precious-textarea').clear();
      document.body.textContent = 'saved';
    }
  }
}

customElements.define('diarybot-entry', DiarybotEntry);
