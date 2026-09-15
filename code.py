import json
import os
import time

import board
import busio
import digitalio
import displayio
import framebufferio
import microcontroller
import rgbmatrix
import supervisor
import terminalio
from adafruit_display_text import label
import adafruit_wiznet5k.adafruit_wiznet5k_socketpool as socketpool
from adafruit_wiznet5k.adafruit_wiznet5k import WIZNET5K


DEFAULT_PANEL_COUNT = 12
SUPPORTED_PANEL_COUNTS = (3, 6, 9, 12)
PANEL_WIDTH = 32
PANEL_HEIGHT = 16
BIT_DEPTH = 4
TEXT_UDP_PORT = 5000
HTTP_PORT = 80
HTTP_POLL_INTERVAL = 0.025
BITMAP_PATH = "/saved.rgb565"
BITMAP_TEMP_PATH = "/saved.tmp"
BITMAP_MAGIC = b"RGB5"
MAX_COLOR_CHANGES = 16

# font5x8.bin uses CP437 positions, while received text is Unicode. These
# custom glyphs preserve common Spanish characters and use clearer accents
# than the compact CP437 versions.
LED_FONT_GLYPHS = {
    0x00C1: bytes((0xF8, 0x24, 0x22, 0x25, 0xF8)),  # Á
    0x00C9: bytes((0xFE, 0x92, 0x92, 0x93, 0x82)),  # É
    0x00CD: bytes((0x00, 0x84, 0xFE, 0x85, 0x00)),  # Í
    0x00D1: bytes((0xFC, 0x09, 0x11, 0x22, 0xFE)),  # Ñ
    0x00D3: bytes((0x78, 0x84, 0x86, 0x85, 0x78)),  # Ó
    0x00DA: bytes((0x7C, 0x80, 0x82, 0x81, 0x7C)),  # Ú
    0x00DC: bytes((0x7E, 0x81, 0x80, 0x81, 0x7E)),  # Ü
    0x00E1: bytes((0x20, 0x54, 0x56, 0x79, 0x40)),  # á
    0x00E9: bytes((0x38, 0x54, 0x56, 0x55, 0x18)),  # é
    0x00ED: bytes((0x00, 0x44, 0x7E, 0x41, 0x00)),  # í
    0x00F1: bytes((0x7C, 0x09, 0x05, 0x06, 0x7A)),  # ñ
    0x00F3: bytes((0x38, 0x44, 0x46, 0x45, 0x38)),  # ó
    0x00FA: bytes((0x3C, 0x40, 0x42, 0x21, 0x7C)),  # ú
    0x00FC: bytes((0x3C, 0x41, 0x40, 0x21, 0x7C)),  # ü
}
LED_FONT_CODEPOINTS = {
    0x00A1: 173,  # ¡
    0x00BF: 168,  # ¿
}

DEFAULT_CONFIG = {
    "panel_count": DEFAULT_PANEL_COUNT,
    "text_color": "FFFFFF",
    "text_size": 4,
    "color_order": "GRB",
    "font_style": "regular",
    "animation": "static",
    "animation_in": "none",
    "animation_out": "none",
    "speed": 30,
    "dhcp": True,
    "ip": "192.168.1.50",
    "mask": "255.255.255.0",
    "gateway": "192.168.1.1",
    "dns": "8.8.8.8",
}
NVM_MAGIC = b"RGB1"


