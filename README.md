# Epson Inkjet Keep-Alive

Automatically prints a small CMYK color test pattern on a schedule to prevent
inkjet nozzles from drying out and clogging.

Designed to run on a **QNAP NAS** as a cron job, so it works silently in the
background without needing a PC to be on. Communicates directly with the
printer over **HTTPS/IPP** — no CUPS, no print drivers, no extra services
required.

Built and tested on a **WF-7720** but should work with any Epson (or other
IPP-capable) inkjet that supports `image/jpeg` as a print format.

---

## The problem

Inkjet printers left idle for weeks develop dried ink in the print head
nozzles. The fix — running the built-in cleaning cycle — wastes half a
cartridge and often needs repeating. Printing something small every few days
keeps the ink flowing and the nozzles clear, with far less waste than
reactive cleaning.

## The solution

A single Python script runs on a schedule and prints a 3-inch partial page
that exercises all four ink channels (Cyan, Magenta, Yellow, Black):

- **Solid color bars** — full saturation hit on each ink channel
- **Gradient strips** — each channel from white to full ink
- **Fine nozzle grid** — C/M/Y/K interleaved at small scale
- **Partial page** — only 3" tall, uses roughly ⅓ of a sheet

---

## Requirements

- Python 3.8+
- [Pillow](https://pillow.readthedocs.io/) (`pip install Pillow`)
- An IPP-capable Epson printer on your local network
- An always-on device to run the cron job (QNAP NAS, Raspberry Pi, home server, etc.)

---

## Setup

### 1. Find your printer's IP address

On the printer touchscreen: **Settings → Network Settings → Wi-Fi/LAN → IP Address**

Assign it a static IP (or DHCP reservation) in your router so it never changes.

### 2. Find the IPP path

The script needs to know the printer's exact IPP endpoint. You can discover it
by sending a `Get-Printer-Attributes` probe:

```sh
python3 - << 'EOF'
import socket, ssl, struct, re

IP, PORT = "192.168.1.XXX", 631

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def attr(tag, name, value):
    nb, vb = name.encode(), value.encode()
    return struct.pack(">B", tag) + struct.pack(">H", len(nb)) + nb + struct.pack(">H", len(vb)) + vb

ipp  = struct.pack(">BB", 0x01, 0x01) + struct.pack(">H", 0x000b) + struct.pack(">i", 1)
ipp += b'\x01'
ipp += attr(0x47, "attributes-charset", "utf-8")
ipp += attr(0x48, "attributes-natural-language", "en-us")
ipp += attr(0x45, "printer-uri", f"ipps://{IP}:{PORT}/")
ipp += attr(0x44, "requested-attributes", "printer-uri-supported")
ipp += attr(0x44, "requested-attributes", "document-format-supported")
ipp += b'\x03'

http = (f"POST / HTTP/1.1\r\nHost: {IP}:{PORT}\r\nContent-Type: application/ipp\r\n"
        f"Content-Length: {len(ipp)}\r\nConnection: close\r\n\r\n").encode() + ipp

with socket.create_connection((IP, PORT), timeout=10) as raw:
    with ctx.wrap_socket(raw) as s:
        s.sendall(http)
        resp = b""
        while chunk := s.recv(4096): resp += chunk

he = resp.find(b"\r\n\r\n")
for m in re.finditer(b'[\x20-\x7e]{6,}', resp[he+4:]):
    print(m.group().decode())
EOF
```

Look for `printer-uri-supported` and `document-format-supported` in the output.
For the WF-7720 the URI is `ipps://192.168.1.X:631/ipp/print` and the printer
accepts `image/jpeg`.

> **Tip:** IPP only responds to POST requests, not GET. Standard `curl` checks
> will return 404 even on valid paths — use the probe script above instead.

### 3. Configure the script

Edit the `CONFIGURATION` block at the top of `epson_color_keep_alive.py`:

```python
PRINTER_IP       = "192.168.1.XXX"          # ← Your printer's IP
PRINTER_PORT     = 631
PRINTER_IPP_PATH = "/ipp/print"              # ← From the probe above
PRINTER_URI      = "ipps://192.168.1.XXX:631/ipp/print"
LOG_FILE         = "/share/homes/[user name]/epson_keep_alive.log"
PAGE_H_IN        = 3.0                       # Partial page height in inches
```

### 4. Install Pillow

```sh
pip install Pillow

# On QNAP with a non-standard Python install:
/path/to/python3 -m pip install Pillow
```

### 5. Test manually

```sh
python3 epson_color_keep_alive.py
```

You should see:
```
2026-05-25 13:17:29  INFO  === Epson WF-7720 color keep-alive starting ===
2026-05-25 13:17:29  INFO  Generating CMYK exercise pattern (8.5" × 3.0" @ 150dpi) …
2026-05-25 13:17:29  INFO  JPEG generated (82113 bytes)
2026-05-25 13:17:29  INFO  Sending 82113-byte JPEG to ipps://192.168.1.129:631/ipp/print
2026-05-25 13:17:45  INFO  ✓ Print job accepted  (IPP 0x0000)
```

And a 3-inch CMYK test strip should emerge from the printer.

### 6. Schedule with cron

#### QNAP

QNAP's persistent cron file is `/etc/config/crontab`. Add a line and reload:

```sh
echo "0 8 1,6,11,16,21,26 * * /full/path/to/python3 /share/homes/nasadmin/epson_color_keep_alive.py" \
  >> /etc/config/crontab
crontab /etc/config/crontab
crontab -l   # verify
```

This fires on days 1, 6, 11, 16, 21, 26 of each month at **08:00 UTC
(3:00 AM EST)** — six times a month, roughly every 5 days.

> **Note:** On QNAP, always use `/etc/config/crontab` (not `/etc/crontab`).
> The config version survives reboots; the other does not.

#### Standard Linux

```sh
crontab -e
# Add:
0 8 1,6,11,16,21,26 * * python3 /path/to/epson_color_keep_alive.py
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `TimeoutError` | Printer is in deep sleep | Ping it first; raise the sleep timer on the printer |
| `IPP 0x0400` | Wrong `printer-uri` or path | Run the probe script in Step 2; URI must match exactly |
| `IPP 0x0503` | Printer busy or out of paper/ink | Check printer status |
| `ConnectionRefusedError` | Port 631 not reachable | Ensure IPP/AirPrint is enabled in printer network settings |
| Job accepted but nothing prints | `printer-uri` mismatch | The URI in the script must match what the printer self-reports |
| `ModuleNotFoundError: PIL` | Pillow not installed | `pip install Pillow` |
| Python not found on QNAP | Non-standard install path | `find / -name "python3*" -type f 2>/dev/null` to locate it |

### Checking the log

```sh
tail -f /share/homes/nasadmin/epson_keep_alive.log
```

### Printer sleep mode

The WF-7720 has an aggressive sleep timer. If the cron job fires while the
printer is asleep it may time out before the job is accepted. Two options:

1. Set **Settings → Printer Settings → Sleep Timer** to 60 minutes or more on
   the printer touchscreen
2. Send a wake ping before printing — the printer wakes on network activity
   within a few seconds

---

## How it works

Standard Linux printing relies on **CUPS** to convert documents into a raster
format the printer understands, then delivers them over IPP. Most Epson
inkjets do **not** accept PDF or PostScript directly — they expect
`image/jpeg`, `image/pwg-raster`, or `image/urf` (Apple Raster).

This script skips CUPS entirely:

1. **Pillow** draws the CMYK test pattern directly as a JPEG in memory
2. It's wrapped in a minimal **IPP/1.1 Print-Job** request (pure Python,
   no libraries)
3. Sent over a raw **SSL socket** on port 631

The printer's self-signed certificate is accepted without verification —
standard practice for local network printers.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PRINTER_IP` | — | Printer's static IP address |
| `PRINTER_PORT` | `631` | IPP port (almost always 631) |
| `PRINTER_IPP_PATH` | `/ipp/print` | IPP endpoint path (from probe) |
| `PRINTER_URI` | — | Full `ipps://` URI — must match printer's self-reported value |
| `LOG_FILE` | — | Path to log file |
| `DPI` | `150` | Image resolution — 150dpi is plenty for a nozzle exercise |
| `PAGE_W_IN` | `8.5` | Page width in inches |
| `PAGE_H_IN` | `3.0` | Page height — keep small to save paper |

---

## License

MIT
