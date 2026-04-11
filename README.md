# GivEVC-OCPP

GivEnergy EVC-focused Home Assistant custom integration that acts as a local OCPP 1.6J central system/server.

## What this repository contains

The custom component lives under `custom_components/givenergy_evc_ocpp` and is intentionally opinionated for the GivEnergy EVC rather than trying to clone a generic OCPP integration.

Current starter implementation includes:

- A dedicated embedded websocket listener on its own configurable port, default `7655`
- Single-charger-first onboarding with first-connection adoption
- Configurable preferred `MeterValueSampleInterval`, default `15` seconds, applied back to the charger when supported
- Optional enhanced OCPP diagnostics capture with rolling inbound/outbound frame history for troubleshooting charge-start issues
- OCPP 1.6J handling for `BootNotification`, `Heartbeat`, `StatusNotification`, `Authorize`, `StartTransaction`, `StopTransaction`, `MeterValues`, `FirmwareStatusNotification`, and `DiagnosticsStatusNotification`
- GivEnergy-oriented `MeterValues` parsing that preserves raw samples and maps the most useful live values into Home Assistant entities
- Buttons, sensors, number, switch entities, diagnostics export, and domain services for the main control actions
- A slider-based charging current control clamped to `8-32A`

## Architecture overview

- `server.py`
  Runs the dedicated inbound websocket listener using Home Assistant's `aiohttp` stack and owns the single active charger session.
- `charge_point.py`
  Implements the OCPP 1.6J frame handling for the charger connection and dispatches inbound/outbound calls.
- `coordinator.py`
  Maintains charger state, device identity, raw payload snapshots, meter parsing heuristics, and command helpers used by entities and services.
- Platform files
  `sensor.py`, `button.py`, `number.py`, and `switch.py` expose the HA entities.
- `diagnostics.py`
  Exports raw payloads and parsed state for troubleshooting.

## Installation

### HACS

1. In HACS, add this repository as a custom repository.
2. Category: `Integration`
3. Install `GivEnergy EVC OCPP`
4. Restart Home Assistant.
5. Add the integration from the UI.
6. Set the preferred meter values interval. The integration defaults to `15` seconds and will try to apply that to the charger using `ChangeConfiguration`.
7. Leave the listen port at `7655` unless you have already configured the charger to use a different port.
8. Point the charger at:

   `ws://<home_assistant_host>:7655/<charge_point_id>`

   If the charger omits the path, the integration will still try to adopt it on boot using the boot payload details.

### Manual install

1. Copy `custom_components/givenergy_evc_ocpp` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add the integration from the UI.
4. Set the preferred meter values interval. The integration defaults to `15` seconds and will try to apply that to the charger using `ChangeConfiguration`.
5. Leave the listen port at `7655` unless you have already configured the charger to use a different port.
6. Point the charger at:

   `ws://<home_assistant_host>:7655/<charge_point_id>`

   If the charger omits the path, the integration will still try to adopt it on boot using the boot payload details.

For HACS publication, create tagged GitHub releases so HACS can track versions cleanly.

## Testing notes

Recommended first-pass validation:

1. Enable the integration and confirm Home Assistant starts without a port bind error.
2. Reboot or reconnect the charger and check that a device is created after `BootNotification`.
3. Confirm the charger sends `MeterValues` and that `live power`, `live current`, `live voltage`, `session energy`, and `total energy` sensors populate.
4. Confirm the charger's `MeterValueSampleInterval` changes from the default `60` seconds to the configured value if the charger accepts the setting.
5. Use the `Refresh configuration` button or `givenergy_evc_ocpp.get_configuration` service to pull the full config set.
6. Exercise `Soft reset`, `Trigger meter values`, and `RemoteStartTransaction` / `RemoteStopTransaction` with Home Assistant service calls.
7. Download config-entry diagnostics and inspect the preserved raw `BootNotification`, `StatusNotification`, `MeterValues`, and `GetConfiguration` payloads.

## GivEnergy-specific notes and TODOs

- The `MeterValues` parser is deliberately permissive and keeps raw samples because GivEnergy payloads can contain duplicate timestamps, multiple meter groups, and unusual phase labels.
- Lifetime/session energy mapping currently uses pragmatic heuristics and should be refined against more real charger captures.
- `SetChargingProfile` and other smart-charging actions are wired through as starter support, but the exact payload shapes that the GivEnergy EVC accepts may need charger-specific refinement.