def valid_ipv4(value):
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(part != "" and 0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def normalize_color(value):
    value = str(value).strip().lstrip("#").upper()
    if len(value) != 6:
        return DEFAULT_CONFIG["text_color"]
    try:
        int(value, 16)
    except ValueError:
        return DEFAULT_CONFIG["text_color"]
    return value


def validate_config(candidate):
    result = dict(DEFAULT_CONFIG)
    if isinstance(candidate, dict):
        result.update(candidate)
    result["dhcp"] = bool(result["dhcp"])
    try:
        result["panel_count"] = int(result["panel_count"])
    except (ValueError, TypeError):
        result["panel_count"] = DEFAULT_PANEL_COUNT
    if result["panel_count"] not in SUPPORTED_PANEL_COUNTS:
        result["panel_count"] = DEFAULT_PANEL_COUNT
    result["text_color"] = normalize_color(result["text_color"])
    result["color_order"] = str(result["color_order"]).upper()
    if result["color_order"] not in ("RGB", "GRB"):
        result["color_order"] = DEFAULT_CONFIG["color_order"]
    for key, allowed in (
        ("font_style", ("regular", "bold", "shadow")),
        ("animation", ("static", "scroll_left", "scroll_right", "bounce")),
        ("animation_in", ("none", "left", "right")),
        ("animation_out", ("none", "left", "right")),
    ):
        result[key] = str(result[key]).lower()
        if result[key] not in allowed:
            result[key] = DEFAULT_CONFIG[key]
    try:
        result["text_size"] = int(result["text_size"])
    except (ValueError, TypeError):
        result["text_size"] = 1
    allowed_text_sizes = (1, 2, 3, 4) if result["panel_count"] == 12 else (1, 2)
    if result["text_size"] not in allowed_text_sizes:
        result["text_size"] = 4 if result["panel_count"] == 12 else 2
    try:
        result["speed"] = int(result["speed"])
    except (ValueError, TypeError):
        result["speed"] = DEFAULT_CONFIG["speed"]
    result["speed"] = max(5, min(120, result["speed"]))
    for key in ("ip", "mask", "gateway", "dns"):
        value = str(result[key])
        result[key] = value if valid_ipv4(value) else DEFAULT_CONFIG[key]
    return {key: result[key] for key in DEFAULT_CONFIG}


def load_config():
    nvm = microcontroller.nvm
    if nvm is None or len(nvm) < 8 or bytes(nvm[0:4]) != NVM_MAGIC:
        return dict(DEFAULT_CONFIG)
    size = (nvm[4] << 8) | nvm[5]
    if size <= 0 or size > len(nvm) - 6:
        return dict(DEFAULT_CONFIG)
    try:
        return validate_config(json.loads(bytes(nvm[6:6 + size]).decode("utf-8")))
    except (ValueError, UnicodeError):
        return dict(DEFAULT_CONFIG)


def save_config(candidate):
    nvm = microcontroller.nvm
    if nvm is None:
        raise RuntimeError("NVM is not available")
    payload = json.dumps(validate_config(candidate)).encode("utf-8")
    if len(payload) > len(nvm) - 6:
        raise RuntimeError("Configuration is too large for NVM")
    blob = NVM_MAGIC + bytes((len(payload) >> 8, len(payload) & 0xFF)) + payload
    nvm[0:len(blob)] = blob


config = load_config()
panel_count = config["panel_count"]


# HUB75 display -------------------------------------------------------------

# The 12-panel installation is wired as two rows of six. The chain enters the
# upper-left panel, crosses the top row, then folds down and crosses the lower
# row from right to left. CircuitPython's tiled serpentine mapping turns that
# physical chain into one continuous 192x32 drawing surface.
panel_rows = 2 if panel_count == 12 else 1
panel_columns = panel_count // panel_rows
matrix_width = PANEL_WIDTH * panel_columns
matrix_height = PANEL_HEIGHT * panel_rows
displayio.release_displays()
matrix = rgbmatrix.RGBMatrix(
    width=matrix_width,
    height=matrix_height,
    bit_depth=BIT_DEPTH,
    tile=panel_rows,
    serpentine=panel_rows > 1,
    rgb_pins=[board.GP0, board.GP1, board.GP2,
              board.GP3, board.GP4, board.GP5],
    addr_pins=[board.GP6, board.GP7, board.GP8],
    clock_pin=board.GP9,
    latch_pin=board.GP10,
    output_enable_pin=board.GP11,
)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=False, rotation=0)
with open("/font5x8.bin", "rb") as font_file:
    led_font_data = font_file.read()
if len(led_font_data) != 1282 or led_font_data[0:2] != b"\x05\x08":
    raise RuntimeError("Invalid font5x8.bin")
text_group = None
text_label = None
text_bitmap = None
text_tile = None
image_bitmap = None
image_tile = None
last_message = "NETWORK..." if config["dhcp"] else " "
pending_message = None
animation_phase = "steady"
animation_x = 0.0
animation_direction = -1
text_pixel_width = 1
last_animation_time = time.monotonic()
last_drawn_x = 0


