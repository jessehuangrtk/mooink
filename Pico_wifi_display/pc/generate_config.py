"""Create a private C header for the independent read-only SMB probe."""
import argparse
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def field(obj, name, limit, minimum=1):
    value = obj.get(name)
    if not isinstance(value, str):
        raise ValueError(name + ": expected string")
    data = value.encode("utf-8")
    if not minimum <= len(data) <= limit or any(b < 32 or b == 127 for b in data):
        raise ValueError(name + ": invalid byte length or control character")
    return data

def literal(data):
    # Three-digit octal escapes prevent C injection and ambiguous hex escapes.
    return '"' + ''.join('\\%03o' % b for b in data) + '"'

def generate(config):
    wifi, smb = config["wifi"], config["smb"]
    if smb.get("require_signing") is not True:
        raise ValueError("SMB signing must be required")
    if smb.get("file") != "mooink_display.png":
        raise ValueError("Only mooink_display.png is supported")
    interval = config.get("poll_seconds")
    if type(interval) is not int or not 5 <= interval <= 86400:
        raise ValueError("poll_seconds: 5..86400")
    size = config.get("display_size")
    if size not in ("10.3", "13.3"):
        raise ValueError("display_size: 10.3 or 13.3")
    fields = {
        "WIFI_SSID": field(wifi,"ssid",32),
        "WIFI_PASSWORD": field(wifi,"password",63,8),
        "SMB_SERVER": field(smb,"server",127),
        "SMB_SHARE": field(smb,"share",80),
        "SMB_USER": field(smb,"username",80),
        "SMB_PASSWORD": field(smb,"password",128),
        "SMB_DOMAIN": field(smb,"domain",80,0),
    }
    for key in ("SMB_SERVER","SMB_SHARE"):
        if any(ch in fields[key] for ch in (b"/",b"\\",b"@",b"?",b"#")):
            raise ValueError(key + ": use server/share name, not a URL")
    width,height = (1404,1872) if size=="10.3" else (1650,2200)
    lines = ["#pragma once", "/* PRIVATE: generated from config.local.json. */"]
    lines += ["#define DISPLAY_" + name + " " + literal(value) for name,value in fields.items()]
    lines += ["#define DISPLAY_POLL_SECONDS %du" % interval,
              "#define DISPLAY_WIDTH %du" % width,
              "#define DISPLAY_HEIGHT %du" % height,
              "#define DISPLAY_REQUIRE_SEAL %d" % int(smb.get("require_encryption") is True)]
    return "\n".join(lines)+"\n"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=ROOT/"config.local.json")
    args=parser.parse_args()
    dest=ROOT/"firmware"/"config.local.h"
    try:
        content=generate(json.loads(args.config.read_text(encoding="utf-8")))
        fd,tmp=tempfile.mkstemp(dir=dest.parent,prefix=".private_config_")
        try:
            with os.fdopen(fd,"w",encoding="ascii") as stream:
                stream.write(content)
            os.replace(tmp,dest)
        finally:
            Path(tmp).unlink(missing_ok=True)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise SystemExit("Invalid or missing private config; check config.example.json.")
    print("Private firmware config generated (credentials not printed).")

if __name__=="__main__":
    main()
