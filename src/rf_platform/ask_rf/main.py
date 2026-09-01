from __future__ import annotations

import argparse
from typing import Any

import httpx

from rf_platform.ask_rf.api_client import AskRFApiClient
from rf_platform.common.config import get_settings
from rf_platform.common.logging import configure_logging, get_logger
from rf_platform.contracts.api import AskRFResponse

EXAMPLES = [
    "What technologies are nearby?",
    "Was anything unusual this morning?",
    "Did the system observe Bluetooth activity?",
]
ASK_RF_CSS = """
:root,
html,
body,
.gradio-container,
.main,
.app,
.wrap,
.block,
.panel,
.form,
.prose {
  background: #ffffff !important;
  color: #0f172a !important;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
  box-sizing: border-box;
}
*,
*::before,
*::after { box-sizing: border-box; }
html, body { overflow-x: hidden; }
.gradio-container {
  max-width: 1040px !important;
  width: 100% !important;
  margin: 0 auto !important;
  padding: 1rem !important;
}
.askrf-hero {
  padding: 1.75rem 1.5rem 1.35rem 1.5rem;
  background: linear-gradient(135deg, #ffffff 0%, #eff6ff 100%);
  border: 1px solid #dbeafe;
  border-radius: 22px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.07);
  color: #0f172a !important;
}
.askrf-title {
  color: #06152b !important;
  font-size: clamp(2.2rem, 5vw, 4.1rem);
  font-weight: 800;
  letter-spacing: -0.05em;
  margin: 0;
  line-height: 1;
}
.askrf-subtitle { color: #0369a1; font-size: 1.35rem; margin-top: .25rem; }
.askrf-status-live { color: #047857; font-weight: 800; }
.askrf-status-down { color: #b91c1c; font-weight: 800; }
.askrf-heading {
  color: #0f172a !important;
  font-size: clamp(1.7rem, 3.6vw, 3rem);
  font-weight: 760;
  letter-spacing: -0.04em;
  margin-top: 1rem;
  line-height: 1.08;
}
.askrf-supporting {
  color: #1f2937 !important;
  font-size: 1.1rem;
  line-height: 1.55;
  max-width: 760px;
  margin-bottom: .35rem;
}
.askrf-notice { color: #334155 !important; font-size: .95rem; margin-top: .75rem; }
.askrf-card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 20px;
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.07);
  padding: 1.25rem 1.35rem;
  color: #0f172a !important;
  width: 100%;
  max-width: 100%;
  margin-top: .35rem;
}
.askrf-answer,
.askrf-answer p {
  font-size: 1.2rem;
  line-height: 1.6;
  color: #111827 !important;
  margin: 0 0 .85rem 0;
}
.askrf-labels {
  color: #334155 !important;
  font-size: .98rem;
  margin: .6rem 0 0 0;
}
.askrf-question {
  color: #075985 !important;
  font-weight: 750;
  margin: .6rem 0 .35rem 0;
  overflow-wrap: anywhere;
}
.askrf-question-field,
.askrf-question-field textarea,
.askrf-question-field input {
  color: #0f172a !important;
  background: #ffffff !important;
  border-color: #bfdbfe !important;
  border-radius: 18px !important;
  max-width: 100% !important;
}
.askrf-question-field textarea,
.askrf-question-field input {
  min-height: 6.5rem !important;
  padding: 1rem 1.1rem !important;
  border: 1px solid #bfdbfe !important;
  font-size: 1.2rem !important;
  line-height: 1.5 !important;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
}
.askrf-question-field textarea:focus,
.askrf-question-field input:focus {
  border-color: #0284c7 !important;
  outline: 3px solid rgba(14, 165, 233, 0.35) !important;
  outline-offset: 2px !important;
  box-shadow: 0 0 0 4px rgba(14, 165, 233, 0.14) !important;
}
.askrf-actions,
.askrf-examples {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: .6rem !important;
  width: 100% !important;
  max-width: 100% !important;
}
.askrf-actions .askrf-primary {
  flex: 1 1 22rem !important;
  min-width: min(100%, 18rem) !important;
}
.askrf-actions .askrf-secondary {
  flex: 0 1 13rem !important;
  min-width: min(100%, 12rem) !important;
}
.askrf-primary button,
.askrf-primary {
  background: #0284c7 !important;
  color: #ffffff !important;
  border: 1px solid #0284c7 !important;
  border-radius: 999px !important;
  box-shadow: 0 8px 18px rgba(2, 132, 199, 0.2) !important;
  font-weight: 760 !important;
}
.askrf-primary button {
  width: 100% !important;
  min-height: 3.1rem !important;
}
.askrf-secondary button,
.askrf-secondary,
.askrf-example button,
.askrf-example {
  background: #ffffff !important;
  color: #0f172a !important;
  border: 1px solid #bfdbfe !important;
  border-radius: 999px !important;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04) !important;
  font-weight: 650 !important;
  white-space: normal !important;
}
.askrf-example,
.askrf-example button {
  color: #075985 !important;
}
textarea, input {
  background: #ffffff !important;
  color: #0f172a !important;
  border-color: #cbd5e1 !important;
  max-width: 100% !important;
}
.askrf-disclosure,
.askrf-disclosure *,
.askrf-disclosure button,
.askrf-disclosure summary {
  background: #ffffff !important;
  color: #0f172a !important;
  border-color: #d1d5db !important;
}
.askrf-disclosure {
  border: 1px solid #d1d5db !important;
  border-radius: 16px !important;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.04) !important;
  margin-top: .5rem !important;
}
.askrf-answer-region {
  width: 100% !important;
  max-width: 100% !important;
}
footer,
.footer,
[data-testid="footer"] {
  display: none !important;
}
@media (max-width: 760px) {
  .gradio-container { padding: .75rem !important; }
  .askrf-hero { padding: 1.35rem 1rem 1rem 1rem; border-radius: 18px; }
  .askrf-card { padding: 1rem; }
  .askrf-actions > *,
  .askrf-examples > * { min-width: min(100%, 14rem) !important; }
}
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


def visibility_update(visible: bool) -> dict[str, Any]:
    return {"__type__": "update", "visible": visible}


def submit_question(
    client: AskRFApiClient,
    question: str,
    context: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], str, str, str, dict[str, Any]]:
    if not question.strip():
        question = "What technologies are nearby?"
    try:
        response = client.query(question, context or None)
    except httpx.HTTPError as exc:
        logger.warning("ask_rf_api_unavailable", error=exc.__class__.__name__)
        question_html, answer_html, details, response_context = unavailable_response(
            question, client.display_timezone
        )
        return (
            visibility_update(True),
            visibility_update(True),
            question_html,
            answer_html,
            details,
            response_context,
        )
    question_html, answer_html, details, response_context = render_answer(response, question)
    return (
        visibility_update(True),
        visibility_update(True),
        question_html,
        answer_html,
        details,
        response_context,
    )


def reset_conversation() -> tuple[
    dict[str, Any], dict[str, Any], str, str, str, dict[str, Any], str
]:
    return visibility_update(False), visibility_update(False), "", "", "", {}, ""


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
    with gr.Blocks(title="Ask RF") as app:
        context_state = gr.State({})
        hero = gr.HTML(landing_markup(status_markup(client)))
        question = gr.Textbox(
            label="Question",
            show_label=False,
            placeholder="Ask about wireless activity or a time period…",
            lines=3,
            interactive=True,
            elem_classes=["askrf-question-field"],
        )
        with gr.Row(elem_classes=["askrf-actions"]):
            ask_button = gr.Button("Ask RF", variant="primary", elem_classes=["askrf-primary"])
            new_button = gr.Button("New question", visible=False, elem_classes=["askrf-secondary"])
        with gr.Row(elem_classes=["askrf-examples"]):
            example_buttons = [
                gr.Button(example, elem_classes=["askrf-example"]) for example in EXAMPLES
            ]
        with gr.Group(visible=False, elem_classes=["askrf-answer-region"]) as answer_region:
            visible_question = gr.HTML("")
            answer_card = gr.HTML("")
            with gr.Accordion(
                "How was this determined?", open=False, elem_classes=["askrf-disclosure"]
            ):
                details = gr.Markdown("")

        ask_button.click(
            lambda q, ctx: submit_question(client, q, ctx),
            inputs=[question, context_state],
            outputs=[
                answer_region,
                new_button,
                visible_question,
                answer_card,
                details,
                context_state,
            ],
        )
        question.submit(
            lambda q, ctx: submit_question(client, q, ctx),
            inputs=[question, context_state],
            outputs=[
                answer_region,
                new_button,
                visible_question,
                answer_card,
                details,
                context_state,
            ],
        )
        for button, example in zip(example_buttons, EXAMPLES, strict=True):
            button.click(lambda text=example: text, outputs=question)
        new_button.click(
            reset_conversation,
            outputs=[
                answer_region,
                new_button,
                visible_question,
                answer_card,
                details,
                context_state,
                question,
            ],
        )
        app.load(lambda: landing_markup(status_markup(client)), outputs=hero)
    return app


def cli() -> None:
    import gradio as gr

    parser = argparse.ArgumentParser(description="Run Ask RF presentation interface")
    parser.parse_args()
    settings = get_settings()
    app = build_app()
    app.launch(
        server_name=settings.ask_rf_host,
        server_port=settings.ask_rf_port,
        share=settings.gradio_share,
        theme=gr.themes.Soft(primary_hue="sky"),
        css=ASK_RF_CSS,
        footer_links=[],
    )


if __name__ == "__main__":
    cli()