def panel_color(rgb_color):
    if config["color_order"] == "RGB":
        return rgb_color
    red = (rgb_color >> 16) & 0xFF
    green = (rgb_color >> 8) & 0xFF
    blue = rgb_color & 0xFF
    return (green << 16) | (red << 8) | blue


def parse_colored_text(message, max_chars):
    """Return visible text and one RGB color per character.

    UDP messages may change the active color with [RRGGBB]. Color tags do not
    consume display characters; malformed tags are displayed literally.
    """
    clean = message.replace("\r", " ").replace("\n", " ").strip() or " "
    current_color = int(config["text_color"], 16)
    characters = []
    colors = []
    index = 0
    color_changes = 0
    while index < len(clean) and len(characters) < max_chars:
        if (
            index + 7 < len(clean)
            and clean[index] == "["
            and clean[index + 7] == "]"
        ):
            candidate = clean[index + 1:index + 7]
            try:
                next_color = int(candidate, 16)
                if color_changes < MAX_COLOR_CHANGES:
                    current_color = next_color
                    color_changes += 1
                index += 8
                continue
            except ValueError:
                pass
        characters.append(clean[index])
        colors.append(current_color)
        index += 1
    if not characters:
        characters.append(" ")
        colors.append(current_color)
    return clean, "".join(characters), colors


def build_text(message, use_entrance=True):
    global text_group, text_label, text_bitmap, text_tile
    global last_message, animation_phase
    global animation_x, animation_direction, text_pixel_width, last_drawn_x
    scale = config["text_size"]
    max_chars = matrix_width // (6 * scale)
    # Scrolling can show a message longer than the static panel width.
    max_chars = 80 if config["animation"] != "static" else max_chars
    clean, visible_text, character_colors = parse_colored_text(message, max_chars)
    last_message = clean
    text_group = displayio.Group(scale=scale)
    if scale >= 2:
        text_group.y = (matrix_height - 8 * scale) // 2
        # Native 5x8 LED glyphs scale cleanly up to 20x32 at 4x. Unlike the
        # built-in 6x12 terminal font, no pixels or descenders are clipped.
        palette_colors = []
        color_indexes = []
        for rgb_color in character_colors:
            if rgb_color not in palette_colors:
                palette_colors.append(rgb_color)
            color_indexes.append(palette_colors.index(rgb_color) + 1)
        text_bitmap = displayio.Bitmap(
            max(1, len(visible_text) * 6), 8, len(palette_colors) + 1
        )
        palette = displayio.Palette(len(palette_colors) + 1)
        palette[0] = 0x000000
        palette.make_transparent(0)
        for palette_index, rgb_color in enumerate(palette_colors):
            palette[palette_index + 1] = panel_color(rgb_color)
        for char_index, character in enumerate(visible_text):
            codepoint = ord(character)
            custom_glyph = LED_FONT_GLYPHS.get(codepoint)
            if custom_glyph is None:
                codepoint = LED_FONT_CODEPOINTS.get(codepoint, codepoint)
                if codepoint > 255:
                    codepoint = ord("?")
                glyph_start = 2 + codepoint * 5
            for glyph_x in range(5):
                if custom_glyph is None:
                    column = led_font_data[glyph_start + glyph_x]
                else:
                    column = custom_glyph[glyph_x]
                for glyph_y in range(8):
                    if column & (1 << glyph_y):
                        color_index = color_indexes[char_index]
                        text_bitmap[
                            char_index * 6 + glyph_x, glyph_y
                        ] = color_index
                        if config["font_style"] == "bold" and glyph_x < 4:
                            text_bitmap[
                                char_index * 6 + glyph_x + 1, glyph_y
                            ] = color_index
        text_tile = displayio.TileGrid(text_bitmap, pixel_shader=palette)
        text_group.append(text_tile)
        text_label = None
    else:
        text_bitmap = None
        text_tile = None
        # label.Label positions text around its baseline, so its center belongs
        # at the vertical midpoint of the complete logical display.
        text_y = matrix_height // 2
        runs = []
        run_start = 0
        for index in range(1, len(visible_text) + 1):
            if (
                index == len(visible_text)
                or character_colors[index] != character_colors[run_start]
            ):
                runs.append((
                    run_start,
                    visible_text[run_start:index],
                    character_colors[run_start],
                ))
                run_start = index
        if config["font_style"] == "shadow":
            for start, run_text, _ in runs:
                shadow = label.Label(
                    terminalio.FONT, text=run_text, color=0x000000
                )
                shadow.x = start * 6 + 1
                shadow.y = text_y + 1
                text_group.append(shadow)
        text_label = None
        for start, run_text, rgb_color in runs:
            run_label = label.Label(
                terminalio.FONT, text=run_text, color=panel_color(rgb_color)
            )
            run_label.x = start * 6
            run_label.y = text_y
            text_group.append(run_label)
            if text_label is None:
                text_label = run_label
        if config["font_style"] == "bold":
            for start, run_text, rgb_color in runs:
                bold = label.Label(
                    terminalio.FONT, text=run_text, color=panel_color(rgb_color)
                )
                bold.x = start * 6 + 1
                bold.y = text_y
                text_group.append(bold)
    text_pixel_width = max(1, len(visible_text) * 6 * scale)
    entrance = config["animation_in"] if use_entrance else "none"
    if entrance == "left":
        animation_x = -text_pixel_width
        animation_phase = "enter"
    elif entrance == "right":
        animation_x = matrix_width
        animation_phase = "enter"
    else:
        animation_x = 0.0
        animation_phase = "steady"
    animation_direction = -1
    text_group.x = int(animation_x)
    last_drawn_x = int(animation_x)
    display.root_group = text_group
    display.refresh(minimum_frames_per_second=0)
    print("Text:", visible_text)


