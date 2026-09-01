"""RAW 촬영정보(EXIF) 추출 — 후지 RAF 및 타 제조사 RAW 공용.

TIFF 기반 RAW(CR2/NEF/ARW/DNG/ORF/RW2/PEF…)는 exifread 가 파일을 직접 읽는다. 후지 RAF 는
독자 컨테이너라 직접은 못 읽지만, 내부에 표준 EXIF 를 가진 JPEG 프리뷰가 임베드돼 있어
(헤더: 0x54=JPEG offset, 0x58=length, big-endian) 그 JPEG 만 떼어 읽는다. CR3(BMFF) 등
파일 직접이 비면 임베드 프리뷰 JPEG 의 EXIF 로 폴백한다(_exif_tags 참조).

주의: Fuji 필름 시뮬레이션 이름은 MakerNote 에 있어 exifread 로는 안 나온다
(앱 자체 필름시뮬 셀렉터로 대체). 여기선 카메라/노출/렌즈 등 표준 EXIF 만 다룬다.
"""
import io
import struct

from decode_lock import QT_IMG_LOCK   # Qt 디코드/인코드 직렬화(교착 방지 — 모듈 주석 참조)

try:
    import exifread
except Exception:  # 의존성 없으면 기능만 비활성(앱은 계속 동작)
    exifread = None

_RAF_MAGIC = b"FUJIFILMCCD-RAW "


def _read_embedded_jpeg(raf_path, max_bytes=512 * 1024):
    """RAF 헤더에서 임베드 JPEG 위치를 찾아 앞부분(EXIF 포함)만 읽어 반환."""
    with open(raf_path, "rb") as f:
        head = f.read(92)
        if len(head) < 92 or head[:16] != _RAF_MAGIC:
            return None
        off = struct.unpack(">I", head[84:88])[0]
        length = struct.unpack(">I", head[88:92])[0]
        if off <= 0 or length <= 0:
            return None
        f.seek(off)
        return f.read(min(length, max_bytes))  # EXIF APP1 은 JPEG 앞쪽


def _is_raf(path) -> bool:
    return str(path).lower().endswith(".raf")


def _is_display_image(path) -> bool:
    """일반 이미지(display-referred) 인가 — 확장자 목록의 단일 출처는 image_loader.
    지연 임포트(numpy/scipy 를 끌고 오므로), 없으면 조용히 False."""
    try:
        from image_loader import IMAGE_EXTS
    except Exception:
        return False
    import os
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTS


def _display_preview_jpeg(path, max_bytes, edge=0):
    """일반 이미지 -> 프리뷰 JPEG 바이트.

    JPEG 은 **파일 그대로** 돌려준다 — 호출부가 setScaledSize 로 libjpeg 축소 디코딩을 하므로
    여기서 디코드하면 그 이득을 버리게 된다. PNG/TIFF 는 임베드 프리뷰가 없어 디코드 후
    JPEG 로 재인코딩해야 한다.
    ⚠️`edge`(요청 긴 변) 를 받으면 **읽는 단계에서 축소**한다 — 없으면 96px 썸네일 하나 때문에
      12MP PNG 를 풀해상도로 디코드하고 10.7MB JPEG 을 만든다(실측 0.78s/장). 썸네일 프로바이더는
      비동기 스레드에서 동시에 여러 장을 굽기 때문에 필름 스캔·TIFF export 폴더를 열면 그대로 체증이 된다.
    ⚠️JPEG 이 max_bytes 보다 크면 **잘라서 주지 않는다** — 잘린 JPEG 은 실패가 아니라 아래쪽이
      회색인 반쪽 이미지로 렌더돼(placeholder 로도 안 떨어짐) 더 나쁘다. 디코드 경로로 넘긴다."""
    import os
    if os.path.splitext(str(path))[1].lower() in (".jpg", ".jpeg"):
        try:
            if os.path.getsize(path) <= max_bytes:
                with open(path, "rb") as f:
                    return f.read()
        except Exception:
            return None
        # 너무 큰 JPEG → 아래 디코드/재인코딩(축소 포함)으로 폴백
    try:
        import numpy as np
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage, QImageReader
        with QT_IMG_LOCK:                         # 플러그인 기계 + 파일 I/O 구간(decode_lock)
            rd = QImageReader(str(path))
            rd.setAutoTransform(True)             # EXIF 방향(로더/썸네일 공통 규약)
            if edge > 0:
                sz = rd.size()                    # 헤더만 읽음(디코드 전)
                long_e = max(sz.width(), sz.height())
                if long_e > edge > 0:
                    f = edge / float(long_e)
                    rd.setScaledSize(QSize(max(1, round(sz.width() * f)),
                                           max(1, round(sz.height() * f))))
            img = rd.read()
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format.Format_RGB888)
        w, h = img.width(), img.height()
        a = (np.frombuffer(img.constBits(), np.uint8)
             .reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3))
        return _encode_bitmap_jpeg(a)
    except Exception:
        return None


