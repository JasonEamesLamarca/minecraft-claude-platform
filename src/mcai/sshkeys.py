"""OpenSSH public keys: strict parsing and SHA256 fingerprints (same format as `ssh-keygen -lf`)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass

# Key types we accept: modern and widely supported. DSA and short RSA keys are refused.
TYPES = {"ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa",
         "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"}
MIN_RSA_BITS = 3072


class KeyError_(ValueError):
    pass


@dataclass(frozen=True)
class PublicKey:
    type: str
    blob_b64: str
    comment: str
    fingerprint: str

    @property
    def line(self) -> str:
        """Type and key only: comments are kept separately and never written to authorized_keys."""
        return f"{self.type} {self.blob_b64}"


def fingerprint(blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _rsa_bits(blob: bytes) -> int:
    def field(off):
        (n,) = struct.unpack(">I", blob[off:off + 4])
        return blob[off + 4:off + 4 + n], off + 4 + n
    _, off = field(0)
    _, off = field(off)       # e
    mod, _ = field(off)
    return len(mod.lstrip(b"\x00")) * 8


def parse_public_key(text: str) -> PublicKey:
    """One public key line ("type base64 [comment]"). Options (command=, from=, ...) are rejected."""
    line = " ".join(str(text).strip().split())
    if "\n" in str(text).strip() or len(line) > 16384:
        raise KeyError_("give exactly one public key line")
    parts = line.split(" ", 2)
    if len(parts) < 2 or parts[0] not in TYPES:
        raise KeyError_("not an OpenSSH public key (expected e.g. 'ssh-ed25519 AAAA... comment')")
    ktype, b64 = parts[0], parts[1]
    comment = re.sub(r"[^\w@.+-]", "", parts[2])[:64] if len(parts) == 3 else ""
    try:
        blob = base64.b64decode(b64, validate=True)
        (n,) = struct.unpack(">I", blob[:4])
        inner = blob[4:4 + n].decode()
    except (binascii.Error, struct.error, UnicodeDecodeError):
        raise KeyError_("the key data is damaged") from None
    if inner != ktype:
        raise KeyError_("the key type doesn't match the key data")
    if ktype == "ssh-rsa":
        try:
            bits = _rsa_bits(blob)
        except struct.error:
            raise KeyError_("the key data is damaged") from None
        if bits < MIN_RSA_BITS:
            raise KeyError_(f"RSA keys need at least {MIN_RSA_BITS} bits; use ed25519 (ssh-keygen -t ed25519)")
    return PublicKey(ktype, b64, comment, fingerprint(blob))
