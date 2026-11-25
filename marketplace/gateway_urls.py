"""Install gateway on the app surface (namespace ``mkt_gateway``), mounted at
``app.onewooz.com/marketplace/``."""

from django.urls import path

from . import gateway

app_name = "mkt_gateway"

urlpatterns = [
    path("connect/", gateway.connect, name="connect"),
    path("authorize/", gateway.authorize, name="authorize"),
    path("reauth/password/", gateway.reauth_password, name="reauth_password"),
    path("reauth/code/send/", gateway.reauth_code_send, name="reauth_code_send"),
    path("reauth/code/verify/", gateway.reauth_code_verify, name="reauth_code_verify"),
    path("reauth/passkey/begin/", gateway.reauth_passkey_begin, name="reauth_passkey_begin"),
    path("reauth/passkey/finish/", gateway.reauth_passkey_finish, name="reauth_passkey_finish"),
    path("install/", gateway.install, name="install"),
    path("order/return/", gateway.order_return, name="order_return"),
    path("done/", gateway.done, name="done"),
    path("apps/", gateway.my_apps, name="my_apps"),
    path("apps/<slug:slug>/uninstall/", gateway.uninstall, name="uninstall"),
    path("open/<uuid:placement_id>/", gateway.account_page, name="account_page"),
]
