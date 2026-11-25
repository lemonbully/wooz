"""Routes for Ask OneWooz, mounted at onewooz.com/ask/ (marketing surface).

URL names carry an ``ask_``-style prefix or are unique words, because the
marketing SEO registry keys pages by bare URL name (``home`` is the site home).
"""

from __future__ import annotations

from django.urls import path

from . import bridge, desk, views, weekly

app_name = "ask"

urlpatterns = [
    path("", views.home, name="ask_home"),
    path("live/", views.live, name="live"),
    path("live/columns/", views.live_columns, name="live_columns"),
    path("weekly/", weekly.weekly, name="weekly"),
    path("weekly/<int:year>/<int:week>/", weekly.weekly, name="weekly_issue"),
    path("weekly/subscribe/", weekly.weekly_subscribe, name="weekly_subscribe"),
    path("weekly/confirm/<str:token>/", weekly.weekly_confirm, name="weekly_confirm"),
    path("weekly/unsubscribe/<str:token>/", weekly.weekly_unsubscribe, name="weekly_unsubscribe"),
    path("notify/off/<str:token>/", weekly.notify_off, name="notify_off"),
    path("desk/", desk.desk, name="desk"),
    path("desk/suggest/<slug:slug>/", desk.desk_suggest, name="desk_suggest"),
    path("search.json", views.search_json, name="search_json"),
    path("new/", views.ask_new, name="ask_new"),
    path("instant/<str:token>/", views.instant, name="instant"),
    path("instant/<str:token>/status/", views.instant_status, name="instant_status"),
    path("instant/<str:token>/post/", views.instant_post, name="instant_post"),
    path("topics/", views.topics, name="topics"),
    path("topics/<slug:slug>/", views.topic, name="topic"),
    path("q/<slug:slug>/", views.question, name="question"),
    path("q/<slug:slug>/answer/", views.answer_create, name="answer_create"),
    path("q/<slug:slug>/vote/", views.question_vote, name="question_vote"),
    path("a/<uuid:pk>/vote/", views.answer_vote, name="answer_vote"),
    path("a/<uuid:pk>/accept/", views.answer_accept, name="answer_accept"),
    path("a/<uuid:pk>/reply/", views.reply_create, name="reply_create"),
    path("report/<str:kind>/<uuid:pk>/", views.report, name="report"),
    path("u/<slug:handle>/", views.profile, name="profile"),
    path("me/", views.profile_update, name="profile_update"),
    path("join/", views.join, name="join"),
    path("leave/", views.leave, name="leave"),
    # Signed-in OneWooz users: redeem the dashboard's one-time hand-off.
    path("link/", bridge.redeem, name="link"),
]