def embedded_preview_jpeg(path, max_bytes=64 * 1024 * 1024, edge=0):
    """포맷 중립 임베드 프리뷰 JPEG 바이트. 실패/없음 시 None.

    RAF(후지 독자 컨테이너)는 헤더 오프셋 고속 파싱, 그 외 제조사 RAW(CR2/CR3/NEF/ARW/DNG…)는
    rawpy(LibRaw)가 컨테이너별 최대 임베드 프리뷰를 추출한다. 썸네일/프리뷰/캡션 입력 공용.

    ⚠️일반 이미지(JPG/PNG/TIFF)도 여기로 온다 — 호출부(썸네일/호버 프리뷰/배경화면 썸네일/
      캡션 입력, 총 5곳)가 전부 `QImageReader(buf, b"jpeg")` 로 **포맷을 하드코딩**하고 있어서
      이 한 곳에서 JPEG 바이트로 맞춰 주는 게 가장 작은 변경이다(호출부 무수정).
    edge: 요청 긴 변(px). 일반 이미지 중 임베드 프리뷰가 없는 PNG/TIFF 를 이 크기로 **축소
      디코딩**하는 데만 쓰인다(0=원본). RAW 경로는 임베드 프리뷰가 이미 작아 무관."""
    if _is_raf(path):
        return _read_embedded_jpeg(path, max_bytes=max_bytes)
    if _is_display_image(path):
        return _display_preview_jpeg(path, max_bytes, edge)
    try:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return bytes(thumb.data)          # 대다수 RAW: 임베드 JPEG 그대로
            if thumb.format == rawpy.ThumbFormat.BITMAP:
                return _encode_bitmap_jpeg(thumb.data)  # 일부 DNG 등: 비트맵 → JPEG 인코딩
    except Exception:
        pass
    return None


def _encode_bitmap_jpeg(arr):
    """rawpy BITMAP 썸네일(ndarray H,W,3 RGB) → JPEG 바이트. 호출부가 JPEG 를 기대하므로
    비트맵 썸네일뿐인 RAW(일부 DNG 등)도 썸네일/프리뷰가 뜨게 한다. 실패 시 None."""
    try:
        import numpy as np
        from PySide6.QtCore import QBuffer, QByteArray
        from PySide6.QtGui import QImage, QImageWriter
        arr = np.ascontiguousarray(arr)
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        with QT_IMG_LOCK:                # 파이썬제 QBuffer 인코딩 = 교착 참가자(decode_lock)
            writer = QImageWriter(buf, b"jpeg")
            writer.setQuality(90)
            ok = writer.write(img)
        buf.close()
        return bytes(ba) if ok else None
    except Exception:
        return None


def _exif_tags(path):
    """포맷별 EXIF 태그 dict(exifread). 실패/의존성없음 시 {}.

    RAF=임베드 JPEG, TIFF 기반 RAW(CR2/NEF/ARW/DNG/ORF/RW2/PEF…)=exifread 로 파일 직접,
    그래도 비면(CR3 등 BMFF) 임베드 프리뷰 JPEG 의 EXIF 로 폴백."""
    if exifread is None:
        return {}
    if _is_raf(path):
        jpeg = _read_embedded_jpeg(path)
        if not jpeg:
            return {}
        try:
            return exifread.process_file(io.BytesIO(jpeg), details=False)
        except Exception:
            return {}
    # TIFF 기반 RAW: exifread 가 파일을 직접 읽는다.
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        if tags:
            return tags
    except Exception:
        pass
    # 일반 이미지는 위의 '파일 직접'이 곧 최종 답이다 — 폴백으로 가면 PNG/TIFF 를 통째로
    # 디코드해 JPEG 로 재인코딩하는데(사진 열 때마다 2회, 실측 1.11s/12MP) Qt JPEG 라이터는
    # EXIF 를 쓰지 않으므로 결과가 항상 빈 딕셔너리다. 순수 낭비라 여기서 끊는다.
    if _is_display_image(path):
        return {}
    # 폴백(CR3 등): 임베드 프리뷰 JPEG 의 표준 EXIF.
    jpeg = embedded_preview_jpeg(path)
    if not jpeg:
        return {}
    try:
        return exifread.process_file(io.BytesIO(jpeg), details=False)
    except Exception:
        return {}


