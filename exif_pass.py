# -*- coding: utf-8 -*-
"""원본 EXIF 를 export JPEG 으로 **통과**시킨다 (`docs/exif_passthrough.md` 1단계).

지금까지 export 는 `Software`/`DateTime` 2개만 든 APP1 을 새로 만들어 끼웠고 카메라가 적어 둔
것은 통째로 버려졌다 — 촬영일 정렬·지도 뷰·라이트룸 임포트가 전부 여기서 끊긴다.

**방식은 재직렬화다.** 소스 APP1 을 파싱해 남길 것만 고르고 오프셋을 다시 계산해 새 APP1 을
만든다. ⚠️소스를 통째로 복사하고 인플레이스로 덮어쓰는 안은 **기각됐다** — 소스 APP1 이
64 KB 상한까지 여유 85 B 뿐인데 지오태그 GPS IFD 가 178 B 라 들어가지 않는다(문서 참조).

★쓰기는 `pipeline._ifd_block` 을 그대로 쓴다(payload 길이로 인라인/오프셋을 정하므로 타입에
무관하게 이미 범용이다). 이 모듈이 더하는 것은 **읽기(파서)** 다.
"""

import os
import struct

import exif_info
import pipeline

# ---------- 태그 ----------
_ORIENTATION = 0x0112
_PROCESSING_SOFTWARE = 0x000B     # 원본 Software(카메라 펌웨어)를 옮겨 담는 자리
_SOFTWARE = 0x0131
_DATETIME = 0x0132
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_INTEROP_IFD = 0xA005
_PIXEL_X = 0xA002
_PIXEL_Y = 0xA003
_MAKERNOTE = 0x927C

# IFD0 에서 버릴 태그 — 전부 **원본 파일 안의 위치**를 가리켜 새 파일에선 무의미하다.
# (썸네일 포인터/길이, 스트립·타일 오프셋, 서브 IFD 포인터)
_DROP_IFD0 = {0x0111, 0x0117, 0x0144, 0x0145, 0x014A, 0x0201, 0x0202}
# ExifIFD 에서 버릴 태그 — Interop IFD 는 값어치 대비 복잡도가 커서 뺀다(문서 '태그 정책').
_DROP_EXIF = {_INTEROP_IFD}

_APP1_MAX_PAYLOAD = 65533         # FFE1 <len> 의 len 은 자기 자신 2B 를 포함한다


def find_app1(jpeg: bytes):
    """JPEG 바이트열에서 EXIF APP1 의 `(오프셋, 세그먼트 전체 길이)`. 없으면 `(None, None)`.

    ⚠️APP1 은 XMP 도 쓴다 — `"Exif\\x00\\x00"` 로 시작하는 것만 EXIF 다.
    """
    if not jpeg or not jpeg.startswith(b"\xFF\xD8"):
        return None, None
    i = 2
    while i + 4 <= len(jpeg):
        if jpeg[i] != 0xFF:
            return None, None
        marker = jpeg[i + 1]
        if marker in (0xD8, 0xD9, 0xDA):      # SOI/EOI/SOS — 여기부터는 메타데이터가 없다
            return None, None
        ln = struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if ln < 2 or i + 2 + ln > len(jpeg):
            return None, None                 # 길이가 깨졌다 — 건드리지 않는다
        if marker == 0xE1 and jpeg[i + 4:i + 10] == b"Exif\x00\x00":
            return i, 2 + ln
        i += 2 + ln
    return None, None


def source_app1(path: str):
    """사진 파일에서 **카메라의 EXIF APP1** 바이트. 없으면 None.

    RAF 는 임베드 프리뷰 JPEG 이 카메라의 진짜 EXIF(MakerNote 포함)를 들고 있다.
    ⚠️**타사 RAW 의 임베드 프리뷰는 LibRaw 가 새로 쓴 것**이다(9개 기종에서 APP1 이 정확히
      1384 B, `Software = dcraw v9.26`). 렌즈·GPS·MakerNote 가 없는 축소본이지만 Make/Model/
      DateTimeOriginal/노출/ISO 는 들어 있어 지금(2개)보다는 낫다 — 그대로 통과시킨다.
      제대로 된 타사 지원은 파일 자체의 IFD 를 읽는 2단계다.
    """
    try:
        if exif_info._is_display_image(path):
            if os.path.splitext(path)[1].lower() not in (".jpg", ".jpeg", ".jfif"):
                return None                   # PNG/TIFF 에는 APP1 이 없다(2단계)
            with open(path, "rb") as f:
                jpeg = f.read()
        else:
            jpeg = exif_info.embedded_preview_jpeg(path)
        if not jpeg:
            return None
        off, ln = find_app1(jpeg)
        return jpeg[off:off + ln] if off is not None else None
    except Exception:
        return None                           # 통과는 부가 기능이다 — 실패해도 export 는 간다