def show_text(message):
    global pending_message, animation_phase, animation_direction
    if text_group is not None and config["animation_out"] != "none":
        pending_message = message
        animation_phase = "exit"
        animation_direction = -1 if config["animation_out"] == "left" else 1
    else:
        pending_message = None
        build_text(message)


def show_saved_bitmap():
    global image_bitmap, image_tile
    try:
        with open(BITMAP_PATH, "rb") as bitmap_file:
            if bitmap_file.read(4) != BITMAP_MAGIC:
                return False
            width = int.from_bytes(bitmap_file.read(2), "big")
            height = int.from_bytes(bitmap_file.read(2), "big")
            if width != matrix_width or height != matrix_height:
                return False
            image_bitmap = displayio.Bitmap(width, height, 65536)
            for pixel in range(width * height):
                packed = bitmap_file.read(2)
                if len(packed) != 2:
                    return False
                image_bitmap[pixel] = (packed[0] << 8) | packed[1]
        converter = displayio.ColorConverter(
            input_colorspace=displayio.Colorspace.RGB565
        )
        image_tile = displayio.TileGrid(image_bitmap, pixel_shader=converter)
        group = displayio.Group()
        group.append(image_tile)
        display.root_group = group
        display.refresh(minimum_frames_per_second=0)
        print("Saved bitmap displayed")
        return True
    except (OSError, ValueError) as error:
        print("Bitmap error:", error)
        return False


def update_animation():
    global animation_x, animation_direction, animation_phase
    global pending_message, last_animation_time, last_drawn_x
    if text_group is None:
        return
    now = time.monotonic()
    elapsed = min(now - last_animation_time, 0.1)
    last_animation_time = now
    distance = config["speed"] * elapsed

    if animation_phase == "exit":
        animation_x += animation_direction * distance
        if animation_x <= -text_pixel_width or animation_x >= matrix_width:
            message = pending_message
            pending_message = None
            build_text(message if message is not None else " ")
            return
    elif animation_phase == "enter":
        if animation_x < 0:
            animation_x = min(0, animation_x + distance)
        else:
            animation_x = max(0, animation_x - distance)
        if animation_x == 0:
            animation_phase = "steady"
    elif config["animation"] == "scroll_left":
        animation_x -= distance
        if animation_x <= -text_pixel_width:
            animation_x = matrix_width
    elif config["animation"] == "scroll_right":
        animation_x += distance
        if animation_x >= matrix_width:
            animation_x = -text_pixel_width
    elif config["animation"] == "bounce":
        left_limit = min(0, matrix_width - text_pixel_width)
        right_limit = max(0, matrix_width - text_pixel_width)
        animation_x += animation_direction * distance
        if animation_x <= left_limit:
            animation_x = left_limit
            animation_direction = 1
        elif animation_x >= right_limit:
            animation_x = right_limit
            animation_direction = -1
    else:
        return

    next_x = int(animation_x)
    if next_x != last_drawn_x:
        last_drawn_x = next_x
        text_group.x = next_x
        display.refresh(minimum_frames_per_second=0)


