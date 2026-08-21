"""Minimal MPQ v1 archive writer (uncompressed, single-unit files) + self-verifying reader.

Builds a WoW 3.3.5a-compatible patch MPQ from a directory tree.
"""
import os
import re
import struct
import sys
import tempfile

# Blizzard-style storage: zlib-compressed sectors with a sector offset table,
# matching the layout of the client's own patch MPQs (flags 0x80000200).
import zlib

MPQ_FILE_EXISTS = 0x80000000
MPQ_FILE_COMPRESS = 0x00000200
FLAGS = MPQ_FILE_EXISTS | MPQ_FILE_COMPRESS

SECTOR_SHIFT = 3
SECTOR_SIZE = 512 << SECTOR_SHIFT  # 4096

# Every archive emitted by this writer carries a private ownership record.
# The exact contents matter: merely knowing the filename is not proof that an
# existing patch archive belongs to Paragon.
OWNER_MARKER_NAME = "ParagonAnniversary\\owner.txt"
OWNER_MARKER_CONTENT = b"Paragon-Anniversary client patch v1\n"


class UnsafeArchiveError(RuntimeError):
    """Raised when an output path exists but is not demonstrably ours."""


def validate_patch_name(name, locale=False):
    """Validate a WoW 3.3.5 one-character patch archive basename."""
    pattern = r"patch-enUS-[0-9A-Z]\.MPQ" if locale else r"patch-[0-9A-Z]\.MPQ"
    if (not name or os.path.basename(name) != name
            or re.fullmatch(pattern, name, re.IGNORECASE) is None):
        expected = "patch-enUS-?.MPQ" if locale else "patch-?.MPQ"
        raise ValueError("archive name must match %s (exactly one letter or digit)" % expected)
    return name