def read_orientation(path) -> int:
    """RAW 의 EXIF Image Orientation(1~8) 반환. 실패/없음 시 1(가로).
    날짜 스탬프를 촬영 방향(센서 가로 프레임)에 맞춰 배치하는 데 쓴다."""
    if exifread is None:
        return 1
    try:
        tags = _exif_tags(path)
        ori = tags.get("Image Orientation")
        v = int(ori.values[0]) if ori and ori.values else 1
        return v if v in (1, 2, 3, 4, 5, 6, 7, 8) else 1
    except Exception:
        return 1


def _ratio(v):
    try:
        return float(v.num) / float(v.den) if v.den else None
    except Exception:
        try:
            return float(v)
        except Exception:
            return None


def _first(tag):
    try:
        return tag.values[0]
    except Exception:
        return None


def _fmt_aperture(tag):
    f = _ratio(_first(tag))
    return f"f/{f:g}" if f else None


def _fmt_shutter(tag):
    r = _first(tag)
    try:
        num, den = r.num, r.den
    except Exception:
        return None
    if not den:
        return None
    if num <= 0:                                 # 0/x 등 변칙 EXIF → 표시 생략(0 나눗셈 방지)
        return None
    if num != 1 and den % num == 0:              # 2/4 같은 형태 정규화
        den, num = den // num, 1
    if num == 1:
        return f"1/{den}s"
    f = num / den
    if f >= 1:
        return f"{f:g}s"
    return f"1/{round(den / num)}s"


def _fmt_focal(tag):
    f = _ratio(_first(tag))
    return f"{f:g}mm" if f else None


def _fmt_iso(tag):
    v = _first(tag)
    return f"ISO {v}" if v is not None else None


def _fmt_ev(tag):
    f = _ratio(_first(tag))
    if f is None:
        return None
    return "0 EV" if abs(f) < 1e-6 else f"{f:+.2f} EV"


def _camera_name(make, model):
    """Make + Model -> 사람이 읽는 바디 이름. 없으면 None.

    ⚠️단순 결합은 `"Canon Canon EOS 400D DIGITAL"`, `"NIKON CORPORATION NIKON D90"` 처럼
    제조사가 두 번 들어간다(Model 에 이미 브랜드가 박힌 바디가 많다). Model 이 Make 의 첫 단어로
    시작하면 Make 를 떼서 `"Canon EOS 400D DIGITAL"`, `"NIKON D90"` 로 만든다.
    `"FUJIFILM" + "X100V"` 처럼 겹치지 않는 경우는 그대로 결합된다."""
    make, model = (make or "").strip(), (model or "").strip()
    if model and make:
        first = make.split()[0]
        if model.upper().startswith(first.upper()):
            return model
    return (make + " " + model).strip() or None


def _fmt_date(tag):
    # "2026:04:20 18:16:23" -> "2026-04-20 18:16:23"
    if tag is None:
        return None                 # str(None)=="None"(truthy)이 'Date: None' 로 새어나감
    s = str(tag).strip()
    return s.replace(":", "-", 2) if s else None


def _gps_deg(tag, ref) -> float:
    """GPS RATIONAL 3개(도/분/초) + Ref 문자 -> 십진 도. 실패 시 None.
    ⚠️EXIF 의 위/경도는 **부호가 없다** — 남반구/서반구는 Ref 가 S/W 로만 표시된다."""
    try:
        v = tag.values
        if len(v) < 2:
            return None
        d = _ratio(v[0]) or 0.0
        m = _ratio(v[1]) or 0.0
        sec = (_ratio(v[2]) or 0.0) if len(v) > 2 else 0.0
        deg = d + m / 60.0 + sec / 3600.0
        return -deg if str(ref).strip().upper() in ("S", "W") else deg
    except Exception:
        return None


