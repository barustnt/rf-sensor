from __future__ import annotations

import argparse
from typing import Any

import httpx

from rf_platform.ask_rf.api_client import AskRFApiClient
from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.contracts.api import AskRFResponse

NOTICE = "AI-assisted RF observation—not independently confirmed ground truth."
EXAMPLES = [
    "What technologies are nearby?",
    "Was anything unusual this morning?",
    "Did the system observe Bluetooth activity?",
]
ASK_RF_CSS = """
:root, body, .gradio-container {
  background: #ffffff !important;
  color: #0f172a !important;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
}
.gradio-container { max-width: 1040px !important; margin: 0 auto !important; }
.askrf-hero {
  padding: 3rem 2rem 2rem 2rem;
  background: linear-gradient(135deg, #ffffff 0%, #eff6ff 100%);
  border: 1px solid #dbeafe;
  border-radius: 28px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.08);
}
.askrf-title {
  font-size: clamp(2.4rem, 6vw, 4.8rem);
  font-weight: 800;
  letter-spacing: -0.05em;
  margin: 0;
}
.askrf-subtitle { color: #0369a1; font-size: 1.35rem; margin-top: .25rem; }
.askrf-status-live { color: #047857; font-weight: 800; }
.askrf-status-down { color: #b91c1c; font-weight: 800; }
.askrf-heading {
  font-size: clamp(2rem, 4vw, 3.4rem);
  font-weight: 760;
  letter-spacing: -0.04em;
  margin-top: 2rem;
}
.askrf-supporting { color: #334155; font-size: 1.15rem; line-height: 1.7; max-width: 760px; }
.askrf-notice { color: #475569; font-size: .95rem; margin-top: 1.25rem; }
.askrf-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 24px;
  box-shadow: 0 12px 34px rgba(15, 23, 42, 0.08);
  padding: 1.5rem;
  color: #0f172a;
}
.askrf-answer { font-size: 1.25rem; line-height: 1.7; color: #111827; }
.askrf-labels { color: #475569; font-size: .98rem; }
.askrf-question { color: #075985; font-weight: 700; }
button.primary, .primary button {
  background: #0284c7 !important;
  color: #ffffff !important;
  border-radius: 999px !important;
}
textarea, input {
  background: #ffffff !important;
  color: #0f172a !important;
  border-color: #cbd5e1 !important;
}
.wrap, .block, .panel { background: #ffffff !important; }
@media (max-width: 640px) { .askrf-hero { padding: 2rem 1rem; border-radius: 18px; } }
"""

logger = get_logger("rf_platform.ask_rf")


def status_markup(client: AskRFApiClient) -> str:
    return (
        '<span class="askrf-status-live">● System live</span>'
        if client.ready()
        else '<span class="askrf-status-down">● System unavailable</span>'
    )


def landing_markup(status: str) -> str:
    return f"""
<div class="askrf-hero">
  <p class="askrf-title">Ask RF</p>
  <p class="askrf-subtitle">RF environment assistant</p>
  <p>{status}</p>
  <div class="askrf-heading">What would you like to know?</div>
  <p class="askrf-supporting">
    Ask about wireless activity, nearby technologies, or what happened at a specific time.
  </p>
  <p class="askrf-notice">{NOTICE}</p>
</div>
"""


def render_answer(response: AskRFResponse, question: str) -> tuple[str, str, str, dict[str, Any]]:
    question_html = f'<div class="askrf-question">You asked: {escape_visible(question)}</div>'
    answer_html = f"""
<div class="askrf-card">
  <div class="askrf-answer">{paragraphs(response.display_answer)}</div>
  <p class="askrf-labels">
    Time: {escape_visible(response.time_label)}
    · Location: {escape_visible(response.location_label)}
  </p>
  <p class="askrf-notice">{NOTICE}</p>
</div>
"""
    detail_lines = [escape_visible(response.evidence_explanation)]
    if response.limitations:
        detail_lines.append("Limitations:")
        detail_lines.extend(f"- {escape_visible(item)}" for item in response.limitations)
    details = "\n".join(detail_lines)
    return question_html, answer_html, details, response.follow_up_context


def unavailable_response(
    question: str, display_timezone: str
) -> tuple[str, str, str, dict[str, Any]]:
    response = AskRFResponse(
        answer_status="unavailable",
        display_answer=(
            "Ask RF is temporarily unavailable because the platform API is not ready. "
            "Please try again after the system is live."
        ),
        interpreted_interval={
            "start_utc": "2026-01-01T00:00:00+00:00",
            "end_utc": "2026-01-01T01:00:00+00:00",
            "display_timezone": display_timezone,
            "assumptions": [],
        },
        time_label="unavailable",
        location_label="monitored area",
        evidence_explanation="The Ask RF server could not reach the platform API.",
        limitations=["No technical exception details are shown in Ask RF."],
        follow_up_context={},
    )
    return render_answer(response, question)


def submit_question(
    client: AskRFApiClient,
    question: str,
    context: dict[str, Any] | None,
) -> tuple[str, str, str, dict[str, Any]]:
    if not question.strip():
        question = "What technologies are nearby?"
    try:
        response = client.query(question, context or None)
    except httpx.HTTPError as exc:
        logger.warning("ask_rf_api_unavailable", error=exc.__class__.__name__)
        return unavailable_response(question, client.display_timezone)
    return render_answer(response, question)


def reset_conversation() -> tuple[str, str, str, dict[str, Any], str]:
    return "", "", "", {}, ""


def escape_visible(value: object) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def paragraphs(text: str) -> str:
    return "".join(f"<p>{escape_visible(part)}</p>" for part in text.split("\n\n") if part.strip())


def build_app():  # type: ignore[no-untyped-def]
    import gradio as gr

    configure_logging()
    settings = get_settings()
    client = AskRFApiClient(settings)
    with gr.Blocks(title="Ask RF", css=ASK_RF_CSS, theme=gr.themes.Soft(primary_hue="sky")) as app:
        context_state = gr.State({})
        hero = gr.HTML(landing_markup(status_markup(client)))
        question = gr.Textbox(
            label="",
            placeholder="Ask about wireless activity or a time period…",
            lines=3,
            elem_classes=["askrf-question-field"],
        )
        with gr.Row():
            ask_button = gr.Button("Ask RF", variant="primary", elem_classes=["primary"])
            new_button = gr.Button("New question")
        with gr.Row():
            example_buttons = [gr.Button(example) for example in EXAMPLES]
        visible_question = gr.HTML("")
        answer_card = gr.HTML("")
        with gr.Accordion("How was this determined?", open=False):
            details = gr.Markdown("")

        ask_button.click(
            lambda q, ctx: submit_question(client, q, ctx),
            inputs=[question, context_state],
            outputs=[visible_question, answer_card, details, context_state],
        )
        question.submit(
            lambda q, ctx: submit_question(client, q, ctx),
            inputs=[question, context_state],
            outputs=[visible_question, answer_card, details, context_state],
        )
        for button, example in zip(example_buttons, EXAMPLES, strict=True):
            button.click(lambda text=example: text, outputs=question)
        new_button.click(
            reset_conversation,
            outputs=[visible_question, answer_card, details, context_state, question],
        )
        app.load(lambda: landing_markup(status_markup(client)), outputs=hero)
    return app


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run Ask RF presentation interface")
    parser.parse_args()
    settings = get_settings()
    app = build_app()
    app.launch(
        server_name=settings.ask_rf_host,
        server_port=settings.ask_rf_port,
        share=settings.gradio_share,
    )


if __name__ == "__main__":
    cli()
