#!/usr/bin/env python3
"""Send an RGB565 bitmap to an RGB Ethernet UDP bitmap layout."""

import argparse
import socket
import time

from PIL import Image


DEFAULT_WIDTH = 96
DEFAULT_HEIGHT = 96
PORT = 5001
MAGIC = b"RGBU"
CHUNK_SIZE = 1024


def rgb565_bytes(image):
    output = bytearray()
    for red, green, blue in image.getdata():
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output.extend((value >> 8, value & 0xFF))
    return bytes(output)


def frame_packets(payload, frame_id, chunk_count):
    for chunk_index in range(chunk_count):
        offset = chunk_index * CHUNK_SIZE
        header = MAGIC + bytes((
            frame_id >> 8,
            frame_id & 0xFF,
            chunk_index,
            chunk_count,
        ))
        yield header + payload[offset:offset + CHUNK_SIZE]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="PNG, JPEG, or another Pillow image")
    parser.add_argument("--host", required=True, help="Display IPv4 address")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                        help="Logical frame width (16 for the 3-panel column)")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT,
                        help="Logical frame height (96 for bitmap modes)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Complete-frame transmission attempts")
    parser.add_argument("--delay", type=float, default=0.25,
                        help="Seconds between UDP chunks")
    args = parser.parse_args()

    if args.width <= 0 or args.height <= 0:
        parser.error("width and height must be positive")
    frame_bytes = args.width * args.height * 2
    chunk_count = (frame_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE
    if chunk_count > 255:
        parser.error("frame requires more than 255 UDP chunks")

    with Image.open(args.image) as source:
        image = source.convert("RGB").resize(
            (args.width, args.height), Image.Resampling.LANCZOS
        )
    payload = rgb565_bytes(image)
    if len(payload) != frame_bytes:
        raise RuntimeError("Unexpected RGB565 payload size")
    frame_id = int(time.monotonic() * 1000) & 0xFFFF
    expected_ack = MAGIC + bytes((frame_id >> 8, frame_id & 0xFF)) + b"OK"

    acknowledged = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(1.0)
        for _ in range(max(1, args.retries)):
            for packet in frame_packets(payload, frame_id, chunk_count):
                udp.sendto(packet, (args.host, args.port))
                time.sleep(max(0, args.delay))
            try:
                response, _ = udp.recvfrom(32)
                if response == expected_ack:
                    acknowledged = True
                    break
            except socket.timeout:
                pass

    if not acknowledged:
        raise RuntimeError("Display did not acknowledge the complete bitmap")
    print("Sent {}x{} RGB565 frame {} ({} bytes in {} chunks)".format(
        args.width, args.height, frame_id, len(payload), chunk_count
    ))


if __name__ == "__main__":
    main()
