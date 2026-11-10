"""Zstandard decompression with the game's dictionaries.

The dictionary is selected by the dictionary ID stored in the frame header:
1 = zs.zsdic (default), 2 = bkprb.zsdic, 3 = bcett.byml.zsdic, 4 = pack.zsdic.
Frames without an ID are tried with the default dictionary first, then without one.
"""
from __future__ import annotations

ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'

try:  # Python 3.14+
    from compression import zstd as _z

    def _frame_dict_id(data: bytes) -> int:
        return _z.get_frame_info(data).dictionary_id

    def _make_dict(blob: bytes):
        d = _z.ZstdDict(blob)
        return d, d.dict_id

    def _decompress(data: bytes, d=None) -> bytes:
        return _z.decompress(data, zstd_dict=d) if d is not None else _z.decompress(data)

    def _decompress_head(data: bytes, n: int, d=None) -> bytes:
        dec = _z.ZstdDecompressor(zstd_dict=d) if d is not None else _z.ZstdDecompressor()
        return dec.decompress(data, max_length=n)

    ZstdError = _z.ZstdError
except ImportError:  # fallback: zstandard package
    import zstandard as _zs

    def _frame_dict_id(data: bytes) -> int:
        return _zs.get_frame_parameters(data).dict_id

    def _make_dict(blob: bytes):
        d = _zs.ZstdCompressionDict(blob)
        return d, d.dict_id()

    def _decompress(data: bytes, d=None) -> bytes:
        dctx = _zs.ZstdDecompressor(dict_data=d) if d is not None else _zs.ZstdDecompressor()
        return dctx.decompressobj().decompress(data)

    def _decompress_head(data: bytes, n: int, d=None) -> bytes:
        return _decompress(data, d)[:n]

    ZstdError = _zs.ZstdError


def is_zstd(data: bytes) -> bool:
    return data[:4] == ZSTD_MAGIC


class ZstdCodec:
    def __init__(self):
        self.dicts: dict[int, object] = {}
        self.names: dict[int, str] = {}
        self.default_id = None

    def add_dict(self, name: str, blob: bytes):
        d, did = _make_dict(blob)
        self.dicts[did] = d
        self.names[did] = name
        if name == 'zs.zsdic':
            self.default_id = did

    def decompress(self, data: bytes, head: int | None = None) -> bytes:
        """Whole frame, or with head=n only the first n bytes (data may then be truncated)."""
        run = _decompress if head is None else (lambda b, d=None: _decompress_head(b, head, d))
        try:
            did = _frame_dict_id(data)
        except Exception:
            did = 0
        if did:
            d = self.dicts.get(did)
            if d is None:
                raise ValueError(f'Unknown zstd dictionary {did}')
            return run(data, d)
        if self.default_id is not None:
            try:
                return run(data, self.dicts[self.default_id])
            except ZstdError:
                pass
        return run(data)
