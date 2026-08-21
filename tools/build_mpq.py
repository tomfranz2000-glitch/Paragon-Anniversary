"""Minimal MPQ v1 archive writer (uncompressed, single-unit files) + self-verifying reader.

Builds a WoW 3.3.5a-compatible patch MPQ from a directory tree.
"""
import os
import struct
import sys

# Blizzard-style storage: zlib-compressed sectors with a sector offset table,
# matching the layout of the client's own patch MPQs (flags 0x80000200).
import zlib

MPQ_FILE_EXISTS = 0x80000000
MPQ_FILE_COMPRESS = 0x00000200
FLAGS = MPQ_FILE_EXISTS | MPQ_FILE_COMPRESS

SECTOR_SHIFT = 3
SECTOR_SIZE = 512 << SECTOR_SHIFT  # 4096


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


def build(src_dir, out_path):
    # canary check for the crypt table / hash implementation
    assert hash_string("(hash table)", HASH_FILE_KEY) == 0xC3AF3770, "hash algo broken"
    assert hash_string("(block table)", HASH_FILE_KEY) == 0xEC83B3A3, "hash algo broken"

    files = []  # (archive_name, abs_path)
    for root, _dirs, names in os.walk(src_dir):
        for n in names:
            abs_p = os.path.join(root, n)
            rel = os.path.relpath(abs_p, src_dir).replace("/", "\\")
            files.append((rel, abs_p))
    files.sort()

    listfile = "\r\n".join(name for name, _ in files) + "\r\n"
    entries = [(name, open(p, "rb").read()) for name, p in files]
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

    with open(out_path, "wb") as f:
        f.write(header)
        for chunk in data_chunks:
            f.write(chunk)
        f.write(hash_enc)
        f.write(block_enc)

    return [name for name, _ in entries]


def verify(path, expected_names, src_dir):
    """Independent read-back: parse header, decrypt tables, locate every file by hash, compare bytes."""
    raw = open(path, "rb").read()
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
        if name != "(listfile)":
            disk = open(os.path.join(src_dir, name.replace("\\", os.sep)), "rb").read()
            assert data == disk, "content mismatch: " + name
    return True


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    names = build(src, out)
    verify(out, names, src)
    print("OK: %d files, %d bytes" % (len(names), os.path.getsize(out)))
    for n in names:
        print("  " + n)
