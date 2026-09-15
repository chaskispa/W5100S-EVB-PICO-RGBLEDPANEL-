# W5100S-EVB-PICO RGB Panel Ethernet

CircuitPython controller for `32x16` HUB75 panels. It supports horizontal
chains of 3, 6, or 9 panels, plus a 12-panel serpentine wall arranged as six
panels across by two panels high. The 12-panel wall behaves as one `192x32`
display and is the default layout on a fresh installation. Its default `4x`
font fills the entire 32-pixel display height.

## Install on the W5100S-EVB-PICO

### 1. Install CircuitPython

1. Download the latest stable UF2 for the
   [W5100S-EVB-PICO](https://circuitpython.org/board/wiznet_w5100s_evb_pico/).
   This project was developed with CircuitPython `10.2.1`.
2. Use a USB cable that supports data, not a charge-only cable.
3. With the board disconnected, hold `BOOTSEL`, connect USB, and release
   `BOOTSEL` when the `RPI-RP2` drive appears. If the board has a reset button,
   you can instead hold `BOOTSEL`, press and release reset, and then release
   `BOOTSEL`.
4. Copy the downloaded `.uf2` file to `RPI-RP2`.
5. The boot drive will disconnect automatically and a new drive named
   `CIRCUITPY` will appear.

### 2. Copy the project

Copy these items to the root of `CIRCUITPY`, preserving the `lib` directory:

```text
CIRCUITPY/
├── code.py
└── lib/
    ├── adafruit_ticks.mpy
    ├── adafruit_display_text/
    └── adafruit_wiznet5k/
```

The required compiled libraries are already included in this repository. Do
not copy `README.md` to the board unless you want it there for reference.

Safely eject `CIRCUITPY`, connect Ethernet, and reset or power-cycle the board.
After DHCP completes, the panel displays its IP address. Open
`http://PANEL_IP/` in a browser to configure the operating mode and network.

If the panel stays blank, connect to the CircuitPython USB serial console at
`115200` baud to read the startup error.

## Panel wiring and power

The firmware uses GPIO `0` through `11` for HUB75 and GPIO `16` through `19`
for the onboard W5100S. Connect the HUB75 panels in one chain from `OUT` to
`IN`.

Power the panels from a separate regulated `5 V` supply with enough current for
the complete chain. Connect the panel power-supply ground to a GND pin on the
W5100S-EVB-PICO. Do not power the panel chain from USB.

### 12-panel serpentine layout

Select **12 panels (6x2 serpentine, 192x32)** in the web interface. Viewed from
the front, number and daisy-chain the panels in this order:

```text
DATA IN -> [1] -> [2] -> [3] -> [4] -> [5] -> [6]
                                                   |
          [12] <- [11] <- [10] <- [9] <- [8] <- [7]
```

Connect panel 6 `OUT` to panel 7 `IN` at the right edge. Rotate every panel in
the bottom row by 180 degrees so the short serpentine connections line up.
CircuitPython's tiled serpentine mapping corrects the rotated lower row and
presents the whole assembly as a single `192x32` canvas.

## First setup

After DHCP completes, the panel shows its IP address. Open that address in a
browser to configure text appearance and network settings.

Configuration is stored in the RP2040 non-volatile memory and survives power
cycles. Saving from the web UI automatically reloads the program.

## UDP text

Send a raw UTF-8 datagram to UDP port `5000`. Newlines are converted to spaces
and text longer than the physical display width is clipped.

Example:

```sh
printf 'Hello' | nc -u PANEL_IP 5000
```

Use `[RRGGBB]` before a block of text to set that block's color. Tags contain
six hexadecimal RGB digits, are case-insensitive, and do not use display
space. Text before the first tag uses the color configured in the web UI.

```sh
printf '[FF0000]RED [00FF00]GREEN [0000FF]BLUE' | nc -u PANEL_IP 5000
printf '[FFFFFF]Español [00FFFF]Ñ' | nc -u PANEL_IP 5000
```

Malformed tags are displayed as ordinary text. Each valid tag remains active
until the next valid color tag or the end of the message. A datagram may
contain up to 16 color changes. UTF-8 Spanish characters are preserved in the
large font: `á é í ó ú ü ñ`, their uppercase forms, `¿`, and `¡`.

The web UI controls persistent text color, panel color order (`RGB` or `GRB`),
font scale, font style (regular, bold, or shadow), animation, entrance and exit
effects, and animation speed. The 16-pixel-high layouts offer `1x` and `2x`
font sizes. The 12-panel `192x32` layout also offers `3x` (24 pixels high) and
`4x` (the full 32-pixel height). Appearance changes apply immediately without
a reboot. Static messages are clipped; animated messages can contain up to 80
characters. A Send Message form displays text directly from the browser using
the active appearance and animation settings.

Scaled (`2x` through `4x`) text uses a lightweight native 5x8 LED bitmap font.
At `4x`, each glyph occupies 20x32 pixels and fills the complete 12-panel wall
height without loading the memory-heavy BDF font system.

The web interface can upload any browser-supported image. The browser resizes
it to the active panel canvas and sends RGB565 data, which is stored as
`saved.rgb565` and displayed after reboot. CircuitPython owns write access to
the filesystem during normal operation, so the USB CIRCUITPY drive is
read-only to the host; use the UF2 bootloader when firmware files must change.

Panel count (`3`, `6`, `9`, or `12`) is persistent and configurable in the web
UI. Changing it restarts the controller so the HUB75 framebuffer can be rebuilt
at `96x16`, `192x16`, `288x16`, or `192x32`, respectively. The first three
layouts are single horizontal rows; the 12-panel layout is the 6-by-2
serpentine wall shown above.

## Network

The web UI supports DHCP or a static IPv4 address, subnet mask, gateway, and DNS
server. When changing to a static address, reconnect the browser using the new
address after the board reloads.

Static IP mode is intended for permanent installations. At boot the panel wall
stays completely blank: it does not show the network message, its IP address,
or a saved bitmap. The first UDP text datagram supplies the content to display.
DHCP mode retains the visible network and IP startup indicators.
