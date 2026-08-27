# UAE receive-only scan profiles

Milestone 6 adds a deterministic, receive-only scan planner for sequential USRP B210 captures. The
planner is coverage-first: it records which frequency ranges were actually captured and whether an
analysis was accepted, pending, rejected, experimental, operator accepted, or independently
validated. It does **not** claim model accuracy or independent RF technology validation.

The original RF-GPT fine-tuning dataset and formal evaluation records are unavailable. The current
fine-tuned RF-GPT checkpoint is treated as the operator-approved project baseline based on Atheer
capture testing, without a measured accuracy claim.

## Safety boundaries

- Receive-only; no transmit behavior is added or exposed.
- No Pluto+ support.
- No RF-GPT weights, tokenizer files, adapter contracts, or `atheer-hann-v1` preprocessing changes.
- Raw IQ persistence remains disabled by default.
- Scanner dry-run does not open the B210, call the platform API, start a worker, or call vLLM.
- Ask RF never calls vLLM/RF-GPT and never promotes scan profiles from the browser.

## Catalogue format

The profile catalogue is TOML and versioned:

```text
config/scan-profiles/uae-b210-sub6-v1.toml
```

Top-level fields identify the profile set, version, display name, and source notes. A `[defaults]`
section supplies capture bandwidth, sample rate, slice step/overlap, gain, antenna, sample count,
qualification state, and presentation policy. Each `[[profiles]]` entry then declares:

- `profile_id` and display name;
- start/end frequency in Hz;
- capture bandwidth, sample rate, slice step, and explicit overlap;
- gain, antenna, sample count, priority, and enabled flag;
- candidate technology families;
- qualification state;
- presentation policy;
- regulatory/source note and known limitations.

Unsupported bands are declared separately under `[[unsupported_profiles]]` so operators can see why
B210 coverage is incomplete.

## Qualification states and presentation policy

Qualification state is separate from scan enablement and Ask RF presentation eligibility:

- `experimental` — may be scanned and shown to technical operators, but Ask RF must not use it for
  definitive technology presence/absence.
- `operator_accepted` — operator has explicitly accepted the profile for presentation use.
- `independently_validated` — independently validated profile.
- `regulatory_review_required` — cannot be scanned until configuration is updated after review.
- `unsupported_hardware` — outside the configured hardware capability.

Presentation policy values are `hidden`, `technical_only`, and `presentation_eligible`.
Configuration validation rejects `presentation_eligible` unless the profile is
`operator_accepted` or `independently_validated`. Promotion is a configuration change, not a browser
control.

## Default enablement

All large UAE multi-band profile groups are disabled by default. Scanning captures nothing unless an
operator provides an explicit allowlist:

```bash
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5
```

The catalogue entries are regulatory and engineering references, not a claim that every listed
frequency is currently deployed by a UAE operator.

## UAE reference groups

The default catalogue includes these B210-supported candidate groups:

- LTE/4G and possible NR/DSS candidate IMT ranges below 3 GHz:
  694-790, 791-862, 880-960, 1427-1492, 1710-1785, 1805-1880, 1920-1980,
  2110-2170, 1980-2010, 2170-2200, 2300-2400, and 2496-2690 MHz.
- LTE/5G NR FR1 TDD candidates: 3300-3400 and 3400-3800 MHz. LTE and NR may overlap in these
  ranges; frequency compatibility is necessary but not sufficient for confirmation.
- Shared 2.4 GHz range: 2400-2483.5 MHz for Wi-Fi/WLAN, Bluetooth Classic, BLE, and generic ISM/SRD
  activity.
- Wi-Fi 5 GHz ranges: 5150-5250, 5250-5350, 5470-5725, and 5725-5875 MHz.
- UAE SRD/ISM candidates: 433.05-434.79, 863-870, 915-921, 2400-2483.5, and 5725-5875 MHz.

Explicitly unsupported/incomplete references are also listed:

- full Wi-Fi 6E 5945-6425 MHz: the B210 cannot cover the complete band because its upper limit is
  6 GHz;
- 5G mmWave beginning at 24.25 GHz: unsupported by B210;
- 60 GHz Wi-Fi/SRD: unsupported by B210.