if config["dhcp"]:
    build_text("NETWORK...", use_entrance=False)
else:
    # Fixed-IP installations remain completely dark until content arrives.
    display.root_group = displayio.Group()
    display.refresh(minimum_frames_per_second=0)


# Ethernet -----------------------------------------------------------------

cs = digitalio.DigitalInOut(board.GP17)
spi_bus = busio.SPI(board.GP18, MOSI=board.GP19, MISO=board.GP16)
ethernet = WIZNET5K(spi_bus, cs, is_dhcp=config["dhcp"])
pool = socketpool.SocketPool(ethernet)
if not config["dhcp"]:
    ethernet.ifconfig = tuple(
        pool.inet_aton(config[key]) for key in ("ip", "mask", "gateway", "dns")
    )

ip_address = ethernet.pretty_ip(ethernet.ip_address)
if config["dhcp"]:
    build_text(ip_address, use_entrance=False)

text_udp = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
text_udp.settimeout(0)
text_udp.bind((ip_address, TEXT_UDP_PORT))

http_server = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
http_server.settimeout(0)
http_server.bind((ip_address, HTTP_PORT))
http_server.listen(1)
text_packet = bytearray(1024)


def poll_text_udp():
    try:
        size, remote = text_udp.recvfrom_into(text_packet)
    except Exception:
        return
    if size <= 0:
        return
    try:
        message = bytes(text_packet[0:size]).decode("utf-8")
    except UnicodeError:
        print("Ignored invalid UTF-8 from", remote)
        return
    show_text(message)


# Web configuration ---------------------------------------------------------

def url_decode(value):
    value = value.replace("+", " ")
    output = bytearray()
    index = 0
    while index < len(value):
        if value[index] == "%" and index + 2 < len(value):
            try:
                output.append(int(value[index + 1:index + 3], 16))
                index += 3
                continue
            except ValueError:
                pass
        output.extend(value[index].encode("utf-8"))
        index += 1
    return output.decode("utf-8")


def parse_query(query):
    values = {}
    for item in query.split("&"):
        if "=" in item:
            key, value = item.split("=", 1)
            values[url_decode(key)] = url_decode(value)
    return values


def option(value, current, title):
    selected = " selected" if int(value) == int(current) else ""
    return '<option value="{}"{}>{}</option>'.format(value, selected, title)


def text_option(value, current, title):
    selected = " selected" if value == current else ""
    return '<option value="{}"{}>{}</option>'.format(value, selected, title)