def _to_le(payload: bytes, typ: int, bo: str) -> bytes:
    """소스 값을 little-endian 으로. `_ifd_block` 이 항상 `II` 로 쓰기 때문이다.

    ★⚠️**little-endian 소스면 아무것도 하지 않는다.** 바이트순서를 안 보고 무조건 뒤집었다가
      `II` 소스의 `0x8769`(ExifIFD 포인터)가 깨져 **서브 IFD 전체(MakerNote 63개 포함)가
      통째로 유실**됐다 — 값이 비는 게 아니라 IFD 를 못 찾아 조용히 0개가 된다.
    ⚠️ASCII/UNDEFINED 는 **바이트열이라 뒤집으면 안 된다** — MakerNote 가 여기 해당한다
      (자기 안에 자기 바이트순서를 따로 들고 있다).
    """
    if bo == "<":
        return payload
    unit = pipeline._TIFF_SWAP_UNIT.get(typ, 0)
    if unit <= 1:
        return payload
    out = bytearray(payload)
    for i in range(0, len(out) - unit + 1, unit):
        out[i:i + unit] = out[i:i + unit][::-1]
    return bytes(out)


def _read_ifd(tiff: bytes, bo: str, base: int):
    """IFD 하나 -> `([(tag, typ, count, payload_LE), ...], next_ifd_offset)`.

    payload 는 **이미 little-endian 으로 정규화**돼 있어 그대로 `_ifd_block` 에 넘길 수 있다.
    ⚠️길이를 못 구하거나(모르는 타입) 범위를 벗어나는 엔트리는 **버린다** — 깨진 EXIF 하나로
      export 를 실패시키지 않는다.
    """
    if base + 2 > len(tiff):
        return [], 0
    n = struct.unpack(bo + "H", tiff[base:base + 2])[0]
    end = base + 2 + 12 * n
    if n > 4096 or end + 4 > len(tiff):
        return [], 0                          # 엔트리 수가 비상식적 = 깨진 헤더
    out = []
    for k in range(n):
        e = base + 2 + 12 * k
        tag, typ, count = struct.unpack(bo + "HHI", tiff[e:e + 8])
        unit = pipeline._TIFF_TYPE_SIZE.get(typ)
        if unit is None or count > 0x10000000:
            continue
        size = unit * count
        if size <= 4:
            payload = tiff[e + 8:e + 8 + size]
        else:
            off = struct.unpack(bo + "I", tiff[e + 8:e + 12])[0]
            if off + size > len(tiff):
                continue                      # 오프셋이 블록 밖 — 버린다
            payload = tiff[off:off + size]
        out.append((tag, typ, count, _to_le(payload, typ, bo)))
    nxt = struct.unpack(bo + "I", tiff[end:end + 4])[0]
    return out, nxt


def parse_app1(app1: bytes):
    """APP1 세그먼트 -> `{"ifd0": [...], "exif": [...], "gps": [...]}`. 실패 시 None.

    ⚠️IFD1(썸네일)은 **읽지 않는다** — 크롭 전 구도라 거짓이고, 소스 APP1 의 95%가 이것과
      패딩이다(재직렬화가 3 KB 로 줄어드는 이유).
    """
    try:
        if not app1 or app1[:2] != b"\xFF\xE1" or app1[4:10] != b"Exif\x00\x00":
            return None
        tiff = app1[10:]
        if tiff[:2] not in (b"II", b"MM"):
            return None
        bo = "<" if tiff[:2] == b"II" else ">"
        if struct.unpack(bo + "H", tiff[2:4])[0] != 42:
            return None
        ifd0, _ = _read_ifd(tiff, bo, struct.unpack(bo + "I", tiff[4:8])[0])
        sub = {"ifd0": ifd0, "exif": [], "gps": []}
        for tag, typ, count, payload in ifd0:
            if tag in (_EXIF_IFD, _GPS_IFD) and len(payload) >= 4:
                off = struct.unpack("<I", payload[:4])[0]
                key = "exif" if tag == _EXIF_IFD else "gps"
                sub[key], _ = _read_ifd(tiff, bo, off)
        return sub
    except Exception:
        return None


def _put(entries, tag, typ, count, payload):
    """같은 태그가 있으면 갈아끼우고 없으면 넣는다(정렬은 마지막에 한 번)."""
    for i, (t, _, _, _) in enumerate(entries):
        if t == tag:
            entries[i] = (tag, typ, count, payload)
            return
    entries.append((tag, typ, count, payload))


def _ascii(tag, text):
    b = text.encode("ascii", "replace") + b"\x00"
    return (tag, 2, len(b), b)


