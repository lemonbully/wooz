/* OneWooz dashboard service worker: browser push notifications.
 *
 * Served by accounts.push_views.service_worker from the app origin root, so
 * its scope is the whole owner dashboard. It does one job: show each push
 * (an encrypted bell notification sent by accounts.webpush) as a system
 * notification, and open the right dashboard page when it is clicked. It
 * never intercepts page requests (no fetch handler), so it cannot affect how
 * the dashboard loads.
 */
"use strict";

var ICON = "{{ icon|escapejs }}";
var BADGE = "{{ badge|escapejs }}";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

// Links arrive as app paths ("/store/<id>/orders/…"); resolve them against
// this worker's scope so they work on any host, including the "/user" prefix
// used in local development.
function resolveUrl(u) {
  var scope = new URL(self.registration.scope);
  u = String(u || "");
  if (/^https?:\/\//i.test(u)) return u;
  if (!u) return scope.href;
  if (u.charAt(0) === "/") {
    if (scope.pathname !== "/" && u.indexOf(scope.pathname) === 0) return scope.origin + u;
    return new URL(u.replace(/^\/+/, ""), scope.href).href;
  }
  return new URL(u, scope.href).href;
}

function dashboardClients() {
  var scope = self.registration.scope;
  return self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    return list.filter(function (c) { return String(c.url || "").indexOf(scope) === 0; });
  });
}

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = { title: "OneWooz", body: event.data ? event.data.text() : "" }; }
  var isTest = String(data.id || "").indexOf("test-") === 0;

  event.waitUntil(dashboardClients().then(function (list) {
    // The owner is looking at the dashboard in this browser right now: the
    // page's bell, toast and sound already announce it, so don't pop up a
    // second alert on top of what they are reading.
    var looking = list.some(function (c) { return c.focused && c.visibilityState === "visible"; });
    if (looking && !isTest) {
      list.forEach(function (c) { try { c.postMessage({ type: "ow-push", data: data }); } catch (e) {} });
      return;
    }
    return self.registration.showNotification(data.title || "OneWooz", {
      body: data.body || "",
      icon: ICON,
      badge: BADGE,
      tag: "ow-" + (data.id || Date.now()),
      requireInteraction: !!data.sticky,
      timestamp: data.ts || Date.now(),
      data: { url: resolveUrl(data.url) }
    });
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || self.registration.scope;
  event.waitUntil(dashboardClients().then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url === url && "focus" in list[i]) return list[i].focus();
    }
    // A new tab rather than navigating an open one, so unsaved work in an
    // open dashboard tab (the content editor, a product form) is never lost.
    return self.clients.openWindow(url);
  }));
});

// The browser rotated the subscription (rare). Subscribe again with the same
// key so pushes keep arriving; the next dashboard visit re-registers the new
// endpoint with the server.
self.addEventListener("pushsubscriptionchange", function (event) {
  var old = event.oldSubscription;
  var key = old && old.options && old.options.applicationServerKey;
  if (!key) return;
  event.waitUntil(self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }));
});
