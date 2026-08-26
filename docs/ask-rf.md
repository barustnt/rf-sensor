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
  `unsupported_question`, or `unavailable`
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
- LTE and 5G NR questions do not invent local carrier frequencies. Until later multi-band scan
  profiles are configured, Ask RF states that configured LTE/5G bands were not monitored.
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

Follow-ups such as “Was it Bluetooth?” and “What about 5G?” reuse the previous interpreted time
period in the current UI session. The New question action clears that session context. Conversation
history is not stored in PostgreSQL in this milestone.

## Limitations

Ask RF is deterministic and does not call an external LLM, RF-GPT, vLLM, the B210, or any sensor.
It does not validate RF semantic accuracy; labels remain unverified model observations. Multi-band
scanning, LTE/NR profile coverage, and richer natural-language parsing are reserved for later
milestones.
