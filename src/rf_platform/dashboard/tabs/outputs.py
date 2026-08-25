from __future__ import annotations

from typing import Any

from rf_platform.dashboard.api_client import DashboardApiClient


def _format_finding(item: dict[str, Any]) -> str:
    score = item.get("model_score")
    score_text = "uncalibrated/not provided" if score is None else str(score)
    return (
        f"- **{item.get('label', 'unknown')}** "
        f"(model_score: {score_text})\n"
        f"  - Observation: {item.get('observation', '')}\n"
        f"  - Evidence: {', '.join(str(x) for x in item.get('evidence', [])) or 'linked capture'}"
    )


def render_outputs(
    client: DashboardApiClient,
    sensor_id: str | None = None,
    profile_id: str | None = None,
    location: str | None = None,
    technology: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    status: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[dict[str, object]]:
    return client.outputs(
        sensor_id=sensor_id,
        profile_id=profile_id,
        location=location,
        technology=technology,
        model_version=model_version,
        prompt_version=prompt_version,
        status=status,
        start_utc=start_utc,
        end_utc=end_utc,
        limit=limit,
        offset=offset,
    ).get("items", [])


def render_output_detail(client: DashboardApiClient, analysis_id: str) -> str:
    if not analysis_id:
        return "Select or enter an analysis ID."
    detail = client.output_detail(analysis_id)
    model = detail.get("model", {})
    capture = detail.get("capture") or {}
    sensor = detail.get("sensor") or {}
    findings = detail.get("technologies") or detail.get("findings") or []
    finding_text = (
        "\n".join(_format_finding(item) for item in findings) or "No structured findings."
    )
    artifacts = detail.get("artifacts", [])
    artifact_text = (
        "\n".join(
            f"- {item.get('kind')}: `{item.get('object_key')}` ({item.get('byte_size')} bytes)"
            for item in artifacts
        )
        or "No artifacts linked."
    )
    preview = next((item for item in artifacts if item.get("kind") == "spectrogram"), None)
    preview_text = (
        f"![Spectrogram preview]({client.base_url}{preview.get('preview_url')})"
        if preview and preview.get("preview_url")
        else "No spectrogram preview available."
    )
    events = detail.get("linked_events", [])
    event_text = (
        "\n".join(
            f"- {item.get('event_id')} — {item.get('status')}: {item.get('summary')}"
            for item in events
        )
        or "No linked events."
    )
    alerts = detail.get("linked_alerts", [])
    alert_text = (
        "\n".join(
            f"- {item.get('alert_id')} — {item.get('status')}: {item.get('reason')}"
            for item in alerts
        )
        or "No linked alerts."
    )
    annotations = detail.get("annotations", [])
    annotation_text = (
        "\n".join(
            "- "
            f"{item.get('timestamp_utc')} {item.get('actor')}: "
            f"{item.get('label')} {item.get('comment') or ''}"
            for item in annotations
        )
        or "No annotations."
    )
    quality_flags = detail.get("quality_flags", [])
    quality_text = (
        ", ".join(str(item) for item in quality_flags) if quality_flags else "None reported."
    )
    inference_parameters = detail.get("inference_parameters", {})
    return f"""## RF-GPT Output Detail

- **Analysis ID:** `{detail.get("analysis_id")}`
- **Capture ID:** `{detail.get("capture_id")}`
- **Capture time:** {capture.get("started_at_utc")} → {capture.get("ended_at_utc")}
- **Sensor:** {capture.get("sensor_id")}
- **Sensor location:** `{sensor.get("location")}`
- **Profile:** {capture.get("profile_id")}
- **Model:** {model.get("name")} `{model.get("version")}` via `{model.get("adapter")}`
- **Prompt version:** `{model.get("prompt_version")}`
- **Latency:** {detail.get("latency_ms")} ms
- **Parser valid:** {detail.get("parser_valid")}
- **Quality flags:** {quality_text}
- **Radio settings:** `{capture.get("radio")}`
- **Preprocessing:** `{(capture.get("preprocessing") or {}).get("pipeline_version")}`
- **Inference parameters:** `{inference_parameters}`

### Spectrogram preview
{preview_text}

### Structured findings
{finding_text}

### Evidence and artifacts
{artifact_text}

### Linked events / alerts
{event_text}
{alert_text}

### Annotations
{annotation_text}

### Limitations
- Model output is an RF-GPT observation, not verified ground truth.
- Numeric confidence is not invented; missing model scores remain unset.

### Raw model response
```json
{detail.get("raw_response")}
```
"""