## Expanded slice counts

With 20 MHz capture bandwidth and 18 MHz step/2 MHz overlap, the catalogue expands deterministically.
All scannable profiles together contain 145 slices across 24 profiles. Capture-only estimates exclude
RF-GPT inference time and may be much shorter than full-platform analysis time with one VLM worker.

Selected slice counts:

| Profile | Slices |
|---|---:|
| `uae_shared_2400_2483_5` | 5 |
| `uae_imt_694_790` | 6 |
| `uae_imt_2496_2690` | 11 |
| `uae_nr_tdd_3300_3400` | 6 |
| `uae_nr_tdd_3400_3800` | 23 |
| `uae_wifi5_5470_5725` | 15 |
| `uae_wifi5_5725_5875` | 9 |

Print the exact plan for the active configuration instead of relying on this table. The normal
dry-run output is intentionally concise: selected profiles, planned slices, estimates, warnings, and
notes. The full catalogue remains available through `GET /api/v1/scan-profiles` or the explicit
`--scan-plan-verbose` CLI option.

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5 \
RF_SCAN_MAX_SLICES_PER_CYCLE=2 \
make PYTHON='conda run -n rf-intel python' scan-plan
```

For that limited two-slice example, the first planned slices are 2400-2420 MHz and 2418-2438 MHz.
They request 40 MHz of capture bandwidth, overlap by 2 MHz, and cover a 38 MHz union of the
83.5 MHz configured profile. The planner warns that this truncated plan does not provide complete
profile coverage.

Key estimate fields include:

- `requested_capture_bandwidth_hz` — sum of requested capture bandwidth for planned slices;
- `overlap_hz` — requested capture bandwidth minus planned union coverage;
- `planned_union_coverage_hz` — merged coverage from the actual limited plan;
- `configured_profile_bandwidth_hz` — full configured width of selected profiles;
- `full_profile_slice_count` and `planned_slice_count`;
- `plan_truncated` and `full_profile_coverage_complete`.

## Scanner and backpressure

The B210 scanner retunes and captures one slice at a time using the existing B210 capture
implementation. Before each slice it checks the platform API for queued/running/retry-pending jobs
for this sensor only. If the in-flight count reaches `RF_SCAN_MAX_INFLIGHT_JOBS` (default `1`), it
pauses and polls until backlog clears. Succeeded, failed, and dead-letter jobs do not block new
capture. API outages and hardware failures use bounded cooldowns to avoid hot retry loops.

Key settings:

```dotenv
RF_SCAN_PROFILE_CONFIG=config/scan-profiles/uae-b210-sub6-v1.toml
RF_SCAN_PROFILE_SET=uae-b210-sub6-v1
RF_SCAN_ENABLED_PROFILE_IDS=
RF_SCAN_MAX_INFLIGHT_JOBS=1
RF_SCAN_BACKPRESSURE_POLL_SECONDS=5
RF_SCAN_FAILURE_COOLDOWN_SECONDS=30
RF_SCAN_RETUNE_SETTLE_SECONDS=0.1
RF_SCAN_CYCLE_INTERVAL_SECONDS=0
# RF_SCAN_MAX_SLICES_PER_CYCLE=3
```

## Coverage semantics

Coverage is based on stored capture values, preferring actual UHD-returned center frequency and
bandwidth when present. Each capture contributes:

```text
actual_center_frequency_hz ± actual_bandwidth_hz / 2
```

intersected with the configured profile range. Ranges are merged deterministically with a small
tolerance for tuning precision. Coverage reports distinguish:

- hardware captured;
- analysis pending;
- analysis rejected;
- accepted observation;
- experimental identification;
- operator-accepted or independently validated presentation-eligible identification.

Complete band coverage is reported only when every required planned slice was captured within the
question interval. Missing edge slices, stale captures, simulated captures, mock analyses,
parser-invalid analyses, semantic contradictions, and band-incompatible findings do not establish
trusted presentation coverage.

## Band/technology consistency

The shared band-compatibility service is defensive. It rejects or downgrades impossible combinations
before event or presentation use. Examples:

- Bluetooth/BLE at 433 MHz is incompatible.
- Wi-Fi/WLAN at 950 MHz is incompatible.
- DVB-S/S2 satellite claims in terrestrial sub-10 GHz captures are incompatible.
- 5G mmWave cannot be claimed from a B210 capture.

Generic descriptions such as `cellular`, `wideband activity`, `tone`, `chirp`, or `ISM activity`
may remain valid when exact protocol evidence is insufficient. The service never invents a UAE
operator, carrier, device identity, owner, user behavior, or transmitted content.

## Read-only technical APIs

```text
GET /api/v1/scan-profiles
GET /api/v1/coverage?start_utc=...&end_utc=...&sensor_id=...
GET /api/v1/sensors/{sensor_id}/jobs/summary
```

These endpoints expose technical profile and coverage information to Command Center operators. They
do not expose sensor tokens, database credentials, or browser controls that start/stop scanning,
retune hardware, or promote profile qualification.

## Manual acceptance plan

Use a small allowlist first. Do not run the whole UAE catalogue as the first live test.

Validate and print a dry-run plan:

```bash
RF_SENSOR_ADAPTER=b210 \
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5 \
RF_SCAN_MAX_SLICES_PER_CYCLE=2 \
make PYTHON=/home/user/miniconda3/envs/rf-b210/bin/python scan-plan
```

Start the API in `rf-intel`:

```bash
RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set shared token}" \
RF_DATABASE_URL="postgresql+asyncpg://rf_platform:${RF_POSTGRES_PASSWORD:?set password}@127.0.0.1:5432/rf_platform" \
make PYTHON='conda run -n rf-intel python' api
```

If inference is intentionally required, start vLLM per `docs/rfgpt-runtime.md` and run the worker
with the same `RF_DATABASE_URL`, `RF_RFGPT_ADAPTER`, `RF_RFGPT_MODEL_NAME`, and
`RF_RFGPT_MODEL_VERSION` as the API. vLLM is not required for dry-run or coverage-only tests.

Run one limited experimental scan cycle in `rf-b210`:

```bash
RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set the same token used by API}" \
RF_SENSOR_ADAPTER=b210 \
RF_SENSOR_ID=laptop-b210-001 \
RF_PLATFORM_URL=http://127.0.0.1:8000 \
RF_B210_DEVICE_ARGS=serial=321D88A \
RF_B210_SERIAL=321D88A \
RF_SCAN_ENABLED_PROFILE_IDS=uae_shared_2400_2483_5 \
RF_SCAN_MAX_SLICES_PER_CYCLE=2 \
RF_B210_PERSIST_RAW_IQ=false \
/home/user/miniconda3/envs/rf-b210/bin/python -m rf_platform.sensor_agent.main --scan --scan-one-cycle --scan-max-slices 2
```

View technical coverage:

```bash
curl 'http://127.0.0.1:8000/api/v1/scan-profiles'
curl 'http://127.0.0.1:8000/api/v1/coverage?sensor_id=laptop-b210-001'
make PYTHON='conda run -n rf-intel python' dashboard
```

Ask RF monitored-but-unvalidated behavior after experimental captures:

```bash
make PYTHON='conda run -n rf-intel python' ask-rf
# Ask: "Was 5G observed?" or "Was Bluetooth observed?"
```

Ask RF will not use experimental profiles for definitive technology conclusions. It may report that
a range was monitored but profile technology identification is not yet validated.

## Source references

Documented source notes correspond to:

- UAE National Frequency Plan, TDRA public table (accessed 2026-08-27):
  <https://tdra.gov.ae/en/test/national-frequency-plan>
- TDRA UAE Spectrum Outlook 2026-2031 v2.0 (published 2025-11-01):
  <https://tdra.gov.ae/-/media/TDRA-Media/Resources/Resources-2025/UAE-Spectrum-Outlook-20262031v2.ashx>
- TDRA Ultra-Wide Band and Short Range Devices Regulations v5.0 (published 2023-10-30):
  <https://tdra.gov.ae/-/media/About/regulations-and-ruling/EN/UWB-and-SRD-Regulations-50.ashx>
- Ettus USRP B210 hardware specifications:
  <https://kb.ettus.com/B200/B210/B200mini/B205mini/B206mini>
