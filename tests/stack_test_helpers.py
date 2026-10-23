"""Helpers for identifying disposable test data before touching live services."""
import hashlib


def torrent_infohash(payload):
    """Hash the original bencoded info dictionary without re-encoding it."""
    def skip(pos):
        kind = payload[pos:pos + 1]
        if kind == b'i':
            return payload.index(b'e', pos) + 1
        if kind in (b'l', b'd'):
            pos += 1
            while payload[pos:pos + 1] != b'e':
                pos = skip(pos)
            return pos + 1
        colon = payload.index(b':', pos)
        return colon + 1 + int(payload[pos:colon])

    if not payload.startswith(b'd'):
        raise ValueError('Torrent must be a bencoded dictionary')
    pos = 1
    while payload[pos:pos + 1] != b'e':
        colon = payload.index(b':', pos)
        length = int(payload[pos:colon])
        key = payload[colon + 1:colon + 1 + length]
        start = colon + 1 + length
        end = skip(start)
        if key == b'info':
            return hashlib.sha1(payload[start:end]).hexdigest()
        pos = end
    raise ValueError('Torrent has no info dictionary')
