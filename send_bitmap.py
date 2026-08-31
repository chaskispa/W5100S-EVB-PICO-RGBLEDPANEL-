#!/usr/bin/env python3
"""Resize an image, convert it to RGB565, and upload it to RGB Ethernet."""

import argparse
import re
import urllib.request

from PIL import Image


def controller_size(host):
    with urllib.request.urlopen("http://{}/".format(host), timeout=5) as response:
        page = response.read().decode("utf-8")
    match = re.search(r"&middot;\s*(\d+)x(\d+)\s*&middot;", page)
    if not match:
        raise RuntimeError("Could not detect panel size from controller")
    return int(match.group(1)), int(match.group(2))


def rgb565_bytes(image):
    output = bytearray()
    for red, green, blue in image.getdata():
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output.extend((value >> 8, value & 0xFF))
    return bytes(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="PNG, JPEG, or another Pillow image")
    parser.add_argument("--host", default="192.168.100.245")
    args = parser.parse_args()

    width, height = controller_size(args.host)
    with Image.open(args.image) as source:
        image = source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    payload = rgb565_bytes(image)
    request = urllib.request.Request(
        "http://{}/bitmap".format(args.host),
        data=payload,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = response.read().decode("utf-8")
    print("{} ({}x{}, {} bytes)".format(result, width, height, len(payload)))


if __name__ == "__main__":
    main()
