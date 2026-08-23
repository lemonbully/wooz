/*
 * Passkey (WebAuthn) client helpers.
 *
 * The WebAuthn API speaks ArrayBuffers; the server speaks base64url JSON.
 * Everything below is that translation plus two ceremonies.
 *
 * No inline handlers and no eval: this file is served from our own origin and
 * every caller is attached with addEventListener, so the strict
 * Content-Security-Policy needs no relaxation for it.
 *
 * Endpoints are read from data attributes on the script tag rather than
 * written here. The dashboard sits at the root of its own host in production
 * but under a /user prefix in single-host development, and a path baked into
 * this file would be wrong in one of those two.
 */
(function () {
  "use strict";

  var script = document.currentScript;

  function endpoint(name) {
    var value = script && script.dataset ? script.dataset[name] : "";
    if (!value) {
      throw new Error(
        "passkeys.js is missing its data-" +
          name.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); }) +
          " attribute."
      );
    }
    return value;
  }

  function base64urlToBuffer(value) {
    var padded = value.replace(/-/g, "+").replace(/_/g, "/");
    while (padded.length % 4) padded += "=";
    var binary = window.atob(padded);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  function bufferToBase64url(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    for (var i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrfToken() {
    var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (field) return field.value;
    var match = document.cookie.match(/(?:^|;\s*)(?:__Host-)?ow_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function post(url, body, extraHeaders) {
    var headers = { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" };
    if (extraHeaders) Object.assign(headers, extraHeaders);
    var response = await fetch(url, {
      method: "POST",
      headers: headers,
      body: body === undefined ? null : body,
      credentials: "same-origin"
    });
    var payload = await response.json();
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "That request could not be completed.");
    }
    return payload;
  }

  function isSupported() {
    return typeof window.PublicKeyCredential === "function" &&
           !!(navigator.credentials && navigator.credentials.create);
  }

  // -- Registration --------------------------------------------------------

  async function register(label) {
    var started = await post(endpoint("registerBegin"));
    var options = JSON.parse(started.options).publicKey || JSON.parse(started.options);

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    (options.excludeCredentials || []).forEach(function (item) {
      item.id = base64urlToBuffer(item.id);
    });

    var credential = await navigator.credentials.create({ publicKey: options });

    var payload = JSON.stringify({
      id: credential.id,
      rawId: bufferToBase64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        attestationObject: bufferToBase64url(credential.response.attestationObject)
      }
    });

    var url = endpoint("registerFinish");
    if (label) url += "?label=" + encodeURIComponent(label);
    return post(url, payload, { "Content-Type": "application/json" });
  }

  // -- Authentication ------------------------------------------------------

  async function signIn() {
    var started = await post(endpoint("authBegin"));
    var options = JSON.parse(started.options).publicKey || JSON.parse(started.options);

    options.challenge = base64urlToBuffer(options.challenge);
    (options.allowCredentials || []).forEach(function (item) {
      item.id = base64urlToBuffer(item.id);
    });

    var assertion = await navigator.credentials.get({ publicKey: options });

    var payload = JSON.stringify({
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      type: assertion.type,
      response: {
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle
          ? bufferToBase64url(assertion.response.userHandle)
          : null
      }
    });

    return post(endpoint("authFinish"), payload, {
      "Content-Type": "application/json",
      // The credential id travels in a header so the view can look up the
      // stored public key before verifying the signature against it.
      "X-Credential-Id": assertion.id
    });
  }

  window.OneWoozPasskeys = {
    isSupported: isSupported,
    register: register,
    signIn: signIn
  };
})();
