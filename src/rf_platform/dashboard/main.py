from __future__ import annotations

import argparse
from typing import Any

from rf_platform.common.config import get_settings
from rf_platform.dashboard.api_client import DashboardApiClient
from rf_platform.dashboard.tabs.alerts import render_alerts, update_alert_status
from rf_platform.dashboard.tabs.jobs import render_job_list, render_jobs, retry_job
from rf_platform.dashboard.tabs.logs import render_logs
from rf_platform.dashboard.tabs.operations import render_metrics, run_retention_report
from rf_platform.dashboard.tabs.outputs import render_output_detail, render_outputs
from rf_platform.dashboard.tabs.overview import render_overview
from rf_platform.dashboard.tabs.sensors import render_sensors
from rf_platform.dashboard.tabs.storage import render_storage, render_storage_history


def build_dashboard():  # type: ignore[no-untyped-def]
    import gradio as gr

    settings = get_settings()
    client = DashboardApiClient(settings)

    def _render_filtered_outputs(
        sensor: Any,
        profile: Any,
        location: Any,
        tech: Any,
        model: Any,
        prompt: Any,
        status: Any,
        start: Any,
        end: Any,
        limit: Any,
        offset: Any,
    ) -> list[dict[str, object]]:
        return render_outputs(
            client,
            sensor_id=sensor or None,
            profile_id=profile or None,
            location=location or None,
            technology=tech or None,
            model_version=model or None,
            prompt_version=prompt or None,
            status=status or None,
            start_utc=start or None,
            end_utc=end or None,
            limit=int(limit or 25),
            offset=int(offset or 0),
        )

    with gr.Blocks(title="RF Command Center") as demo:
        gr.Markdown(
            "# RF Command Center\nModel outputs are observations, not verified ground truth."
        )
        with gr.Tab("Overview"):
            overview = gr.Textbox(label="Overview", lines=10)
            overview_button: Any = gr.Button("Refresh overview")
            overview_button.click(lambda: render_overview(client), outputs=overview)
        with gr.Tab("Sensors"):
            sensor_status = gr.Textbox(label="Status filter", placeholder="online/offline/degraded")
            sensor_limit = gr.Number(label="Limit", value=50, precision=0)
            sensor_offset = gr.Number(label="Offset", value=0, precision=0)
            sensors = gr.JSON(label="Sensors")
            sensors_button: Any = gr.Button("Refresh sensors")
            sensors_button.click(
                lambda status, limit, offset: render_sensors(
                    client, status or None, int(limit or 50), int(offset or 0)
                ),
                inputs=[sensor_status, sensor_limit, sensor_offset],
                outputs=sensors,
            )
        with gr.Tab("Storage"):
            storage = gr.JSON(label="Storage summary, warnings, trend")
            storage_button: Any = gr.Button("Refresh storage")
            storage_button.click(lambda: render_storage(client), outputs=storage)
            target_type = gr.Textbox(label="History target type", placeholder="central or sensor")
            target_id = gr.Textbox(label="History target ID", placeholder="laptop-all-in-one")
            storage_history_limit = gr.Number(label="History limit", value=50, precision=0)
            storage_history_offset = gr.Number(label="History offset", value=0, precision=0)
            storage_history = gr.JSON(label="Storage history")
            history_button: Any = gr.Button("Refresh history")
            history_button.click(
                lambda tt, tid, limit, offset: render_storage_history(
                    client,
                    tt or None,
                    tid or None,
                    int(limit or 50),
                    int(offset or 0),
                ),
                inputs=[target_type, target_id, storage_history_limit, storage_history_offset],
                outputs=storage_history,
            )
        with gr.Tab("Jobs"):
            jobs = gr.JSON(label="Job summary")
            jobs_button: Any = gr.Button("Refresh jobs")
            jobs_button.click(lambda: render_jobs(client), outputs=jobs)
            job_status = gr.Textbox(
                label="Job status filter", placeholder="failed/deadletter/pending"
            )
            job_limit = gr.Number(label="Job limit", value=50, precision=0)
            job_offset = gr.Number(label="Job offset", value=0, precision=0)
            job_list = gr.JSON(label="Jobs")
            job_list_button: Any = gr.Button("List jobs")
            job_list_button.click(
                lambda status, limit, offset: render_job_list(
                    client, status or None, int(limit or 50), int(offset or 0)
                ),
                inputs=[job_status, job_limit, job_offset],
                outputs=job_list,
            )
            retry_id = gr.Textbox(label="Retry eligible job ID")
            retry_actor = gr.Textbox(label="Actor", value="operator")
            retry_comment = gr.Textbox(label="Retry comment")
            retry_result = gr.JSON(label="Retry result")
            retry_button: Any = gr.Button("Retry failed/dead-letter job")
            retry_button.click(
                lambda jid, actor, comment: retry_job(client, jid, actor, comment),
                inputs=[retry_id, retry_actor, retry_comment],
                outputs=retry_result,
            )
        with gr.Tab("RF-GPT outputs"):
            gr.Markdown("Model output is not automatically verified ground truth.")
            output_sensor = gr.Textbox(label="Sensor filter")
            output_profile = gr.Textbox(label="Profile filter", placeholder="campus_general")
            output_location = gr.Textbox(label="Location filter")
            output_tech = gr.Textbox(label="Technology filter", placeholder="bluetooth-like")
            output_model = gr.Textbox(label="Model version filter", placeholder="mock-v1")
            output_prompt = gr.Textbox(label="Prompt version filter")
            output_status = gr.Textbox(label="Status filter")
            output_start = gr.Textbox(label="Start UTC", placeholder="2026-08-25T00:00:00Z")
            output_end = gr.Textbox(label="End UTC", placeholder="2026-08-26T00:00:00Z")
            output_limit = gr.Number(label="Limit", value=25, precision=0)
            output_offset = gr.Number(label="Offset", value=0, precision=0)
            outputs = gr.JSON(label="Output rows")
            outputs_button: Any = gr.Button("Refresh outputs")
            outputs_button.click(
                _render_filtered_outputs,
                inputs=[
                    output_sensor,
                    output_profile,
                    output_location,
                    output_tech,
                    output_model,
                    output_prompt,
                    output_status,
                    output_start,
                    output_end,
                    output_limit,
                    output_offset,
                ],
                outputs=outputs,
            )
            analysis_id = gr.Textbox(label="Analysis ID for readable detail")
            detail = gr.Markdown(label="Readable RF-GPT output detail")
            detail_button: Any = gr.Button("Load output detail")
            detail_button.click(
                lambda aid: render_output_detail(client, aid), inputs=analysis_id, outputs=detail
            )
        with gr.Tab("Logs"):
            log_severity = gr.Textbox(label="Severity filter")
            log_limit = gr.Number(label="Limit", value=50, precision=0)
            log_offset = gr.Number(label="Offset", value=0, precision=0)
            logs = gr.JSON(label="Logs")
            logs_button: Any = gr.Button("Refresh logs")
            logs_button.click(
                lambda severity, limit, offset: render_logs(
                    client, severity or None, int(limit or 50), int(offset or 0)
                ),
                inputs=[log_severity, log_limit, log_offset],
                outputs=logs,
            )
        with gr.Tab("Alerts"):
            alert_status = gr.Textbox(
                label="Status filter", placeholder="open/acknowledged/dismissed/confirmed"
            )
            alert_limit = gr.Number(label="Limit", value=50, precision=0)
            alert_offset = gr.Number(label="Offset", value=0, precision=0)
            alerts = gr.JSON(label="Alerts")
            alerts_button: Any = gr.Button("Refresh alerts")
            alerts_button.click(
                lambda status, limit, offset: render_alerts(
                    client, status or None, int(limit or 50), int(offset or 0)
                ),
                inputs=[alert_status, alert_limit, alert_offset],
                outputs=alerts,
            )
            alert_id = gr.Textbox(label="Alert ID")
            new_status = gr.Radio(["acknowledged", "dismissed", "confirmed"], label="Decision")
            actor = gr.Textbox(label="Actor", value="operator")
            comment = gr.Textbox(label="Annotation/comment")
            alert_result = gr.JSON(label="Decision result")
            alert_button: Any = gr.Button("Apply audited alert decision")
            alert_button.click(
                lambda aid, status, actor_value, comment_value: update_alert_status(
                    client, aid, status, actor_value, comment_value
                ),
                inputs=[alert_id, new_status, actor, comment],
                outputs=alert_result,
            )
        with gr.Tab("Operations"):
            metrics = gr.JSON(label="Operational health and metrics")
            metrics_button: Any = gr.Button("Refresh metrics")
            metrics_button.click(lambda: render_metrics(client), outputs=metrics)
            retention_actor = gr.Textbox(label="Retention report actor", value="operator")
            retention = gr.JSON(label="Report-only retention result")
            retention_button: Any = gr.Button("Generate retention report (no deletion)")
            retention_button.click(
                lambda actor_value: run_retention_report(client, actor_value),
                inputs=retention_actor,
                outputs=retention,
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
