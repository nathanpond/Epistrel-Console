"""``{{ job|job_text:stage_text }}`` and ``{{ error|job_error }}`` for
``console/_job_status.html`` (E7, ``console.jobs``)."""

from typing import Any

from django import template

from console.jobs import JobError, JobState, job_error

register = template.Library()


@register.filter(name="job_text")
def job_text(job: Any, stage_text: Any = "") -> str:
    """The job's status text, with ``stage_text`` in place of the humanised stage when given."""
    if not isinstance(job, JobState):
        return ""
    return job.text_with_stage(stage_text if isinstance(stage_text, str) else "")


@register.filter(name="job_error")
def job_error_filter(error: Any) -> JobError | None:
    return job_error(error)
