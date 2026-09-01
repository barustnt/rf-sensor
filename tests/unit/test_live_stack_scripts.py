from pathlib import Path


def _script(name: str) -> str:
    return Path("scripts", name).read_text(encoding="utf-8")


def test_live_up_supports_external_vllm_and_validates_served_model() -> None:
    source = _script("live_up.sh")

    assert 'RF_VLLM_MANAGED_VALUE="${RF_VLLM_MANAGED:-true}"' in source
    assert 'RF_RFGPT_ENDPOINT_VALUE="${RF_RFGPT_ENDPOINT:-http://127.0.0.1:8090/v1}"' in source
    assert "RF_VLLM_HEALTH_URL_VALUE=" in source
    assert 'RF_VLLM_MODELS_URL_VALUE="${RF_RFGPT_ENDPOINT_VALUE}/v1/models"' in source
    assert "validate_vllm_model" in source
    assert 'EXPECTED_MODEL_NAME="$RF_RFGPT_MODEL_NAME"' in source
    assert source.count('RF_RFGPT_ENDPOINT="$RF_RFGPT_ENDPOINT_VALUE"') == 2
    assert source.count('RF_GRADIO_SHARE="$RF_GRADIO_SHARE_VALUE"') == 2
    assert 'log "using external vLLM endpoint $RF_RFGPT_ENDPOINT_VALUE"' in source
    assert 'wait_http vllm "$RF_VLLM_HEALTH_URL_VALUE" 12 5 ""' in source
    assert 'show_gradio_public_url dashboard "Command Center public"' in source
    assert 'show_gradio_public_url ask-rf "Ask RF public"' in source


def test_live_down_never_stops_external_vllm_or_deletes_volume() -> None:
    source = _script("live_down.sh")

    assert 'if [[ "$RF_VLLM_MANAGED_VALUE" == "true" ]]' in source
    assert "external vLLM is not managed by this host and will not be stopped" in source
    assert '--project-name "$COMPOSE_PROJECT"' in source
    assert "down\n" in source
    assert "down -v" not in source
    assert "volume rm" not in source
