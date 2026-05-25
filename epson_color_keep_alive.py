#!/usr/bin/env python3
"""
epson_color_keep_alive.py
─────────────────────────
Prints a partial-page CMYK ink exercise pattern to an Epson WF-7720
via HTTPS/IPP.  Uses only ~3 inches of paper — enough to keep all
4 ink channels flowing without wasting a full sheet.

Requires:  pip install Pillow

Usage:
    python3 epson_color_keep_alive.py
"""

import io
import ssl
import sys
import struct
import socket
import logging
import datetime

from PIL import Image, ImageDraw

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
PRINTER_IP       = "192.168.1.129"
PRINTER_PORT     = 631
PRINTER_IPP_PATH = "/ipp/print"
PRINTER_URI      = "ipps://192.168.1.xxx:631/ipp/print"
JOB_NAME         = "ColorKeepAlive"
LOG_FILE         = "/share/homes/xxxx/epson_keep_alive.log"
DPI              = 150    # 150dpi — low res is fine for nozzle exercise
PAGE_W_IN        = 8.5
PAGE_H_IN        = 3.0    # Only 3 inches tall — saves paper
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── 1. GENERATE CMYK EXERCISE JPEG ─────────────────────────────────────────────

def build_color_test_jpeg() -> bytes:
    W = int(PAGE_W_IN * DPI)   # 1275 px
    H = int(PAGE_H_IN * DPI)   #  450 px

    img  = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    now  = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M")

    # ── Title bar ──────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 36], fill=(30, 30, 30))
    draw.text((W // 2, 18),
              f"Epson WF-7720 Ink Keep-Alive  ·  {now}",
              fill=(255, 255, 255), anchor="mm")

    # ── 4 CMYK solid bars ─────────────────────────────────────────────────
    # These are the actual 4 Epson ink channels rendered in RGB:
    #   Cyan    = (0, 183, 235)
    #   Magenta = (236, 0, 140)
    #   Yellow  = (255, 240, 0)
    #   Black   = (0, 0, 0)
    cmyk = [
        ((0,   183, 235), "CYAN"),
        ((236, 0,   140), "MAGENTA"),
        ((255, 240, 0),   "YELLOW"),
        ((0,   0,   0),   "BLACK"),
    ]
    bar_top = 44
    bar_h   = 100
    bw      = W // 4

    for i, (col, lbl) in enumerate(cmyk):
        x0 = i * bw
        draw.rectangle([x0, bar_top, x0 + bw - 2, bar_top + bar_h], fill=col)
        brightness = 0.299*col[0] + 0.587*col[1] + 0.114*col[2]
        txt = (0, 0, 0) if brightness > 140 else (255, 255, 255)
        draw.text((x0 + bw // 2, bar_top + bar_h // 2),
                  lbl, fill=txt, anchor="mm")

    # ── Gradient strips — one per ink channel ─────────────────────────────
    strip_top = bar_top + bar_h + 12
    strip_h   = 55
    steps     = 40
    sw        = W // steps

    gradients = [
        ((0, 183, 235), "C →"),
        ((236, 0, 140), "M →"),
        ((255, 240, 0), "Y →"),
        ((20,  20,  20), "K →"),
    ]
    for row, (col, lbl) in enumerate(gradients):
        y0 = strip_top + row * (strip_h + 6)
        draw.text((4, y0 + strip_h // 2), lbl, fill=(0,0,0), anchor="lm")
        for step in range(steps):
            t  = step / (steps - 1)
            # Fade from white to full ink color
            r  = int(255 + (col[0] - 255) * t)
            g  = int(255 + (col[1] - 255) * t)
            b  = int(255 + (col[2] - 255) * t)
            x0 = 36 + step * sw
            draw.rectangle([x0, y0, x0 + sw, y0 + strip_h], fill=(r, g, b))

    # ── Fine nozzle grid — all 4 colors interleaved ───────────────────────
    grid_top = strip_top + len(gradients) * (strip_h + 6) + 10
    grid_h   = H - grid_top - 28
    if grid_h > 10:
        cols_g  = 60
        rows_g  = max(1, grid_h // 16)
        gw      = W // cols_g
        gh      = grid_h // rows_g
        palette = [
            (0, 183, 235),    # C
            (236, 0, 140),    # M
            (255, 240, 0),    # Y
            (20,  20,  20),   # K
        ]
        for r in range(rows_g):
            for c in range(cols_g):
                col = palette[(r + c) % 4]
                draw.rectangle(
                    [c * gw, grid_top + r * gh,
                     c * gw + gw - 1, grid_top + r * gh + gh - 1],
                    fill=col)

    # ── Footer ────────────────────────────────────────────────────────────
    draw.rectangle([0, H - 22, W, H], fill=(30, 30, 30))
    draw.text((W // 2, H - 11),
              "Auto-printed by epson_color_keep_alive.py",
              fill=(180, 180, 180), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, dpi=(DPI, DPI))
    return buf.getvalue()


# ── 2. SEND JPEG VIA HTTPS/IPP ─────────────────────────────────────────────────

def _ipp_attr(tag: int, name: str, value) -> bytes:
    name_b = name.encode()
    val_b  = value.encode() if isinstance(value, str) else value
    return (struct.pack(">B", tag) +
            struct.pack(">H", len(name_b)) + name_b +
            struct.pack(">H", len(val_b))  + val_b)


def build_ipp_request(jpeg_bytes: bytes) -> bytes:
    ipp  = struct.pack(">BB", 0x01, 0x01)   # IPP 1.1
    ipp += struct.pack(">H", 0x0002)         # Print-Job
    ipp += struct.pack(">i", 1)
    ipp += b'\x01'                           # operation-attributes
    ipp += _ipp_attr(0x47, "attributes-charset",         "utf-8")
    ipp += _ipp_attr(0x48, "attributes-natural-language", "en-us")
    ipp += _ipp_attr(0x45, "printer-uri",                 PRINTER_URI)
    ipp += _ipp_attr(0x42, "requesting-user-name",         "qnap")
    ipp += _ipp_attr(0x42, "job-name",                     JOB_NAME)
    ipp += _ipp_attr(0x49, "document-format",              "image/jpeg")
    ipp += b'\x03'                           # end-of-attributes
    ipp += jpeg_bytes

    http = (
        f"POST {PRINTER_IPP_PATH} HTTP/1.1\r\n"
        f"Host: {PRINTER_IP}:{PRINTER_PORT}\r\n"
        f"Content-Type: application/ipp\r\n"
        f"Content-Length: {len(ipp)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode() + ipp
    return http


def send_to_printer(jpeg_bytes: bytes) -> bool:
    log.info("Sending %d-byte JPEG to %s", len(jpeg_bytes), PRINTER_URI)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    try:
        with socket.create_connection((PRINTER_IP, PRINTER_PORT), timeout=60) as raw:
            with ctx.wrap_socket(raw) as sock:
                sock.sendall(build_ipp_request(jpeg_bytes))
                response = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk

        first_line = response.split(b"\r\n")[0].decode(errors="replace")
        he         = response.find(b"\r\n\r\n")
        ipp_body   = response[he + 4:] if he != -1 else b""
        ipp_status = struct.unpack(">H", ipp_body[2:4])[0] if len(ipp_body) >= 4 else 0xFFFF

        if "200" in first_line and ipp_status == 0x0000:
            log.info("✓ Print job accepted  (IPP 0x%04X)", ipp_status)
            return True
        else:
            log.error("Print job failed — HTTP: %s  IPP: 0x%04X",
                      first_line.strip(), ipp_status)
            return False

    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        log.error("Network error: %s", exc)
        return False


# ── 3. WAKE PRINTER ────────────────────────────────────────────────────────────

WAKE_TIMEOUT   = 90    # seconds to wait for printer to wake up
WAKE_INTERVAL  = 5     # seconds between ping attempts

def wake_printer() -> bool:
    """
    Ping the printer on port 631 until it responds, then wait a few extra
    seconds for the print head to fully initialise.  Returns True if the
    printer became reachable within WAKE_TIMEOUT seconds.
    """
    import time
    deadline = time.monotonic() + WAKE_TIMEOUT
    attempt  = 0

    log.info("Waking printer at %s …", PRINTER_IP)
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with socket.create_connection((PRINTER_IP, PRINTER_PORT), timeout=3):
                if attempt == 1:
                    log.info("Printer already awake")
                else:
                    log.info("Printer responded after %d ping(s) — "
                             "waiting 30 s for warm-up …", attempt)
                    time.sleep(30)   # give the head time to initialise
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            log.info("  ping %d — no response, retrying in %ds …",
                     attempt, WAKE_INTERVAL)
            time.sleep(WAKE_INTERVAL)

    log.error("Printer did not respond within %d seconds — aborting.", WAKE_TIMEOUT)
    return False


# ── 4. MAIN ────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Epson WF-7720 color keep-alive starting ===")

    if not wake_printer():
        sys.exit(4)

    try:
        log.info("Generating CMYK exercise pattern (%.1f\" × %.1f\" @ %ddpi) …",
                 PAGE_W_IN, PAGE_H_IN, DPI)
        jpeg = build_color_test_jpeg()
        log.info("JPEG generated (%d bytes)", len(jpeg))
    except Exception as exc:
        log.exception("JPEG generation failed: %s", exc)
        sys.exit(2)

    success = send_to_printer(jpeg)
    sys.exit(0 if success else 3)


if __name__ == "__main__":
    main()
