# Receive-only USRP B210 sensor

Milestone 4 adds a production sensor adapter for the Ettus USRP B210 behind the same sensor-agent
contract used by the simulated adapter. It is receive-only: the adapter exposes no transmit path and
never calls UHD TX APIs.

## Runtime environment

Use a separate Conda environment for UHD hardware access. Do not merge this with `rf-intel` or the
`vllm-env` model runtime.

```bash
conda env create -f environment-b210.yml
conda activate rf-b210
python -m uhd_images_downloader
pip install -e .[dev]
```

Expected validated hardware/runtime facts:

- Device: Ettus USRP B210
- UHD package: `4.9.0.1` from conda-forge
- Python: `3.11`
- NumPy: `2.2.6`
- CPU sample format: `fc32`
- Wire sample format: `sc16`

The UHD image path is provided by the environment and should not be hardcoded in application code.

## Configuration

Example local untracked configuration:

```bash
export RF_SENSOR_ADAPTER=b210
export RF_SENSOR_ID=laptop-b210-001
export RF_SENSOR_TOKEN=change-me
export RF_PLATFORM_URL=http://127.0.0.1:8000
export RF_SENSOR_PROFILE=b210_2g4_demo
export RF_B210_DEVICE_ARGS=serial=321D88A
export RF_B210_SERIAL=321D88A
export RF_B210_RX_CHANNEL=0
export RF_B210_ANTENNA=RX2
export RF_B210_CENTER_FREQUENCY_HZ=2440000000
export RF_B210_SAMPLE_RATE_SPS=20000000
export RF_B210_BANDWIDTH_HZ=20000000
export RF_B210_GAIN_DB=30
export RF_B210_SAMPLE_COUNT=1048576
export RF_B210_RECEIVE_TIMEOUT_SECONDS=5
export RF_B210_SETTLING_SECONDS=0.1
export RF_B210_CPU_FORMAT=fc32
export RF_B210_WIRE_FORMAT=sc16
export RF_B210_PERSIST_RAW_IQ=false
```

Keep machine addresses configurable. The demo values belong in local environment files or profiles,
not Python code. When `RF_SENSOR_PROFILE=b210_2g4_demo`, the selected profile name, capture logs,
and stored `Capture.profile_id` should all remain `b210_2g4_demo`; `campus_general` must not be
used as the selected B210 profile ID.

## Local receive-only hardware/preprocessing acceptance

This check does not require the API, PostgreSQL, NATS, dashboard, or RF-GPT. It opens the B210,
captures exactly `1048576` complex samples, runs `atheer-hann-v1`, and writes a 512x512 PNG.

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_SENSOR_PROFILE=b210_2g4_demo \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_B210_RX_CHANNEL=0 \
RF_B210_ANTENNA=RX2 \
RF_B210_CENTER_FREQUENCY_HZ=2440000000 \
RF_B210_SAMPLE_RATE_SPS=20000000 \
RF_B210_BANDWIDTH_HZ=20000000 \
RF_B210_GAIN_DB=30 \
RF_B210_SAMPLE_COUNT=1048576 \
RF_B210_RECEIVE_TIMEOUT_SECONDS=5 \
RF_B210_CPU_FORMAT=fc32 \
RF_B210_WIRE_FORMAT=sc16 \
RF_B210_PERSIST_RAW_IQ=false \
conda run -n rf-b210 python scripts/run_b210_receive_smoke.py
```

The script prints selected settings before streaming, sample statistics, actual UHD-returned radio
values, the artifact path, and the SHA-256 digest. It exits nonzero on hardware, receive, sample, or
preprocessing failure. It never transmits.

## One-shot upload command

With the API running and `RF_SENSOR_TOKEN` configured:

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_SENSOR_PROFILE=b210_2g4_demo \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_B210_RX_CHANNEL=0 \
RF_B210_ANTENNA=RX2 \
RF_B210_SAMPLE_COUNT=1048576 \
RF_B210_PERSIST_RAW_IQ=false \
conda run -n rf-b210 python -m rf_platform.sensor_agent.main --once
```

Equivalent Make target:

```bash
make PYTHON='conda run -n rf-b210 python' sensor-b210-once
```

## Continuous B210 sensor command

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_SENSOR_PROFILE=b210_2g4_demo \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_B210_RX_CHANNEL=0 \
RF_B210_ANTENNA=RX2 \
RF_B210_SAMPLE_COUNT=1048576 \
RF_B210_PERSIST_RAW_IQ=false \
conda run -n rf-b210 python -m rf_platform.sensor_agent.main
```

Equivalent Make target:

```bash
make PYTHON='conda run -n rf-b210 python' sensor-b210
```

The continuous loop keeps heartbeats and desired-state polling active, spools failed uploads, and
backs off bounded capture failures instead of silently falling back to the simulated adapter.

## Optional full-platform acceptance

Semantic RF-GPT accuracy is not an acceptance criterion. The acceptance criteria are transport,
persistence, schema validity, provenance, and dashboard visibility.

Terminal 1:

```bash
export RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set an untracked shared sensor token}"
export RF_DATABASE_URL="postgresql+asyncpg://rf_platform:${RF_POSTGRES_PASSWORD}@127.0.0.1:5432/rf_platform"
export RF_RFGPT_ADAPTER=vllm
export RF_RFGPT_ENDPOINT=http://127.0.0.1:8090/v1
export RF_RFGPT_MODEL_NAME=rfgpt
export RF_RFGPT_MODEL_VERSION=Qwen2.5-VL-7B-rfa-wtr-v2-joint
export RF_RFGPT_REQUEST_TIMEOUT_SECONDS=300
export RF_RFGPT_REPETITION_PENALTY=1.05
export RF_RFGPT_MAX_OUTPUT_TOKENS=224
export RF_WORKER_CONCURRENCY=1
make infra-up
make PYTHON='conda run -n rf-intel python' migrate
make PYTHON='conda run -n rf-intel python' seed
make PYTHON='conda run -n rf-intel python' api
```

Set `RF_POSTGRES_PASSWORD` locally before constructing `RF_DATABASE_URL`; do not paste or log the
password. Set `RF_SENSOR_TOKEN` from an untracked local secret and reuse the same value for the
sensor command. The API and worker must use the same `RF_RFGPT_ADAPTER`, model name/version,
v4 prompt/schema code revision, and `RF_DATABASE_URL` so jobs are targeted at the worker's model.

Terminal 2, with the local vLLM server already running per `docs/rfgpt-runtime.md`:

```bash
RF_DATABASE_URL="postgresql+asyncpg://rf_platform:${RF_POSTGRES_PASSWORD}@127.0.0.1:5432/rf_platform" \
RF_RFGPT_ADAPTER=vllm \
RF_RFGPT_ENDPOINT=http://127.0.0.1:8090/v1 \
RF_RFGPT_MODEL_NAME=rfgpt \
RF_RFGPT_MODEL_VERSION=Qwen2.5-VL-7B-rfa-wtr-v2-joint \
RF_RFGPT_REQUEST_TIMEOUT_SECONDS=300 \
RF_RFGPT_REPETITION_PENALTY=1.05 \
RF_RFGPT_MAX_OUTPUT_TOKENS=224 \
RF_WORKER_CONCURRENCY=1 \
make PYTHON='conda run -n rf-intel python' worker
```

Terminal 3:

```bash
RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set the same token used by the API}" \
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_SENSOR_PROFILE=b210_2g4_demo \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_B210_RX_CHANNEL=0 \
RF_B210_ANTENNA=RX2 \
RF_B210_SAMPLE_COUNT=1048576 \
RF_B210_PERSIST_RAW_IQ=false \
conda run -n rf-b210 python -m rf_platform.sensor_agent.main --once
```

Terminal 4:

```bash
make PYTHON='conda run -n rf-intel python' dashboard
```

Verify that the B210 sensor is visible by stable ID, the capture and `atheer-hann-v1`
spectrogram are persisted, the analysis job completes or records a parser/model failure safely,
and the dashboard displays provenance. Any RF-GPT label is an unverified model observation.
Internally inconsistent model output is preserved as raw response, rejected from trusted findings,
and must not produce an event or alert.
