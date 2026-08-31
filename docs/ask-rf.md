# Ask RF

Ask RF is a separate presentation interface for non-technical users and demonstration audiences. It
is intentionally separate from the technical Command Center.

| Interface | Audience | Default port | Purpose |
|---|---|---:|---|
| Command Center | Technical operators | 7860 | Sensors, jobs, logs, alerts, RF-GPT details, spectrograms, and operations |
| Ask RF | Non-technical users | 7861 | Plain-language answers from trusted stored observations |

Ask RF is read-only. It calls the platform API server-side and never exposes `RF_SENSOR_TOKEN`,
PostgreSQL credentials, NATS credentials, retry controls, alert controls, spectrograms, raw JSON, or
model/debug settings in the browser.

## Launch

Start infrastructure and the API first:

```bash
make infra-up
make migrate
make seed
RF_SENSOR_TOKEN="${RF_SENSOR_TOKEN:?set the shared sensor token}" \
RF_DATABASE_URL="postgresql+asyncpg://rf_platform:${RF_POSTGRES_PASSWORD:?set password}@127.0.0.1:5432/rf_platform" \
make PYTHON='conda run -n rf-intel python' api
```

Start the technical Command Center separately:

```bash
make PYTHON='conda run -n rf-intel python' dashboard
# opens on http://127.0.0.1:7860 by default
```

Start Ask RF separately:

```bash
make PYTHON='conda run -n rf-intel python' ask-rf
# opens on http://127.0.0.1:7861 by default
```

vLLM, RF-GPT, the RF-GPT worker, and B210 capture are not required for asking historical questions
that have already been processed and stored.

## Configuration

```dotenv
RF_PLATFORM_URL=http://127.0.0.1:8000
RF_ASK_RF_HOST=0.0.0.0
RF_ASK_RF_PORT=7861
RF_DISPLAY_TIMEZONE=Asia/Dubai
RF_API_TIMEOUT_SECONDS=5
RF_GRADIO_SHARE=false
```

Do not put real passwords or tokens in documentation. Ask RF uses only the platform URL and timeout
for its server-side API calls.

## Presentation endpoint

Ask RF uses a dedicated read-only presentation endpoint:

```text
POST /api/v1/ask-rf/query
```

Request fields:

- `question`
- `display_timezone`
- optional `prior_context` returned by the previous Ask RF response for follow-up questions

Response fields:

- `schema_version`
- `answer_status`: `observation`, `no_signal`, `no_data`, `partial_data`, `not_monitored`,
  `profile_not_validated`, `unsupported_question`, or `unavailable`
- `display_answer`
- `interpreted_interval`
- `time_label`
- `location_label`
- `evidence_explanation`
- `limitations`
- `follow_up_context`

The UI renders only the human-readable answer, time/location labels, evidence explanation, and
limitations. It does not show UUIDs, job IDs, model names, adapter names, parser flags, database
fields, raw responses, raw JSON, logs, or spectrograms.

## Trusted-data filtering

Ask RF answers only from accepted stored observations. It excludes, by default:

- simulated sensor captures;
- mock adapter analyses and `mock-v1` model runs;
- parser-invalid or `parser_failed` analyses;
- failed/dead-letter jobs and model-configuration mismatches;
- semantic inconsistencies and older contradictory records re-detected with the shared no-signal
  consistency helper;
- events that are linked only to excluded analyses.

Excluded records remain visible in the Command Center for technical review but do not influence
presentation answers.

## Coverage-aware language

Ask RF distinguishes no data from no signal. It never treats “not observed” as proof of absence
unless accepted observations exist for the monitored range.

- Bluetooth/BLE questions map to the 2.4 GHz range. Current 20 MHz captures cover only part of the
  full Bluetooth/BLE band, so Ask RF says Bluetooth was not confirmed in the monitored portion and
  never claims Bluetooth was absent from the complete band.
- LTE and 5G NR questions do not invent local carrier frequencies. With no eligible captures, Ask RF
  says configured LTE/5G bands were not monitored. Experimental scan-profile captures can show that
  a frequency range was monitored, but Ask RF returns `profile_not_validated` and does not claim
  technology presence or absence until the profile is operator-accepted or independently validated.
- General “nearby technologies” questions report only accepted, internally consistent observations
  in actually monitored ranges and include a short coverage limitation.

## Supported question patterns

- What happened today at 10 AM?
- What happened yesterday at 11 PM?
- What technologies are nearby?
- Was anything unusual this morning?
- Was Bluetooth observed?
- What about BLE?
- Was LTE observed?
- Was 5G observed?
- Was 5G NR observed?
- Was Wi-Fi observed?

Follow-ups such as “Was it Bluetooth?” and “What about 5G?” reuse the previous interpreted time
period in the current UI session. The New question action clears that session context. Conversation
history is not stored in PostgreSQL in this milestone.

## Milestone 6 scan-profile behavior

Ask RF uses the same trusted-data and band-consistency services as the worker and event correlation.
It excludes simulated captures, mock/model-invalid runs, parser-invalid analyses, semantic
contradictions, and band-incompatible findings. Internally consistent findings from experimental
profiles remain excluded from definitive presentation conclusions, but Ask RF may show a clearly
labeled experimental indication. If those stored findings include model scores, Ask RF reports the
median score and explicitly states that it is not a calibrated probability. Operator-accepted or
independently validated profiles may contribute to observation/no-signal logic, subject to coverage
completeness and consistency checks.

For “What technologies are nearby?”, Ask RF lists only presentation-eligible accepted observations.
It may state in plain language that additional ranges were monitored experimentally and summarize
consistent experimental technology indications, but it does not show profile IDs, validation flags,
model names, raw rejected output, raw JSON, or UUIDs.

## Limitations

Ask RF is deterministic and does not call an external LLM, RF-GPT, vLLM, the B210, or any sensor.
It does not validate RF semantic accuracy; labels remain unverified model observations. The original
fine-tuning dataset and formal evaluation records are unavailable, so the current RF-GPT checkpoint
is used only as the operator-approved baseline without an accuracy claim. Richer natural-language
parsing and automated broad scan scheduling remain outside this milestone.
