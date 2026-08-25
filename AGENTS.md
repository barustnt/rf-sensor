# Agent build notes

`PROJECT_SPEC.md` is the source of truth. Keep Agent Orchestrator development-only; do not add it
as a runtime dependency, service, API dependency, deployment component, or dashboard component.

Build gates for Milestones 0-1:

- Use the `rf-intel` Conda environment, not Conda `base` or the operating-system Python.
- Run `make check` before marking work complete.
- Run `make demo` for the simulated end-to-end acceptance flow.
- Keep public contracts versioned and validated with Pydantic.
- Keep sensor capture, transport, storage, inference, event generation, and presentation separate.
- Do not add Pluto+, UHD, real RF-GPT packages, RF transmission, payload decoding, public tunnels,
  or destructive retention in Milestones 0-1.
- Keep addresses, credentials, paths, sensor locations, and radio settings in environment/config,
  not application code.

See `PROJECT_SPEC.md` for the complete requirements and out-of-scope list.
