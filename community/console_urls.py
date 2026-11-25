"""Ask OneWooz console routes (namespace ``ask_console``), mounted on the admin
surface at ``/ask/`` behind AdminAccessMiddleware."""

from django.urls import path

from . import authoring, console

app_name = "ask_console"

urlpatterns = [
    path("", console.overview, name="overview"),
    path("moderation/", console.queue, name="queue"),
    path("post/<str:kind>/<uuid:pk>/", console.post_action, name="post_action"),
    path("questions/", console.questions, name="questions"),
    path("questions/new/", authoring.question_new, name="question_new"),
    path("questions/<uuid:pk>/", console.thread, name="thread"),
    path("questions/<uuid:pk>/answer/", console.thread_answer, name="thread_answer"),
    path("answers/<uuid:pk>/edit/", authoring.answer_edit, name="answer_edit"),
    path("members/", console.members, name="members"),
    path("members/<uuid:pk>/", console.member_detail, name="member"),
    path("members/<uuid:pk>/action/", console.member_action, name="member_action"),
    path("topics/", console.topics, name="topics"),
    path("topics/<uuid:pk>/save/", console.topic_save, name="topic_save"),
    path("topics/<uuid:pk>/delete/", console.topic_delete, name="topic_delete"),
    path("instant/", console.instant, name="instant"),
    path("digest/", console.digest_view, name="digest"),
    path("digest/<uuid:pk>/action/", console.subscriber_action, name="subscriber_action"),
    path("settings/", console.settings_view, name="settings"),
    path("activity/", console.activity, name="activity"),
]
