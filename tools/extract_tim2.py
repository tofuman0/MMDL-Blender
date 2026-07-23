#!/usr/bin/env python3
"""Extract embedded TIM2 archives from Tokyo Xtreme Racer 0 XMDL files."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def xmdl_sections(data: bytes) -> list[tuple[int, int]]:
    if len(data) < 16:
        raise ValueError("file is too small to contain an XMDL section table")

    count = read_u32(data, 0)
    table_end = 4 + count * 4
    if not 1 <= count <= 32 or table_end > len(data):
        raise ValueError("invalid XMDL section count")

    offsets = [read_u32(data, 4 + index * 4) for index in range(count)]
    if offsets != sorted(offsets) or offsets[0] < table_end or offsets[-1] >= len(data):
        raise ValueError("invalid XMDL section offsets")

    ends = offsets[1:] + [len(data)]
    return list(zip(offsets, ends))


def extract_tim2(source: Path, output_dir: Path) -> list[Path]:
    data = source.read_bytes()
    tim2_sections = [
        (start, end)
        for start, end in xmdl_sections(data)
        if data[start : start + 4] == b"TIM2"
    ]
    if not tim2_sections:
        raise ValueError(f"{source.name} contains no TIM2 sections")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index, (start, end) in enumerate(tim2_sections):
        suffix = "" if len(tim2_sections) == 1 else f"_{index:02d}"
        destination = output_dir / f"{source.stem}{suffix}.tm2"
        destination.write_bytes(data[start:end])
        outputs.append(destination)

        picture_count = struct.unpack_from("<H", data, start + 6)[0]
        print(
            f"{destination}: 0x{start:X}-0x{end:X}, "
            f"{end - start} bytes, {picture_count} picture(s)"
        )

    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract embedded TIM2 texture archives from an XMDL file."
    )
    parser.add_argument("xmdl", type=Path, help="source .xmdl file")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="destination directory (default: beside the source file)",
    )
    # Blender passes its own arguments to scripts; arguments after `--` belong
    # to this extractor. A normal Python invocation is parsed conventionally.
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None
    args = parser.parse_args(argv)

    source = args.xmdl.resolve()
    output_dir = (args.output_dir or source.parent).resolve()
    extract_tim2(source, output_dir)


if __name__ == "__main__":
    main()
