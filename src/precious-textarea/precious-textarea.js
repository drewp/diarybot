import {PolymerElement, html} from '@polymer/polymer/polymer-element.js';
import '@polymer/iron-localstorage/iron-localstorage.js';
import '@polymer/iron-autogrow-textarea/iron-autogrow-textarea.js';

class PreciousTextarea extends PolymerElement {
    static get template() {
        return html`
    <iron-localstorage name="{{localId}}" value="{{msg}}"></iron-localstorage>
    <iron-autogrow-textarea
      name="{{name}}"
      value="{{msg}}"
      rows="2"
      style='width: 100%'
      speech="on"></iron-autogrow-textarea>`;
    }

    static get properties() {
        return {
            name: {type: String}, // form name
            localId: {type: String}, // domain-wide unique id for this field
            msg: {type: String},
        };
    }

get value() {
    return this.shadowRoot.lastChild.value;
}

    clear() {
        this.msg = null;
    }
}

customElements.define('precious-textarea', PreciousTextarea);
