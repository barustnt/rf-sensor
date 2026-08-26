# Local RF-GPT vLLM runtime

Milestone 3 integrates RF-GPT through a local vLLM OpenAI-compatible HTTP endpoint. The platform
runs in Conda environment `rf-intel`; the VLM server runs separately in `vllm-env`.

## Recorded local runtime facts

- Invocation mechanism: local-only vLLM OpenAI-compatible HTTP API.
- Endpoint: configure `RF_RFGPT_ENDPOINT=http://127.0.0.1:8090/v1`.
- Served model name: `rfgpt`.
- Model version recorded by the platform: `Qwen2.5-VL-7B-rfa-wtr-v2-joint`.
- Model path: supplied locally through an untracked environment file as `RF_RFGPT_MODEL_PATH`.
- Model architecture: `Qwen2_5_VLForConditionalGeneration`.
- Weights: BF16, four shards, approximately 15.45 GiB.
- GPU: RTX 4090 Laptop GPU, approximately 16 GiB VRAM.
- Measured before implementation on 2026-08-25: 16,376 MiB total, 15,960 MiB free,
  15 MiB used, 45 C.
- vLLM environment versions observed: `vllm==0.19.0`, `transformers==4.57.6`,
  `torch==2.10.0`.
- Runtime uses CPU offload and worker concurrency 1.
- Known approximate direct-inference latency: 49 seconds for a single image.
- Observed CPU-offloaded generation speed is about 1 token/second on the validated local runtime;
  a 256-token attempt measured 250.6 seconds and still reached `finish_reason="length"`.
- Preprocessing: `atheer-hann-v1`, documented in `docs/rf-preprocessing.md`.
- Prompt contract: `technology-detection-primary-v4`, constrained JSON with no non-RF
  attribution, identity, ownership, or behavioral conclusions.
- Response schema: `rfgpt_analysis_primary_v4`. The only model-supplied RF quality flags accepted
  as trusted structured output are `no_signal`, `low_snr`, `uncertain`, `interference`,
  `clipping_suspected`, and `limited_bandwidth`; other flags are preserved in the raw response and
  filtered from trusted output.
- Timeout: set `RF_RFGPT_REQUEST_TIMEOUT_SECONDS=300`.
- Output budget: keep `RF_RFGPT_MAX_OUTPUT_TOKENS=224` unless a live test proves the endpoint
  can complete comfortably inside the request-timeout budget.
- Per-image inference is intentionally limited to at most one primary technology finding and one
  primary signal finding. Multi-capture correlation remains responsible for broader summaries.

Do not put the model directory or any model weights inside the repository.

## Untracked local environment

Create a local file that is ignored by Git, for example `.env.rfgpt.local`:

```bash
export RF_RFGPT_MODEL_PATH="/local/untracked/model/path"
export RF_RFGPT_ADAPTER=vllm
export RF_RFGPT_ENDPOINT=http://127.0.0.1:8090/v1
export RF_RFGPT_MODEL_NAME=rfgpt
export RF_RFGPT_MODEL_VERSION=Qwen2.5-VL-7B-rfa-wtr-v2-joint
export RF_RFGPT_REQUEST_TIMEOUT_SECONDS=300
export RF_RFGPT_TEMPERATURE=0
export RF_RFGPT_TOP_P=1
export RF_RFGPT_REPETITION_PENALTY=1.05
export RF_RFGPT_MAX_OUTPUT_TOKENS=224
export RF_WORKER_CONCURRENCY=1
```

Use the actual local model path on the machine that owns the model. Do not commit that file.

## Validated local-only launch command

Run this from a shell where `RF_RFGPT_MODEL_PATH` is set and `vllm-env` is active:

```bash
HF_HUB_OFFLINE=1 \
TRANSFORMERS_OFFLINE=1 \
CUDA_VISIBLE_DEVICES=0 \
vllm serve "${RF_RFGPT_MODEL_PATH}" \
  --served-model-name rfgpt \
  --host 127.0.0.1 \
  --port 8090 \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.80 \
  --cpu-offload-gb 10 \
  --max-model-len 2048 \
  --max-num-seqs 1 \
  --enforce-eager \
  --limit-mm-per-prompt '{"image":1,"video":0}'
```

Expected startup time is several minutes because the BF16 shards must be loaded and CPU offload
initialized. Keep the endpoint bound to `127.0.0.1`.

## Health checks

```bash
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8090/v1/models
```

The platform adapter checks both endpoints and verifies that `rfgpt` is present in `/v1/models`.
It sends the RF spectrogram as a lossless PNG data URL before the text prompt and requests strict,
bounded structured JSON through `response_format` using schema `rfgpt_analysis_primary_v4`.
The worker also validates required keys, per-image item limits, string lengths, numeric ranges, and
the configured job model identity application-side before trusting output. Empty technology and
signal arrays are a valid no-observation result.
Internally inconsistent output, such as a no-signal assessment combined with non-empty findings, is
stored in full as raw response but rejected from trusted findings with `semantic_inconsistency` and
does not produce an event or alert.

The API and worker must be started with the same RF-GPT configuration:

- `RF_RFGPT_ADAPTER`
- `RF_RFGPT_MODEL_NAME`
- `RF_RFGPT_MODEL_VERSION`
- prompt/schema defaults from the same code revision
- `RF_DATABASE_URL`

`RF_DATABASE_URL` must include the PostgreSQL password in the runtime environment. Do not log or
paste the password; use a local secret manager or untracked environment file.

## GPU and CPU offload behavior

The RTX 4090 Laptop GPU has limited VRAM for this model. The validated command uses:

- `--gpu-memory-utilization 0.80` to leave headroom for the desktop and platform processes;
- `--cpu-offload-gb 10` to keep the model usable on approximately 16 GiB VRAM;
- `--max-num-seqs 1` plus platform `RF_WORKER_CONCURRENCY=1` for single-image inference.

CPU offload makes the model fit, but live generation is slow. The observed generation rate is
approximately 1 token/second; a 256-token response took 250.6 seconds and hit the completion-token
limit. The platform therefore uses a compact primary-finding schema and a 224-token output cap
under a 300-second request timeout. Do not raise the output cap near or above the timeout budget
unless you also validate end-to-end latency under the same GPU/offload conditions.

The bounded schema exists because the model was repeating identical technology and signal
findings. It limits each image to the primary RF observation only: at most one technology item,
at most one signal item, short observations, and no duplicate findings.

Monitor with:

```bash
nvidia-smi
```

## Tokenizer fix requirement

The local model's `tokenizer_config.json` must already contain `fix_mistral_regex=true`. Milestone 3
does not modify tokenizer files, model weights, or any files under the model directory.

## Shutdown

Stop the `vllm serve` process with `Ctrl-C` in its terminal. Wait for the process to exit, then
confirm GPU memory is released:

```bash
nvidia-smi
```

## Troubleshooting

- OOM or CUDA allocation failure: confirm no other GPU process is using VRAM, keep
  `--max-num-seqs 1`, reduce `--gpu-memory-utilization`, or increase CPU offload.
- Timeout or `finish_reason="length"`: confirm the platform uses
  `RF_RFGPT_REQUEST_TIMEOUT_SECONDS=300`, `RF_RFGPT_MAX_OUTPUT_TOKENS=224`, and
  `RF_RFGPT_REPETITION_PENALTY=1.05`; first requests may be slower after startup, and CPU-offloaded
  generation at roughly 1 token/second needs a conservative output limit and bounded schema.
- Unavailable endpoint: verify `/health`, `/v1/models`, host `127.0.0.1`, port `8090`, and that
  `RF_RFGPT_ENDPOINT` ends with `/v1`.
- Invalid output: the adapter stores the raw response, marks `parser_valid=false`, creates no
  trusted findings, and classifies the job as a parser failure.