def build_app1(parsed, *, software="", when="", width=0, height=0,
               gps=None, keep_gps=True) -> bytes:
    """파싱된 소스 + 우리 값 -> 새 APP1. 상한을 넘거나 실패하면 None(호출부가 폴백).

    `gps`: 사용자가 Location 탭에서 붙인 `(lat, lon, alt|None)`. **원본 EXIF GPS 보다 우선**한다.
    `keep_gps`: 사용자 좌표가 없을 때 **원본 GPS 를 통과시킬지**. 앱 설정(기본 ON)이 정한다.

    구조: `TIFF 헤더 | IFD0 | ExifIFD | GPS IFD | 공유 데이터` — 세 IFD 가 **하나의 꼬리 블록**을
    나눠 쓰므로 `_ifd_block` 을 같은 `blobs` 로 순서대로 불러야 한다(`_exif_app1` 과 동일).
    ⚠️`0x8769`/`0x8825` 값은 뒤쪽 IFD 의 절대 오프셋이라 **크기를 먼저 확정한 뒤**(2-패스) 채운다.
    """
    try:
        ifd0 = [e for e in parsed["ifd0"]
                if e[0] not in _DROP_IFD0 and e[0] not in (_EXIF_IFD, _GPS_IFD)]
        exif = [e for e in parsed["exif"] if e[0] not in _DROP_EXIF]

        # --- 원본 Software(카메라 펌웨어)를 ProcessingSoftware 로 옮긴다 ---
        # ⚠️`dcraw ...` 는 카메라가 아니라 LibRaw 가 프리뷰를 뽑으며 쓴 값이라 옮기지 않는다
        #   (옮기면 "이 사진을 dcraw 가 처리했다"는 거짓이 된다).
        for tag, typ, count, payload in list(ifd0):
            if tag == _SOFTWARE:
                orig = payload.split(b"\x00")[0].decode("ascii", "replace").strip()
                if orig and not orig.lower().startswith("dcraw"):
                    _put(ifd0, _PROCESSING_SOFTWARE, 2, len(payload), payload)
                break

        # --- 우리가 덮어쓰는 값 ---
        _put(ifd0, _ORIENTATION, 3, 1, struct.pack("<H", 1))   # 회전은 픽셀에 이미 구웠다
        if software:
            _put(ifd0, *_ascii(_SOFTWARE, software))
        if when:
            _put(ifd0, *_ascii(_DATETIME, when))
        if width > 0 and height > 0 and exif:
            _put(exif, _PIXEL_X, 4, 1, struct.pack("<I", int(width)))
            _put(exif, _PIXEL_Y, 4, 1, struct.pack("<I", int(height)))

        # --- GPS: 사용자 좌표 > 원본 > 없음 ---
        if gps:
            gps_ent = list(pipeline._gps_entries(gps))
        elif keep_gps:
            gps_ent = list(parsed["gps"])
        else:
            gps_ent = []

        if exif:
            _put(ifd0, _EXIF_IFD, 4, 1, b"")       # 값은 2-패스로 채운다
        if gps_ent:
            _put(ifd0, _GPS_IFD, 4, 1, b"")
        ifd0.sort(key=lambda e: e[0])
        exif.sort(key=lambda e: e[0])
        gps_ent.sort(key=lambda e: e[0])

        # --- 2-패스 오프셋 ---
        exif_off = 8 + (2 + 12 * len(ifd0) + 4)
        exif_size = (2 + 12 * len(exif) + 4) if exif else 0
        gps_off = exif_off + exif_size
        gps_size = (2 + 12 * len(gps_ent) + 4) if gps_ent else 0
        data_off = gps_off + gps_size
        ifd0 = [(t, ty, c,
                 struct.pack("<I", exif_off) if t == _EXIF_IFD else
                 struct.pack("<I", gps_off) if t == _GPS_IFD else pl)
                for (t, ty, c, pl) in ifd0]

        blobs = bytearray()
        b0 = pipeline._ifd_block(ifd0, data_off, blobs)
        b1 = pipeline._ifd_block(exif, data_off, blobs) if exif else b""
        b2 = pipeline._ifd_block(gps_ent, data_off, blobs) if gps_ent else b""
        tiff = (b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
                + b0 + b1 + b2 + bytes(blobs))
        payload = b"Exif\x00\x00" + tiff
        if len(payload) + 2 > _APP1_MAX_PAYLOAD:
            return None            # 상한 초과 — 크레딧 전용으로 폴백(라이카 MakerNote 28 KB 등)
        return b"\xFF\xE1" + struct.pack(">H", len(payload) + 2) + payload
    except Exception:
        return None


def app1_for_export(src_path: str, *, software="", when="", width=0, height=0,
                    gps=None, keep_gps=True):
    """`source_app1` -> `parse_app1` -> `build_app1` 한 번에. 어디서든 실패하면 None.

    None 이면 호출부(`pipeline.save_image`)가 기존 `_exif_app1` 크레딧 경로로 되돌아간다 —
    **메타데이터 때문에 산출물을 깨뜨리지 않는다**(`_insert_png_chunks` 와 같은 원칙).
    """
    if not src_path:
        return None
    parsed = parse_app1(source_app1(src_path))
    if not parsed:
        return None
    return build_app1(parsed, software=software, when=when, width=width,
                      height=height, gps=gps, keep_gps=keep_gps)
