"""Output names shared by verification and task recovery."""
import hashlib
import json
import re


def image_name(invoice):
    number = invoice["invoice_number"]
    reserved = re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", number, re.I)
    if (number and len(number.encode("utf-8")) <= 160 and not reserved
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', number) and not number.endswith((".", " "))):
        return f"{number}.png"
    # Arbitrary user identifiers must not become paths or exceed filename limits.
    identity = json.dumps(number, ensure_ascii=False).encode()
    return f"invoice-{hashlib.sha256(identity).hexdigest()}.png"
