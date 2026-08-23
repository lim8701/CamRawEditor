# -*- coding: utf-8 -*-
r"""앱 아이콘 생성 → icons/app.ico (Windows) / icons/app.icns (macOS).

디자인(2026-08 확정): 다크 라운드 타일 + 위아래 스프로킷 홀 + 그레이딩 그러데이션 레터마크.
  - 256/128/64/48px: "RAW" + 홀 5개   (바탕화면·시작메뉴·탐색기)
  - 32/24/16px:      "R"   + 홀 3개   (작업표시줄 소형·창 타이틀바 — RAW 는 이 크기에서 안 읽힘)
홀은 **투명 컷아웃**(진짜 필름처럼 배경이 비친다 — 사용자 선택. 어두운 채움 안은 기각).
Windows 는 없는 크기를 가장 가까운 엔트리에서 스케일하므로 256 이 대형의 마스터 역할을 하고,
128/64/48 은 탐색기 중간 보기에서 스케일링 뭉개짐을 줄이는 보조 엔트리다.

실행:  .\.venv\Scripts\python.exe packaging\make_icon.py          # OS 기본 포맷
       .venv/bin/python packaging/make_icon.py --icns             # 포맷 강제
의존: PySide6(렌더) + Pillow(ico/png 컨테이너). 앱 런타임과 무관한 빌드 도구.
      macOS 는 `iconutil`(Xcode Command Line Tools) 필요.

⚠️도형 상수는 모두 기준 해상도 S=256 단위다 — 다른 해상도는 k=size/S 로 스케일해 **같은
  디자인을 그 해상도에서 다시 렌더**한다(업스케일이 아니다). macOS 는 1024 마스터가 필요하다.

⚠️레터마크 폰트는 OS 마다 다르다: Windows=Segoe UI Black, macOS=Arial Black. 두 폰트 모두
  시스템 제공이라 **번들하지 않는다**(Segoe UI 는 Windows 외 재배포 불가). 글자 모양이 미세하게
  다르므로, 두 플랫폼 아이콘을 픽셀 단위로 맞추고 싶으면 Windows 에서 `--icns` 로 함께 생성할 것.

⚠️macOS 는 아이콘에 **사방 여백**이 있어야 Dock 에서 다른 앱과 같은 크기로 보인다(Apple 그리드
  1024 캔버스에 824 본체 = 80.5%). 128px 이상에만 넣는다 — 16/32 는 여백을 주면 글자가 뭉갠다.

⚠️아이콘을 재생성했으면 **build/ 캐시를 지우고** 패키징할 것 — PyInstaller 는 icon 파일의
  '내용' 변경을 감지하지 못해 exe 를 캐시에서 재사용한다(실측: 창 아이콘만 새것, exe 리소스는
  옛것인 반쪽 빌드가 나왔음). `Remove-Item -Recurse build` 후 build.ps1.
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from PySide6.QtCore import QPointF, QRectF, Qt                      # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase,    # noqa: E402
                           QFontMetrics, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QPainterPath)

S = 256                     # 기준 해상도 — 아래 도형 상수의 단위(다른 크기는 k=size/S 배)
MAC_BODY = 0.805            # macOS 아이콘 본체 비율(Apple 그리드 824/1024)
MAC_PAD_MIN = 128           # 이 크기 이상에만 여백을 준다


def _font_black():
    """레터마크용 헤비 그로테스크. 오프스크린 Windows 는 시스템 폰트 DB 가 비어 TTF 직접 등록."""
    if sys.platform == "win32":
        fid = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\seguibl.ttf")  # Segoe UI Black
        fams = QFontDatabase.applicationFontFamilies(fid)
        if not fams:
            raise RuntimeError("Segoe UI Black(seguibl.ttf) 로드 실패")
        return fams[0]
    for fam in ("Arial Black", "Helvetica Neue", "Impact"):      # macOS 시스템 제공
        if fam in QFontDatabase.families():
            return fam
    raise RuntimeError("헤비 폰트를 찾을 수 없습니다(Arial Black 등)")


def _tile(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    k = size / S
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), 58 * k, 58 * k)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0.0, QColor("#232630"))
    g.setColorAt(1.0, QColor("#15161a"))
    p.fillPath(path, QBrush(g))
    p.setClipPath(path)
    return img, p


def _holes(p, size, n, hw, hh, inset_y, rad):
    """위아래 스프로킷 홀 — 타일을 투명하게 뚫는다(CompositionMode_Clear). n 개 균등 배치."""
    k = size / S
    hw, hh, inset_y, rad = hw * k, hh * k, inset_y * k, rad * k
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 255))
    margin = 40 * k
    span = size - margin * 2
    for i in range(n):
        cx = margin + span * (i + 0.5) / n
        for cy in (inset_y, size - inset_y):
            p.drawRoundedRect(QRectF(cx - hw / 2, cy - hh / 2, hw, hh), rad, rad)
    p.restore()


def _lettermark(p, size, family, text, px, tracking=0.0):
    """그림자 + 틸→앰버→오렌지 그러데이션 레터마크(가운데 정렬)."""
    k = size / S
    f = QFont(family, 1)
    f.setWeight(QFont.Weight.Black)
    f.setPixelSize(max(1, round(px * k)))
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
    fm = QFontMetrics(f)
    x = (size - fm.horizontalAdvance(text)) / 2
    y = size / 2 + fm.capHeight() / 2
    p.setPen(Qt.PenStyle.NoPen)
    for dy, a in ((6, 46), (3, 60)):
        sh = QPainterPath()
        sh.addText(x, y + dy * k, f, text)
        p.setBrush(QColor(0, 0, 0, a))
        p.drawPath(sh)
    path = QPainterPath()
    path.addText(x, y, f, text)
    r = path.boundingRect()
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0.0, QColor("#2fc4b2"))
    g.setColorAt(0.5, QColor("#ffc35e"))
    g.setColorAt(1.0, QColor("#ff7a45"))
    p.setBrush(QBrush(g))
    p.drawPath(path)


def render_large(family, size=S):
    """RAW + 홀 5개 (48px 이상용 마스터)."""
    img, p = _tile(size)
    _lettermark(p, size, family, "RAW", 76, tracking=97)
    _holes(p, size, 5, hw=24, hh=17, inset_y=33, rad=5.5)
    p.end()
    return img


def render_small(family, size=S):
    """R + 홀 3개 (32px 이하용 마스터 — 축소돼도 홀이 점으로 살아남게 크고 적게)."""
    img, p = _tile(size)
    _lettermark(p, size, family, "R", 140)
    _holes(p, size, 3, hw=36, hh=24, inset_y=36, rad=8)
    p.end()
    return img


def _to_pil(qimg):
    from PIL import Image
    qimg = qimg.convertedTo(QImage.Format.Format_ARGB32)
    return Image.frombuffer("RGBA", (qimg.width(), qimg.height()),
                            qimg.bits().tobytes(), "raw", "BGRA", 0, 1)


def make_ico(family, out_dir):
    from PIL import Image
    large, small = _to_pil(render_large(family)), _to_pil(render_small(family))
    # 크기별 아트: 48px+ = RAW, 32px- = R (.ico 는 엔트리마다 다른 이미지 허용)
    entries = [large.resize((s, s), Image.LANCZOS) for s in (256, 128, 64, 48)] + \
              [small.resize((s, s), Image.LANCZOS) for s in (32, 24, 16)]
    ico = os.path.join(out_dir, "app.ico")
    entries[0].save(ico, format="ICO", append_images=entries[1:],
                    sizes=[(im.width, im.height) for im in entries])
    print(f"OK -> {ico}  ({os.path.getsize(ico) / 1024:.0f} KB, "
          f"sizes: {[im.width for im in entries]})")


# (파일명, 픽셀크기) — iconutil 이 요구하는 고정 이름. 32/64 는 소형 아트(R).
_ICONSET = [("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
            ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
            ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
            ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
            ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)]


def make_icns(family, out_dir):
    from PIL import Image
    # 마스터를 최대 필요 해상도에서 벡터 렌더 → 각 크기는 LANCZOS 축소(.ico 와 같은 방식).
    large, small = _to_pil(render_large(family, 1024)), _to_pil(render_small(family, 512))
    icns = os.path.join(out_dir, "app.icns")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "app.iconset")
        os.makedirs(iconset)
        for name, px in _ICONSET:
            src = large if px >= 128 else small
            body = round(px * MAC_BODY) if px >= MAC_PAD_MIN else px
            im = src.resize((body, body), Image.LANCZOS)
            if body != px:                       # 사방 투명 여백(Dock 크기 정합)
                canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
                canvas.paste(im, ((px - body) // 2, (px - body) // 2))
                im = canvas
            im.save(os.path.join(iconset, name), format="PNG")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print(f"OK -> {icns}  ({os.path.getsize(icns) / 1024:.0f} KB, "
          f"sizes: {sorted({px for _, px in _ICONSET})})")


def make_badge(family, proj):
    """UI 안에서 쓰는 소형 아이콘 → assets/icons/edited.png (썸네일 '편집됨' 배지).

    타이틀바와 **같은 소형 아트**(R + 홀 3개)다 — 배지를 따로 디자인하지 않는다.
    48px 로 굽는다: 화면 표시는 16px 라 200% 배율에서도 축소만 하면 된다(업스케일 없음)."""
    from PIL import Image
    out = os.path.join(proj, "assets", "icons", "edited.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    _to_pil(render_small(family)).resize((48, 48), Image.LANCZOS).save(out, format="PNG")
    print(f"OK -> {out}  ({os.path.getsize(out)} B)")


def main():
    app = QGuiApplication([])                                        # noqa: F841 (렌더에 필요)
    family = _font_black()
    out_dir = os.path.join(PROJ, "icons")
    os.makedirs(out_dir, exist_ok=True)
    args = sys.argv[1:]
    want_ico = "--ico" in args or (not args and sys.platform == "win32")
    want_icns = "--icns" in args or (not args and sys.platform == "darwin")
    want_badge = "--badge" in args
    if not (want_ico or want_icns or want_badge):
        raise SystemExit("사용법: make_icon.py [--ico] [--icns] [--badge]")
    print(f"font: {family}")
    if want_ico:
        make_ico(family, out_dir)
    if want_icns:
        make_icns(family, out_dir)
    if want_badge:
        make_badge(family, PROJ)


if __name__ == "__main__":
    main()
