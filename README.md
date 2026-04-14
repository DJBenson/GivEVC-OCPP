# GivEnergy EVC OCPP

<img src="https://raw.githubusercontent.com/DJBenson/GivEVC-OCPP/refs/heads/main/brand/logo.png" />

Home Assistant custom integration for the GivEnergy EVC.

It runs a local OCPP server inside Home Assistant so your charger can connect directly to Home Assistant over your local network.

## 💖 Support this project

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-pink?logo=github)](https://github.com/sponsors/DJBenson)
[![Ko-fi](https://img.shields.io/badge/Support-Ko--fi-ff5f5f?logo=ko-fi)](https://ko-fi.com/djbenson)
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal)](https://paypal.me/jonathanthomson81)

#### WARNING! DO NOT ATTEMPT THIS CHANGE UNLESS YOU HAVE YOUR QR CODE (ATTACHED TO THE SIDE OF THE EVC) TO RELY ON - IT CONTAINS THE WIFI PASSWORD FOR THE DEVICE - IF YOU HAVE LOST THIS / IT IS DAMAGED, YOU CANNOT RE-CONNECT TO THE DEVICE! FOR EVERYONE ELSE, I HIGHLY RECOMMEND YOU TAKE A PHOTO OF THAT CODE AS IT IS VERY SUSCEPTIBLE TO DEGREDATION IF THE CHARGER IS EXPOSED TO THE ELEMENTS.

## Screenshots

<table>
  <thead>
    <tr>
      <th>Sensors</th>
      <th>Configuration</th>
      <th>Diagnostic</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top">
        <img width="323" height="850" alt="image" src="https://github.com/user-attachments/assets/f3eb35b8-0f28-4ba5-b7de-a704e82b8ae1" />
      </td>
      <td valign="top">
        <img width="320" height="670" alt="image" src="https://github.com/user-attachments/assets/3daae2fd-ee3d-41db-b528-e72e3196bbff" />
      </td>
      <td valign="top">
        <img width="318" height="512" alt="image" src="https://github.com/user-attachments/assets/0e0eecef-d848-480a-bda1-19a11c596d5f" />
      </td>
    </tr>
  </tbody>
</table>

## Who is this for?

Let's get this one out of the way early as it's been a recurring question - who exactly is this for?

1. If you currently use GivTCP and it works for you then *this isn't for you* - stick with GivTCP
2. If you're happy with the "Plug and Go" workaround (providing it doesn't die if/when GivEnergy's servers are taken down) then *this isn't for you*

However...

1. If you want total local control over your EVC with no third party cloud involvement - *THIS IS FOR YOU!*
2. If you would prefer to use GivTCP but it doesn't work - *THIS IS FOR YOU!*
3. If you just love tinkering (for now, this change can be undone) - *THIS MAY BE FOR YOU!*

## Features

- Local OCPP 1.6J listener built into Home Assistant
- Works with the GivEnergy EVC on its own configurable port, default `7655`
- Auto-adopts the first charger that connects
- Live charger sensors for status, power, current, voltage, session energy, total energy, and more
- Charger controls such as start/stop charging, reset, unlock connector, current limit, charge mode, and charger availability
- Scheduled charging - set time windows with a current limit, for specific days or every day
- RFID tag management - add and remove authorised RFID tags on the charger's local list
- Supports firmware updates (and downgrades) directly from the integration - refer to the "Firmware Management" section

## Feature Parity

Use this section to track parity between the GivEnergy portal/API and the local integration.

| Status | Feature | Notes |
| --- | --- | --- |
| ✔ | Start/Stop Charge | Fully supported. |
| ✔ | Energy Sensors | Total/Last Session/Today. |
| ✔ | Mode Selection | Solar (SuperEco), Hybrid (Eco), Grid (Boost), Inverter Control (LocalModBus) - the latter may be removed. |
| ✔ | Scheduling | The charger supports one schedule, managed using service calls - status entity provided. |
| ✔ | RFID tag management | Managed using service calls - status entity provided. |
| ✔ | Unlock Charge Port | Fully Supported |
| ✔ | Max Charge Power | Fully Supported |
| ✔ | Restart Charger | Fully Supported - supports 'soft' and 'hard' resets (factory reset is different - see below). |
| ✔ | Set LED State | Fully Supported |
| ✔ | Set DNO Fuse Size | Fully Supported. Note: if GivEnergy portal currently has this set to 'disabled' it will show a zero value in Home Assistant. I suggest re-setting this to a valid value (```40-100```) when you migrate. |
| ✔ | Factory Reset EV Charger | Fully Supported (Warning: this will remove the custom OCPP address and revert to the GivEnergy cloud). |
| ✔ | Enable Local Control | Fully Supported. Requires reboot on toggle. |
| ✔ | Read CP Voltage & Duty Cycle | Fully Supported. Response written to two sensors. |
| ✔ | Change Suspended State Wait Timeout | Fully Supported. |
| ◐ | Plug and Go | This is a server feature but is implemented in this integration. |
| ✖ | Max Charge Energy Per Session | This is a server feature. Doesn't make sense to implement. Use automations instead. |
| ✖ | Charger Configuration | This is a server feature. Will not be implemented |
| ✖ | Change CP Voltage Range | This is a server feature. Will not be implemented |

- Logging is controlled using the options in the config flow and diagnostics can be downloaded which contain verbose OCPP transaction logs.
- Power and Energy are obviously core features of Home Assistant sensors so you can build nice graphs using those
- Errors - handled by the logging system - also the ```Last message response``` sensor.

## Installation

### HACS

[![Open your Home Assistant instance and add this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DJBenson&repository=GivEVC-OCPP&category=integration)

1. Click the HACS button above, or add this repository as a custom repository in HACS.
2. Select category `Integration`.
3. Install `GivEnergy EVC OCPP`.
4. Restart Home Assistant.

### Home Assistant

[![Open your Home Assistant instance and start setting up this integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=givenergy_evc_ocpp)

1. Click the add-integration button above, or go to `Settings -> Devices & Services -> Add Integration`.

### Manual install

1. Copy `custom_components/givenergy_evc_ocpp` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add `GivEnergy EVC OCPP` from `Settings -> Devices & Services -> Add Integration`.

## Charger setup

This integration relies on you updating the OCPP address on your charger to point to Home Assistant instead of GivEnergy's cloud endpoint. This is currently a reversable change but given the current state of GivEnergy, this is subject to change at any time (at which point you'll want to do this anyway!).

### Factory reset the charger

There are two ways to achieve this depending upon whether your EVC is currently connected to the GivEnergy app (and if it's still working) or not - both are listed below;

#### Charger connected to GivEnergy app and currently online/working

1. Open the GivEnergy app and switch to the EV Charger section
2. Open the settings page and click on the "Factory Reset Charger"
3. The charger will beep/reboot and will enter AP mode

#### Charger never connected to GivEnergy app or is currently offline and won't come back online

1. Power cycle the charger
2. Remove the front panel of the EVC with a torx security bit 
3. Remove the inner panel with the thumbscrews 
4. Trigger the factory reset procedure by pressing the tamper switch 10 times
5. The charger will beep/reboot and will enter AP mode

#### Next steps once in AP mode

These next steps are time limited - from the point the EVC restarts, it is in AP mode for a limited amount of time before it stops broadcasting. If this happens, just power cycle the EVC and continue.

1. Scan for networks on your phone or laptop - look for an SSID starting ```EVSE-XXXXX```
2. Connect to that network using the password from the QR code sticker
3. Open a browser and enter ```http://192.168.4.1```
4. The local admin panel should appear - log in with the password ```12345678```
5. In the admin panel, enter your WiFi ```SSID``` and ```Password``` (if your charger is connected by LAN, you can omit these settings)
6. Scroll down to the ```OCPP Server``` field and overwrite it with the address and port of your newly installed OCPP server - examples below
7. You can also set the DHCP mode here - not necessary - leave alone unless required - it's better doing these things on the router
8. Click on the ```Save``` and then the ```Restart``` buttons

Once the charger has rebooted and connected (solid blue light if no car is connected) then it should automatically connect to Home Assistant. Open the integration and you should see your entities gain values (look for your serial number to prove its working). It can take a couple of minutes. 

If the charger doesn't connect, you missed something, go back and re-trace your steps. Pay particular attention to;

1. The WiFi credentials 
2. The OCPP Server address - it must match what you configured in Home Assistant

Examples:

- `ws://homeassistant.local:7655`
- `ws://192.168.1.50:7655`
- `ws://192.168.1.50:7655/<charge_point_id>`

If the charger connects without a path, that is fine. The integration can still identify and adopt it from the boot details it sends after connecting.

## First setup

The integration is designed to need very little configuration.

Typical setup is:

1. Add the integration.
2. Leave the listen port at `7655`.
3. Let the charger connect in.

Existing single-charger installs keep the current legacy behavior and entity IDs.

Additional chargers are discovered under the same listener and can then be accepted explicitly from Home Assistant.

## What you get

Depending on what the charger reports, Home Assistant can expose:

**Binary sensors**
- `Car plugged in`

**Sensors**
- `Connection status`
- `Charger status`
- `Operational status`
- `Live power` (`kW`)
- `Live current` (`A`)
- `Live voltage` (`V`)
- `Charge session energy` (`kWh`)
- `Meter energy` (`kWh`, total increasing)
- `Charge start time`
- `Charge end time`
- `Charge session duration` (displayed in minutes)
- `Current limit` (`A`)
- `EVSE min current` (`A`)
- `EVSE max current` (`A`)
- `Charging schedules` - count of active schedule windows, with details as attributes
- `Last seen` - diagnostic
- `Heartbeat age` - diagnostic
- `Error code` - diagnostic
- `Serial number` - diagnostic
- `Local IP address` - diagnostic
- `Meter values interval` - diagnostic
- `Last message response` - diagnostic, shows the last charger command outcome such as `Accepted`, `Rejected`, or `RebootRequired`
- `CP voltage` (`V`) - diagnostic, populated when the CP read button is pressed
- `CP duty cycle` (`%`) - diagnostic, populated when the CP read button is pressed
- `Firmware status` - diagnostic

**Switches**
- `Charge now`
- `Charger enabled`
- `Plug and Go` - when enabled, charging starts automatically as soon as a car is plugged in
- `Local Modbus` - _yes_, it still supports local modbus, the limitations (modbus on ethernet only active after ~10 minutes) still apply, so GivTCP can still read/control the EVC alongside this integration
- `Front panel LEDs`
- `Firmware server` - enables the built-in firmware transfer server (see Firmware management)

**Numbers**
- `Current limit` (`A`) - set the maximum charge current
- `Max import capacity` (`A`)
- `Randomised delay duration` (`s`) - random delay before charging starts
- `Suspended state timeout` (`s`)

**Selects**
- `Charge mode` - SuperEco / Eco / Boost / ModbusSlave
- `Firmware file` - choose a firmware version to install (requires firmware server enabled)

**Buttons**
- `Soft reset`
- `Hard reset`
- `Factory reset`
- `Unlock connector`
- `Read CP voltage & duty cycle`
- `Trigger meter values`
- `Refresh configuration`
- `Install selected firmware` (requires firmware server enabled)

## CP voltage and duty cycle

The integration can query the charger's Control Pilot state using the `Read CP voltage & duty cycle` button. When pressed, the charger returns a CP voltage reading and PWM duty cycle, which are then exposed through the `CP voltage` and `CP duty cycle` diagnostic sensors.

These values are useful when diagnosing cable detection, EV readiness, and PWM current signalling between the charger and the vehicle.

### CP voltages

| CP state | Voltage range | Meaning |
|----------|---------------|---------|
| State 0 (Off) | `0 V` or `-12 V` | The EVSE is off. No charging power is being offered. |
| State 1 (Standby) | `+12 V` | Charger is ready, but no EV is requesting charge. |
| State 2 (EV connected) | `+9 V` | EV is connected and ready. Charger is advertising available current via PWM. |
| State 3 (Charging) | `+6 V` | Charging is in progress. |
| State 4 (Ventilation required) | `+3 V` | Rare legacy state indicating ventilation is required. |
| State E (Fault) | `0 V` or `-12 V` | Fault condition on the EVSE or vehicle side. |

### Duty cycles

For normal AC charging, duty cycle represents the maximum current the charger is advertising on the control pilot line.

| Duty cycle | Meaning |
|------------|---------|
| `< 3%` | No charging allowed |
| `3%` to `7%` | High-level digital communication required |
| `> 7%` to `< 8%` | No charging allowed |
| `8%` to `< 10%` | Minimum AC charging current, effectively `6 A` |
| `10%` to `85%` | Available current = `duty_cycle * 0.6 A` |
| `> 85%` to `96%` | Available current = `(duty_cycle - 64) * 2.5 A` |
| `> 96%` to `97%` | Maximum standard AC current, effectively `80 A` |
| `> 97%` | No charging allowed |

Example:

- A duty cycle of `53%` means the charger is advertising about `31.8 A` (`53 * 0.6`)

## Firmware management

The integration includes a built-in firmware management tool which is disabled by default. Firmware versions are discovered from a configurable manifest URL, which by default points at the separate firmware repository ([github.com/djbenson/giv-firmware](https://github.com/djbenson/giv-firmware/)). When you enable the firmware server, the manifest is loaded and the available firmware files are shown in the drop down.

Firmware files are downloaded on demand into the local cache and kept there for reuse. Before any update is sent to the charger, the integration verifies the cached file against the manifest checksum. If the file is missing or does not match, it is downloaded again. The drop down shows either `[cached]` or `[download]` to indicate whether the file already exists locally.

1. Ensure you are running at least version 0.2.0 of the integration
2. Enable the "Firmware server" toggle
3. Select a firmware file from the drop down list
4. Click on the "Install selected firmware" button
5. Wait! Keep an eye on the Firmware Status sensor - all being well it should go from Downloading -> Downloaded -> Installing -> Installed within a couple of minutes
6. Check the version number at the top left hand corner of the integration once the sensor shows ```Installed```.

<table>
  <thead>
    <tr>
      <th>Configuration</th>
      <th>Diagnostic</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top">
        <img width="319" height="826" alt="image" src="https://github.com/user-attachments/assets/cb9932db-94ba-40a6-877d-9cd737f78844" />
      </td>
      <td valign="top">
        <img width="321" height="178" alt="image" src="https://github.com/user-attachments/assets/22e3583d-4467-4bcd-9c86-e61b79d752b1" />
      </td>
    </tr>
  </tbody>
</table>

WARNING! Use this at your own risk! I accept no liability if you brick your device (unlikely but needs to be said). Several people have upgraded their chargers to the latest version using this integration (and I've tested downgrading) but it's on you if it bricks your charger.

## Scheduled charging

The integration supports setting a charging schedule directly on the charger which will fire whether the charger is connected to a portal or not.

Schedules are managed via Home Assistant service calls - there are two services:

- `givenergy_evc_ocpp.set_charging_schedule` - set a schedule
- `givenergy_evc_ocpp.clear_charging_schedule` - remove the active schedule

### Setting a schedule

The `set_charging_schedule` service takes the following parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `start` | Yes | Start of the charging window (local time) |
| `end` | Yes | End of the charging window (local time) - can be earlier than start for overnight windows |
| `limit_a` | Yes | Maximum charge current in amps (6-32A) |
| `days` | No | Days the schedule applies to - leave empty for every day, or pick a subset |
| `show_ocpp_output` | No | Return the OCPP `SetChargingProfile` payload sent to the charger, along with the charger response |

The `days` field accepts any combination of `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`. Leave it empty (or select all seven) and the schedule runs every day. Select a subset and it becomes a weekly recurring schedule.

All times are entered in local time - the integration handles the UTC conversion automatically.

Only one schedule is active at a time. Setting a new one replaces the previous one. The `Charging schedules` sensor shows the current count and exposes each window as an attribute.

Enable `Show OCPP output` when calling the action to display the generated OCPP payload on the action response screen.

### Clearing a schedule

Calling `givenergy_evc_ocpp.clear_charging_schedule` sends a `ClearChargingProfile` to the charger, removing the active schedule. The charger will revert to its default behaviour (charge at full rate whenever a car is connected, subject to other settings).

## RFID tag management

The integration lets you manage the charger's local RFID authorisation list directly.

There are two service calls:

- `givenergy_evc_ocpp.add_rfid_tag` - add or update a tag
- `givenergy_evc_ocpp.remove_rfid_tag` - remove a tag

### Adding a tag

| Parameter | Required | Description |
|-----------|----------|-------------|
| `id_tag` | Yes | The RFID tag identifier, as read by the charger |
| `expiry_date` | No | Expiry date/time in ISO 8601 format - leave blank for no expiry |

Example with no expiry:

```yaml
service: givenergy_evc_ocpp.add_rfid_tag
data:
  id_tag: "1234ABCD"
```

Example with an expiry date:

```yaml
service: givenergy_evc_ocpp.add_rfid_tag
data:
  id_tag: "1234ABCD"
  expiry_date: "2026-12-31T00:00:00Z"
```

### Removing a tag

| Parameter | Required | Description |
|-----------|----------|-------------|
| `id_tag` | Yes | The RFID tag identifier to remove |

```yaml
service: givenergy_evc_ocpp.remove_rfid_tag
data:
  id_tag: "1234ABCD"
```

## Diagnostics

This integration keeps more raw charger data than the stock OCPP integration so charger behavior is easier to debug.

Diagnostics may include:

- last boot payload
- last status payload
- last meter values payload
- parsed meter values
- full `GetConfiguration` result
- optional rolling OCPP frame history

If you need deeper troubleshooting, enable enhanced OCPP diagnostics in the integration options and then download diagnostics from Home Assistant.

## Notes

- This integration is built specifically for the GivEnergy EVC. It is not trying to be a generic OCPP integration.
- The charger can connect with or without a charge point ID in the websocket path.
- One listener can now handle more than one GivEnergy charger.
- The original primary charger keeps the legacy entity IDs for compatibility.
- Additional chargers are onboarded separately after discovery and use charger-scoped entity IDs.