def web_page(message=""):
    dhcp_checked = " checked" if config["dhcp"] else ""
    static_checked = "" if config["dhcp"] else " checked"
    if config["panel_count"] == 12:
        size_options = option(1, config["text_size"], "Small (1x, 8 px)")
        size_options += option(2, config["text_size"], "Medium (2x, 16 px)")
        size_options += option(3, config["text_size"], "Large (3x, 24 px)")
        size_options += option(4, config["text_size"], "Full height (4x, 32 px)")
    else:
        size_options = option(1, config["text_size"], "Normal (1x)")
        size_options += option(2, config["text_size"], "Large (2x)")
    rgb_selected = " selected" if config["color_order"] == "RGB" else ""
    grb_selected = " selected" if config["color_order"] == "GRB" else ""
    font_options = text_option("regular", config["font_style"], "Regular")
    font_options += text_option("bold", config["font_style"], "Bold")
    font_options += text_option("shadow", config["font_style"], "Shadow")
    motion_options = text_option("static", config["animation"], "Static")
    motion_options += text_option("scroll_left", config["animation"], "Scroll left")
    motion_options += text_option("scroll_right", config["animation"], "Scroll right")
    motion_options += text_option("bounce", config["animation"], "Bounce")
    in_options = text_option("none", config["animation_in"], "None")
    in_options += text_option("left", config["animation_in"], "Slide from left")
    in_options += text_option("right", config["animation_in"], "Slide from right")
    out_options = text_option("none", config["animation_out"], "None")
    out_options += text_option("left", config["animation_out"], "Slide to left")
    out_options += text_option("right", config["animation_out"], "Slide to right")
    panel_options = option(3, config["panel_count"], "3 panels (96x16)")
    panel_options += option(6, config["panel_count"], "6 panels (192x16)")
    panel_options += option(9, config["panel_count"], "9 panels (288x16)")
    panel_options += option(
        12, config["panel_count"], "12 panels (6x2 serpentine, 192x32)"
    )
    notice = "<p class='ok'>{}</p>".format(message) if message else ""
    return """<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>RGB Ethernet Text</title><style>
body{{font-family:sans-serif;max-width:520px;margin:30px auto;padding:0 16px;background:#111;color:#eee}}
fieldset{{border:1px solid #555;margin:16px 0;padding:14px}}label{{display:block;margin:10px 0}}
input,select,button{{font:inherit;padding:7px}}input[type=text],select{{width:100%;box-sizing:border-box}}
input[type=color]{{width:80px;height:42px;padding:2px;vertical-align:middle}}
button{{background:#19a974;color:white;border:0;padding:10px 18px}}.ok{{color:#5fdb9a}}.muted{{color:#aaa}}
</style></head><body><h1>UDP Text Display</h1>
<p>IP: <b>{ip}</b> &middot; {width}x{height} &middot; UDP {udp_port}</p>{notice}
<form action='/message' method='get'><fieldset><legend>Send message</legend>
<label>Text<input name='text' type='text' maxlength='80' required
 placeholder='Message to display'></label>
<button type='submit'>Send now</button></fieldset></form>
<fieldset><legend>Persistent RGB bitmap</legend>
<input id='imageFile' type='file' accept='image/*'>
<button type='button' onclick='uploadBitmap()'>Upload and display</button>
<button type='button' onclick='location.href="/show-bitmap"'>Show saved</button>
<button type='button' onclick='location.href="/delete-bitmap"'>Delete</button>
<p id='uploadStatus' class='muted'>Images are resized to {width}x{height} RGB565.</p>
<canvas id='bitmapCanvas' width='{width}' height='{height}' hidden></canvas>
</fieldset>
<form action='/save' method='get'><fieldset><legend>Text</legend>
<label>Panel count<select name='panels'>{panel_options}</select></label>
<label>Color <input name='color' type='color' value='#{color}'></label>
<label>Size<select name='size'>{size_options}</select></label>
<label>Panel color order<select name='order'>
<option value='RGB'{rgb_selected}>RGB</option>
<option value='GRB'{grb_selected}>GRB</option></select></label>
<label>Font<select name='font'>{font_options}</select></label>
<label>Continuous animation<select name='animation'>{motion_options}</select></label>
<label>Entrance<select name='animation_in'>{in_options}</select></label>
<label>Exit on next message<select name='animation_out'>{out_options}</select></label>
<label>Speed: <output id='speedValue'>{speed}</output> px/s
<input name='speed' type='range' min='5' max='120' step='5' value='{speed}'
 oninput='speedValue.value=this.value'></label>
<p class='muted'>Built-in monospace font. Long messages are clipped to panel width.</p>
</fieldset><fieldset><legend>Network</legend>
<label><input type='radio' name='network' value='dhcp'{dhcp}> DHCP</label>
<label><input type='radio' name='network' value='static'{static}> Static IP</label>
<label>IP<input name='ip' type='text' value='{cfg_ip}'></label>
<label>Mask<input name='mask' type='text' value='{mask}'></label>
<label>Gateway<input name='gateway' type='text' value='{gateway}'></label>
<label>DNS<input name='dns' type='text' value='{dns}'></label></fieldset>
<button type='submit'>Save and restart</button></form>
<script>
async function uploadBitmap(){{
 const file=document.getElementById('imageFile').files[0];
 const status=document.getElementById('uploadStatus');
 if(!file){{status.textContent='Choose an image first.';return;}}
 const img=new Image(); img.src=URL.createObjectURL(file); await img.decode();
 const canvas=document.getElementById('bitmapCanvas');
 const ctx=canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height);
 ctx.drawImage(img,0,0,canvas.width,canvas.height);
 const rgba=ctx.getImageData(0,0,canvas.width,canvas.height).data;
 const packed=new Uint8Array(canvas.width*canvas.height*2);
 for(let p=0,i=0;p<rgba.length;p+=4,i+=2){{
   const rgb565=((rgba[p]&248)<<8)|((rgba[p+1]&252)<<3)|(rgba[p+2]>>3);
   packed[i]=rgb565>>8; packed[i+1]=rgb565&255;
 }}
 status.textContent='Uploading...';
 const response=await fetch('/bitmap',{{method:'POST',headers:{{'Content-Type':'application/octet-stream'}},body:packed}});
 status.textContent=await response.text();
 URL.revokeObjectURL(img.src);
}}
</script></body></html>""".format(
        ip=ip_address, width=matrix_width, height=matrix_height,
        udp_port=TEXT_UDP_PORT, notice=notice,
        color=config["text_color"], size_options=size_options,
        rgb_selected=rgb_selected, grb_selected=grb_selected,
        font_options=font_options, motion_options=motion_options,
        in_options=in_options, out_options=out_options, speed=config["speed"],
        panel_options=panel_options,
        dhcp=dhcp_checked, static=static_checked,
        cfg_ip=config["ip"], mask=config["mask"],
        gateway=config["gateway"], dns=config["dns"],
    )


