from __future__ import annotations

import argparse
from typing import Any

from rf_platform.common.config import get_settings
from rf_platform.dashboard.api_client import DashboardApiClient
from rf_platform.dashboard.tabs.alerts import render_alerts
from rf_platform.dashboard.tabs.jobs import render_jobs
from rf_platform.dashboard.tabs.logs import render_logs
from rf_platform.dashboard.tabs.outputs import render_outputs
from rf_platform.dashboard.tabs.overview import render_overview
from rf_platform.dashboard.tabs.query import ask_rf
from rf_platform.dashboard.tabs.sensors import render_sensors
from rf_platform.dashboard.tabs.storage import render_storage


def build_dashboard():  # type: ignore[no-untyped-def]
    import gradio as gr

    settings = get_settings()
    client = DashboardApiClient(settings)
    with gr.Blocks(title="RF Intelligence Platform") as demo:
        gr.Markdown(
            "# RF Intelligence Platform\nModel outputs are observations, not verified ground truth."
        )
        with gr.Tab("Overview"):
            overview = gr.Textbox(label="Overview", lines=8)
            overview_button: Any = gr.Button("Refresh overview")
            overview_button.click(lambda: render_overview(client), outputs=overview)
        with gr.Tab("Sensors"):
            sensors = gr.JSON(label="Sensors")
            sensors_button: Any = gr.Button("Refresh sensors")
            sensors_button.click(lambda: render_sensors(client), outputs=sensors)
        with gr.Tab("Storage"):
            storage = gr.JSON(label="Storage")
            storage_button: Any = gr.Button("Refresh storage")
            storage_button.click(lambda: render_storage(client), outputs=storage)
        with gr.Tab("Jobs"):
            jobs = gr.JSON(label="Jobs")
            jobs_button: Any = gr.Button("Refresh jobs")
            jobs_button.click(lambda: render_jobs(client), outputs=jobs)
        with gr.Tab("RF-GPT outputs"):
            outputs = gr.JSON(label="Outputs")
            outputs_button: Any = gr.Button("Refresh outputs")
            outputs_button.click(lambda: render_outputs(client), outputs=outputs)
        with gr.Tab("Logs"):
            logs = gr.JSON(label="Logs")
            logs_button: Any = gr.Button("Refresh logs")
            logs_button.click(lambda: render_logs(client), outputs=logs)
        with gr.Tab("Alerts"):
            alerts = gr.JSON(label="Alerts")
            alerts_button: Any = gr.Button("Refresh alerts")
            alerts_button.click(lambda: render_alerts(client), outputs=alerts)
        with gr.Tab("Ask RF"):
            question = gr.Textbox(label="Question", value="What happened today?")
            timezone = gr.Textbox(label="Timezone", value=settings.timezone)
            answer = gr.JSON(label="Evidence-backed answer")
            ask_button: Any = gr.Button("Ask")
            ask_button.click(
                lambda q, tz: ask_rf(client, q, tz), inputs=[question, timezone], outputs=answer
            )
    return demo


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run RF dashboard")
    parser.parse_args()
    settings = get_settings()
    dashboard = build_dashboard()
    dashboard.launch(
        server_name=settings.dashboard_host,
        server_port=settings.dashboard_port,
        share=settings.gradio_share,
    )


if __name__ == "__main__":
    cli()