def pack_file(data):
    """Return the on-disk block for one file: sector offset table + sectors."""
    n_sectors = max(1, (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
    sectors = []
    for i in range(n_sectors):
        plain = data[i * SECTOR_SIZE:(i + 1) * SECTOR_SIZE]
        comp = zlib.compress(plain)
        if len(comp) + 1 < len(plain):
            sectors.append(b"\x02" + comp)  # 0x02 = zlib compression type
        else:
            sectors.append(plain)  # stored raw when compression does not help
    table_len = 4 * (n_sectors + 1)
    offsets = [table_len]
    for s in sectors:
        offsets.append(offsets[-1] + len(s))
    return struct.pack("<%dI" % (n_sectors + 1), *offsets) + b"".join(sectors)


def unpack_file(block, fsize):
    """Inverse of pack_file, for verification."""
    n_sectors = max(1, (fsize + SECTOR_SIZE - 1) // SECTOR_SIZE)
    offsets = struct.unpack("<%dI" % (n_sectors + 1), block[:4 * (n_sectors + 1)])
    out = b""
    for i in range(n_sectors):
        sector = block[offsets[i]:offsets[i + 1]]
        expected = min(SECTOR_SIZE, fsize - i * SECTOR_SIZE)
        if len(sector) < expected:
            if sector[:1] != b"\x02":
                raise ValueError("unknown compression type %r" % sector[:1])
            sector = zlib.decompress(sector[1:])
        out += sector
    return out

HASH_TABLE_OFFSET = 0
HASH_NAME_A = 1
HASH_NAME_B = 2
HASH_FILE_KEY = 3


def make_crypt_table():
    table = [0] * 0x500
    seed = 0x00100001
    for index1 in range(0x100):
        index2 = index1
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            temp1 = (seed & 0xFFFF) << 0x10
            seed = (seed * 125 + 3) % 0x2AAAAB
            temp2 = seed & 0xFFFF
            table[index2] = temp1 | temp2
            index2 += 0x100
    return table


CRYPT = make_crypt_table()


def hash_string(s, hash_type):
    seed1 = 0x7FED7FED
    seed2 = 0xEEEEEEEE
    for ch in s.upper().replace("/", "\\"):
        ch = ord(ch)
        seed1 = (CRYPT[(hash_type << 8) + ch] ^ (seed1 + seed2)) & 0xFFFFFFFF
        seed2 = (ch + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def encrypt_block(data, key):
    """data: bytes whose length is a multiple of 4"""
    seed = 0xEEEEEEEE
    out = []
    for (val,) in struct.iter_unpack("<I", data):
        seed = (seed + CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        out.append(val ^ ((key + seed) & 0xFFFFFFFF))
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (val + seed + (seed << 5) + 3) & 0xFFFFFFFF
    return struct.pack("<%dI" % len(out), *out)


def decrypt_block(data, key):
    seed = 0xEEEEEEEE
    out = []
    for (val,) in struct.iter_unpack("<I", data):
        seed = (seed + CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        plain = val ^ ((key + seed) & 0xFFFFFFFF)
        out.append(plain)
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (plain + seed + (seed << 5) + 3) & 0xFFFFFFFF
    return struct.pack("<%dI" % len(out), *out)


def read_file(path, name):
    """Read one known file from an MPQ v1 archive, without a listfile."""
    with open(path, "rb") as f:
        raw = f.read()

    base = None
    header = None
    for offset in range(0, min(len(raw), 0x100000), 512):
        if raw[offset:offset + 4] == b"MPQ\x1a":
            if offset + 32 > len(raw):
                break
            header = struct.unpack_from("<4sIIHHIIII", raw, offset)
            base = offset
            break
    if header is None:
        raise ValueError("no MPQ header found")

    _magic, _hsize, _asize, version, _bshift, hpos, bpos, hcount, bcount = header
    if version != 0 or hcount == 0 or hcount & (hcount - 1):
        raise ValueError("unsupported MPQ layout")
    hash_start = base + hpos
    block_start = base + bpos
    hash_end = hash_start + hcount * 16
    block_end = block_start + bcount * 16
    if hash_end > len(raw) or block_end > len(raw):
        raise ValueError("truncated MPQ tables")

    hash_blob = decrypt_block(
        raw[hash_start:hash_end], hash_string("(hash table)", HASH_FILE_KEY))
    block_blob = decrypt_block(
        raw[block_start:block_end], hash_string("(block table)", HASH_FILE_KEY))
    hash_table = [struct.unpack_from("<4I", hash_blob, i * 16)
                  for i in range(hcount)]

    slot = hash_string(name, HASH_TABLE_OFFSET) & (hcount - 1)
    want_a = hash_string(name, HASH_NAME_A)
    want_b = hash_string(name, HASH_NAME_B)
    block_index = None
    idx = slot
    while True:
        name_a, name_b, _locale, candidate = hash_table[idx]
        if candidate == 0xFFFFFFFF:
            break
        if name_a == want_a and name_b == want_b:
            block_index = candidate
            break
        idx = (idx + 1) & (hcount - 1)
        if idx == slot:
            break
    if block_index is None:
        return None
    if block_index >= bcount:
        raise ValueError("invalid MPQ block index")

    fpos, csize, fsize, flags = struct.unpack_from(
        "<4I", block_blob, block_index * 16)
    if flags != FLAGS or base + fpos + csize > len(raw):
        raise ValueError("unsupported MPQ file storage")
    data = unpack_file(raw[base + fpos:base + fpos + csize], fsize)
    if len(data) != fsize:
        raise ValueError("MPQ file size mismatch")
    return data


def is_owned_archive(path):
    """True only for an archive carrying this writer's exact marker."""
    try:
        return read_file(path, OWNER_MARKER_NAME) == OWNER_MARKER_CONTENT
    except (OSError, ValueError, struct.error, zlib.error):
        return False


def assert_safe_output(path):
    """Refuse to replace an archive unless its Paragon ownership is proven."""
    if os.path.exists(path) and not is_owned_archive(path):
        raise UnsafeArchiveError(
            "refusing to overwrite unowned archive: %s\n"
            "Move it aside or choose a free name with --general-name/--locale-name."
            % path)


def build(src_dir, out_path):
    # canary check for the crypt table / hash implementation
    assert hash_string("(hash table)", HASH_FILE_KEY) == 0xC3AF3770, "hash algo broken"
    assert hash_string("(block table)", HASH_FILE_KEY) == 0xEC83B3A3, "hash algo broken"

    assert_safe_output(out_path)

    files = []  # (archive_name, abs_path)
    for root, _dirs, names in os.walk(src_dir):
        for n in names:
            abs_p = os.path.join(root, n)
            rel = os.path.relpath(abs_p, src_dir).replace("/", "\\")
            files.append((rel, abs_p))
    files.sort()

    entries = []
    for name, path in files:
        with open(path, "rb") as f:
            entries.append((name, f.read()))
    if any(name.lower() == OWNER_MARKER_NAME.lower() for name, _data in entries):
        raise ValueError("source tree may not supply reserved ownership marker")
    entries.append((OWNER_MARKER_NAME, OWNER_MARKER_CONTENT))
    listfile = "\r\n".join(name for name, _data in entries) + "\r\n"
    entries.append(("(listfile)", listfile.encode("utf-8")))

    hash_size = 1
    while hash_size < len(entries) * 2:
        hash_size *= 2

    header_size = 32
    data_chunks = []
    block_table = []
    pos = header_size
    for _name, data in entries:
        packed = pack_file(data)
        data_chunks.append(packed)
        block_table.append((pos, len(packed), len(data), FLAGS))
        pos += len(packed)

    hash_table = [[0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF] for _ in range(hash_size)]
    for block_index, (name, _data) in enumerate(entries):
        start = hash_string(name, HASH_TABLE_OFFSET) & (hash_size - 1)
        name_a = hash_string(name, HASH_NAME_A)
        name_b = hash_string(name, HASH_NAME_B)
        idx = start
        while hash_table[idx][3] != 0xFFFFFFFF:
            idx = (idx + 1) & (hash_size - 1)
            if idx == start:
                raise RuntimeError("hash table full")
        # locale=0, platform=0 packed as one dword
        hash_table[idx] = [name_a, name_b, 0, block_index]

    hash_bytes = b"".join(struct.pack("<4I", *e) for e in hash_table)
    block_bytes = b"".join(struct.pack("<4I", *e) for e in block_table)
    hash_enc = encrypt_block(hash_bytes, hash_string("(hash table)", HASH_FILE_KEY))
    block_enc = encrypt_block(block_bytes, hash_string("(block table)", HASH_FILE_KEY))

    hash_pos = pos
    block_pos = hash_pos + len(hash_enc)
    archive_size = block_pos + len(block_enc)

    header = struct.pack(
        "<4sIIHHIIII",
        b"MPQ\x1a", header_size, archive_size, 0, 3,
        hash_pos, block_pos, hash_size, len(block_table),
    )
    assert len(header) == header_size

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(out_path) + ".", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(header)
            for chunk in data_chunks:
                f.write(chunk)
            f.write(hash_enc)
            f.write(block_enc)
        verify(temp_path, [name for name, _data in entries], src_dir)
        os.replace(temp_path, out_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return [name for name, _ in entries]


def verify(path, expected_names, src_dir):
    """Independent read-back: parse header, decrypt tables, locate every file by hash, compare bytes."""
    with open(path, "rb") as f:
        raw = f.read()
    magic, hsize, asize, ver, bsize, hpos, bpos, hcount, bcount = struct.unpack("<4sIIHHIIII", raw[:32])
    assert magic == b"MPQ\x1a" and ver == 0 and asize == len(raw), "bad header"

    hash_bytes = decrypt_block(raw[hpos:hpos + hcount * 16], hash_string("(hash table)", HASH_FILE_KEY))
    block_bytes = decrypt_block(raw[bpos:bpos + bcount * 16], hash_string("(block table)", HASH_FILE_KEY))
    hash_table = [struct.unpack("<4I", hash_bytes[i * 16:(i + 1) * 16]) for i in range(hcount)]
    block_table = [struct.unpack("<4I", block_bytes[i * 16:(i + 1) * 16]) for i in range(bcount)]

    for name in expected_names:
        start = hash_string(name, HASH_TABLE_OFFSET) & (hcount - 1)
        name_a = hash_string(name, HASH_NAME_A)
        name_b = hash_string(name, HASH_NAME_B)
        idx = start
        found = None
        while True:
            e = hash_table[idx]
            if e[3] == 0xFFFFFFFF:
                break
            if e[0] == name_a and e[1] == name_b:
                found = e[3]
                break
            idx = (idx + 1) & (hcount - 1)
            if idx == start:
                break
        assert found is not None, "file not found in archive: " + name
        fpos, csize, fsize, flags = block_table[found]
        assert flags == FLAGS, "unexpected flags for " + name
        data = unpack_file(raw[fpos:fpos + csize], fsize)
        assert len(data) == fsize, "size mismatch after unpack: " + name
        if name == OWNER_MARKER_NAME:
            assert data == OWNER_MARKER_CONTENT, "ownership marker mismatch"
        elif name != "(listfile)":
            with open(os.path.join(src_dir, name.replace("\\", os.sep)), "rb") as f:
                disk = f.read()
            assert data == disk, "content mismatch: " + name
    return True


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    try:
        names = build(src, out)
    except UnsafeArchiveError as exc:
        sys.exit(str(exc))
    print("OK: %d files, %d bytes" % (len(names), os.path.getsize(out)))
    for n in names:
        print("  " + n)
