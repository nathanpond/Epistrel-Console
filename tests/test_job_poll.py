"""``console.job_poll.poll_context`` (#64 convention 10) and its M7 ``trigger`` keyword (#295,
E7). The existing callers (#77's offboarding, #80's checks) keep the default "every 2s"."""

from django.template.loader import render_to_string

from console.job_poll import POLL_TRIGGER, SLOW_POLL_TRIGGER, poll_context


def _render(*, polling: bool = True, trigger: str = POLL_TRIGGER) -> str:
    context = poll_context(
        poll_url="/job/?partial=1",
        refresh_url="/job/",
        body_template="console/_job_status.html",
        polling=polling,
        trigger=trigger,
    )
    return render_to_string("console/_job_poll.html", {**context, "job": None, "job_id": "J"})


def test_the_default_trigger_is_unchanged() -> None:
    assert POLL_TRIGGER == "every 2s"
    assert (
        poll_context(poll_url="", refresh_url="", body_template="", polling=True)["poll_trigger"]
        == "every 2s"
    )
    default = poll_context(
        poll_url="/p/", refresh_url="/r/", body_template="console/_job_status.html", polling=True
    )
    html = render_to_string("console/_job_poll.html", {**default, "job": None, "job_id": "J"})
    assert 'hx-trigger="every 2s"' in html


def test_the_slow_trigger_renders_every_5s() -> None:
    assert 'hx-trigger="every 5s"' in _render(trigger=SLOW_POLL_TRIGGER)


def test_a_terminal_render_has_no_trigger() -> None:
    """**Complement.**"""
    html = _render(trigger=SLOW_POLL_TRIGGER, polling=False)
    assert "hx-trigger" not in html
    assert "hx-get" not in html