def send_http(client, status, body):
    body_bytes = body.encode("utf-8")
    header = (
        "HTTP/1.1 {}\r\nContent-Type: text/html; charset=utf-8\r\n"
        "Content-Length: {}\r\nConnection: close\r\n\r\n"
    ).format(status, len(body_bytes)).encode("utf-8")
    response = header + body_bytes
    for offset in range(0, len(response), 512):
        chunk = response[offset:offset + 512]
        sent = 0
        while sent < len(chunk):
            count = client.send(chunk[sent:])
            if not count:
                raise RuntimeError("HTTP client stopped receiving")
            sent += count


def poll_http():
    global config
    try:
        client, remote = http_server.accept()
    except Exception:
        return False
    should_reload = False
    try:
        client.settimeout(2.0)
        request = bytearray()
        while b"\r\n\r\n" not in request and len(request) < 2048:
            chunk = client.recv(512)
            if not chunk:
                break
            request.extend(chunk)
        header_end = request.find(b"\r\n\r\n")
        if header_end < 0:
            raise ValueError("Incomplete HTTP header")
        header = bytes(request[:header_end]).decode("utf-8")
        body_start = bytes(request[header_end + 4:])
        header_lines = header.split("\r\n")
        first_line = header_lines[0]
        parts = first_line.split(" ")
        method = parts[0] if parts else "GET"
        target = parts[1] if len(parts) >= 2 else "/"
        headers = {}
        for line in header_lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        if method == "POST" and target == "/bitmap":
            expected = matrix_width * matrix_height * 2
            content_length = int(headers.get("content-length", "0"))
            if content_length != expected:
                send_http(client, "400 Bad Request",
                          "Expected {} RGB565 bytes".format(expected))
            else:
                received = 0
                with open(BITMAP_TEMP_PATH, "wb") as bitmap_file:
                    bitmap_file.write(BITMAP_MAGIC)
                    bitmap_file.write(bytes((matrix_width >> 8, matrix_width & 0xFF)))
                    bitmap_file.write(bytes((matrix_height >> 8,
                                             matrix_height & 0xFF)))
                    initial = body_start[:expected]
                    bitmap_file.write(initial)
                    received = len(initial)
                    while received < expected:
                        chunk = client.recv(min(512, expected - received))
                        if not chunk:
                            raise ValueError("Incomplete bitmap upload")
                        bitmap_file.write(chunk)
                        received += len(chunk)
                try:
                    os.remove(BITMAP_PATH)
                except OSError:
                    pass
                os.rename(BITMAP_TEMP_PATH, BITMAP_PATH)
                show_saved_bitmap()
                send_http(client, "200 OK", "Bitmap saved and displayed.")
        elif target == "/show-bitmap":
            message = "Saved bitmap displayed." if show_saved_bitmap() else "No compatible bitmap saved."
            send_http(client, "200 OK", web_page(message))
        elif target == "/delete-bitmap":
            try:
                os.remove(BITMAP_PATH)
                message = "Saved bitmap deleted."
            except OSError:
                message = "No saved bitmap found."
            build_text(last_message)
            send_http(client, "200 OK", web_page(message))
        elif target.startswith("/message?"):
            values = parse_query(target.split("?", 1)[1])
            message = values.get("text", "")[:80]
            if message.strip():
                show_text(message)
                send_http(client, "200 OK", web_page("Message sent."))
            else:
                send_http(client, "400 Bad Request", web_page("Message is empty."))
        elif target.startswith("/save?"):
            values = parse_query(target.split("?", 1)[1])
            new_config = validate_config({
                "panel_count": values.get("panels", config["panel_count"]),
                "text_color": values.get("color", config["text_color"]),
                "text_size": values.get("size", config["text_size"]),
                "color_order": values.get("order", config["color_order"]),
                "font_style": values.get("font", config["font_style"]),
                "animation": values.get("animation", config["animation"]),
                "animation_in": values.get("animation_in", config["animation_in"]),
                "animation_out": values.get("animation_out", config["animation_out"]),
                "speed": values.get("speed", config["speed"]),
                "dhcp": values.get("network", "dhcp") == "dhcp",
                "ip": values.get("ip", config["ip"]),
                "mask": values.get("mask", config["mask"]),
                "gateway": values.get("gateway", config["gateway"]),
                "dns": values.get("dns", config["dns"]),
            })
            network_keys = ("dhcp", "ip", "mask", "gateway", "dns")
            network_changed = any(
                new_config[key] != config[key] for key in network_keys
            )
            panel_changed = new_config["panel_count"] != config["panel_count"]
            save_config(new_config)
            config = new_config
            if network_changed or panel_changed:
                send_http(client, "200 OK", web_page("Saved. Restarting display..."))
                should_reload = True
            else:
                build_text(last_message)
                send_http(client, "200 OK", web_page("Applied and saved."))
        elif target == "/" or target.startswith("/?"):
            send_http(client, "200 OK", web_page())
        else:
            send_http(client, "404 Not Found", "<h1>404</h1>")
    except Exception as error:
        print("HTTP error:", error)
    finally:
        client.close()
    return should_reload


print("RGB Ethernet UDP text ready")
print("IP:", ip_address, "Web: http://{}/".format(ip_address))
print("Panels:", panel_count, "Layout:", panel_columns, "x", panel_rows,
      "Canvas:", matrix_width, "x", matrix_height,
      "Serpentine:", panel_rows > 1)
print("UDP text port:", TEXT_UDP_PORT)
print("Color: #" + config["text_color"], "Size:", config["text_size"],
      "Order:", config["color_order"])
print("Font:", config["font_style"], "Animation:", config["animation"],
      "Speed:", config["speed"], "px/s")

if config["dhcp"]:
    show_saved_bitmap()

last_http_poll = 0.0
last_dhcp_maintenance = time.monotonic()
while True:
    poll_text_udp()
    update_animation()
    now = time.monotonic()
    if now - last_http_poll >= HTTP_POLL_INTERVAL:
        last_http_poll = now
        if poll_http():
            time.sleep(0.5)
            supervisor.reload()
    if config["dhcp"] and now - last_dhcp_maintenance >= 60:
        try:
            ethernet.maintain_dhcp_lease()
        except Exception as error:
            print("DHCP maintenance error:", error)
        last_dhcp_maintenance = now
    time.sleep(0.001)
