# GivEnergy EVC OCPP

Home Assistant custom integration for the GivEnergy EVC.

It runs a local OCPP server inside Home Assistant so your charger can connect directly to Home Assistant over your local network.

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
2. Connect to that network
3. Open a browser and enter ```http://192.168.4.1```
4. The local admin panel should appear - log in with the password ```12345678```
5. In the admin panel, enter your WiFi ```SSID``` and ```Password```
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
3. Leave `Expected charge point ID` blank unless you know you need it.
4. Let the charger connect in.

The first charger that connects will normally be adopted automatically.

## What you get

Depending on what the charger reports, Home Assistant can expose:

- `Car plugged in` - binary sensor
- `Charger status` - sensor
- `Connection status` - sensor
- `Live power` - sensor (`kW`)
- `Live current` - sensor (`A`)
- `Live voltage` - sensor (`V`)
- `Charge session energy` - sensor (`kWh`)
- `Meter energy` - sensor (`kWh`)
- `Charge start time` - sensor
- `Charge end time` - sensor
- `Charge session duration` - sensor (`s`, suggested as minutes in Home Assistant)
- `Current limit` - sensor (`A`)
- `EVSE min current` - sensor (`A`)
- `EVSE max current` - sensor (`A`)
- `Charge now` - switch
- `Charger enabled` - switch
- `Plug and Go` - switch
- `Local Modbus` - switch
- `Front panel LEDs` - switch
- `Charge mode` - select
- `Soft reset` - button
- `Hard reset` - button
- `Unlock connector` - button
- `Trigger meter values` - button
- `Refresh configuration` - button

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
- The integration assumes a single charger for now.