def read_gps(path):
    """파일에 기록된 EXIF GPS -> `(lat, lon, alt|None)` 십진 도. 없거나 실패 시 None.

    카메라가 남긴 좌표를 **초기값**으로 쓰기 위한 것이다. 앱에서 사용자가 붙인 좌표는
    사이드카가 갖고 있고 그쪽이 우선한다(Controller 가 병합).

    ⚠️정확히 (0, 0)은 **위치로 보지 않는다** — GPS 필드를 0으로 채워 두는 파일이 흔하고,
      기니만 앞바다를 찍은 사진일 확률보다 그쪽이 압도적으로 높다.
    """
    if exifread is None:
        return None
    try:
        tags = _exif_tags(path)
    except Exception:
        return None
    lat_t, lon_t = tags.get("GPS GPSLatitude"), tags.get("GPS GPSLongitude")
    if not lat_t or not lon_t:
        return None
    lat = _gps_deg(lat_t, tags.get("GPS GPSLatitudeRef") or "N")
    lon = _gps_deg(lon_t, tags.get("GPS GPSLongitudeRef") or "E")
    if lat is None or lon is None:
        return None
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None
    alt = None
    alt_t = tags.get("GPS GPSAltitude")
    if alt_t:
        a = _ratio(_first(alt_t))
        if a is not None:
            try:
                alt = -a if int(str(_first(tags.get("GPS GPSAltitudeRef")) or 0)) == 1 else a
            except (TypeError, ValueError):
                alt = a
    return (lat, lon, alt)


def format_gps(lat, lon) -> str:
    """좌표 -> 패널/오버레이에 쓰는 한 줄. 소수점 6자리 ~= 0.1m 로 사진 위치엔 과분하다."""
    return f"{float(lat):.6f}, {float(lon):.6f}"


def read_shooting_info(path):
    """RAW 경로 -> (fields, summary).

    fields:  [{"label": str, "value": str}, ...]  (우측 패널용, 순서 유지)
    summary: 오버레이용 2줄 문자열 (예: "23mm  f/2.8\\n1/250s  ISO 1250")
    실패/비RAW/의존성없음 시 ([], "").
    """
    if exifread is None:
        return [], ""
    try:
        tags = _exif_tags(path)
    except Exception:
        return [], ""
    if not tags:
        return [], ""

    def t(key):
        return tags.get(key)

    make = str(t("Image Make") or "").strip()
    model = str(t("Image Model") or "").strip()
    camera = _camera_name(make, model)
    # 렌즈: 표준 EXIF 2.3 태그만 본다. ⚠️대부분 비어 있는 게 정상 —
    #   고정렌즈 바디(X100V)는 아예 안 쓰고, Canon/Nikon 등은 MakerNote 에만 쓰는 경우가 많은데
    #   _exif_tags 는 details=False(MakerNote 미파싱)다. 없으면 없는 대로 두고 초점거리로 대체하지 않는다
    #   (측정: 실사용 파일 8개 전부 렌즈 태그 없음).
    lens = str(t("EXIF LensModel") or t("MakerNote LensModel") or "").strip() or None

    aperture = _fmt_aperture(t("EXIF FNumber")) if t("EXIF FNumber") else None
    shutter = _fmt_shutter(t("EXIF ExposureTime")) if t("EXIF ExposureTime") else None
    iso = _fmt_iso(t("EXIF ISOSpeedRatings")) if t("EXIF ISOSpeedRatings") else None
    focal = _fmt_focal(t("EXIF FocalLength")) if t("EXIF FocalLength") else None
    ev = _fmt_ev(t("EXIF ExposureBiasValue")) if t("EXIF ExposureBiasValue") else None
    program = str(t("EXIF ExposureProgram")).strip() if t("EXIF ExposureProgram") else None
    metering = str(t("EXIF MeteringMode")).strip() if t("EXIF MeteringMode") else None
    wb = str(t("EXIF WhiteBalance")).strip() if t("EXIF WhiteBalance") else None
    flash = str(t("EXIF Flash")).strip() if t("EXIF Flash") else None
    firmware = str(t("Image Software")).strip() if t("Image Software") else None
    date = _fmt_date(t("EXIF DateTimeOriginal") or t("Image DateTime"))

    rows = [
        ("Camera", camera),
        ("Lens", lens),
        ("Firmware", firmware),
        ("Aperture", aperture),
        ("Shutter", shutter),
        ("ISO", iso),
        ("Focal Length", focal),
        ("Exp. Comp.", ev),
        ("Program", program),
        ("Metering", metering),
        ("White Balance", wb),
        ("Flash", flash),
        ("Date", date),
    ]
    fields = [{"label": k, "value": v} for k, v in rows if v]

    # 오버레이 요약(2줄): 초점/조리개 · 셔터/ISO
    line1 = "  ".join(x for x in (focal, aperture) if x)
    line2 = "  ".join(x for x in (shutter, iso) if x)
    summary = "\n".join(x for x in (line1, line2) if x)

    return fields, summary
