# -*- coding: utf-8 -*-
r"""앱 아이콘 생성 → icons/app.ico (멀티사이즈, 크기별 아트).

디자인(2026-08 확정): 다크 라운드 타일 + 위아래 스프로킷 홀 + 그레이딩 그러데이션 레터마크.
  - 256/128/64/48px: "RAW" + 홀 5개   (바탕화면·시작메뉴·탐색기)
  - 32/24/16px:      "R"   + 홀 3개   (작업표시줄 소형·창 타이틀바 — RAW 는 이 크기에서 안 읽힘)
홀은 **투명 컷아웃**(진짜 필름처럼 배경이 비친다 — 사용자 선택. 어두운 채움 안은 기각).
Windows 는 없는 크기를 가장 가까운 엔트리에서 스케일하므로 256 이 대형의 마스터 역할을 하고,
128/64/48 은 탐색기 중간 보기에서 스케일링 뭉개짐을 줄이는 보조 엔트리다.

실행:  .\.venv\Scripts\python.exe packaging\make_icon.py
의존: PySide6(렌더) + Pillow(ico 컨테이너). 둘 다 venv 에 있음. 앱 런타임과 무관한 빌드 도구.

⚠️아이콘을 재생성했으면 **build/ 캐시를 지우고** 패키징할 것 — PyInstaller 는 icon 파일의
  '내용' 변경을 감지하지 못해 exe 를 캐시에서 재사용한다(실측: 창 아이콘만 새것, exe 리소스는
  옛것인 반쪽 빌드가 나왔음). `Remove-Item -Recurse build` 후 build.ps1.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from PySide6.QtCore import QPointF, QRectF, Qt                      # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase,    # noqa: E402
                           QFontMetrics, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QPainterPath)

S = 256                     # 마스터 렌더 해상도(모든 크기는 여기서 LANCZOS 축소)


def _font_black():
    """오프스크린 플랫폼은 시스템 폰트 DB가 비어 있어 TTF 를 직접 등록해야 한다."""
    fid = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\seguibl.ttf")  # Segoe UI Black
    fams = QFontDatabase.applicationFontFamilies(fid)
    if not fams:
        raise RuntimeError("Segoe UI Black(seguibl.ttf) 로드 실패")
    return fams[0]


def _tile():
    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, S, S), 58, 58)
    g = QLinearGradient(0, 0, 0, S)
    g.setColorAt(0.0, QColor("#232630"))
    g.setColorAt(1.0, QColor("#15161a"))
    p.fillPath(path, QBrush(g))
    p.setClipPath(path)
    return img, p


def _holes(p, n, hw, hh, inset_y, rad):
    """위아래 스프로킷 홀 — 타일을 투명하게 뚫는다(CompositionMode_Clear). n 개 균등 배치."""
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 255))
    margin = 40
    span = S - margin * 2
    for i in range(n):
        cx = margin + span * (i + 0.5) / n
        for cy in (inset_y, S - inset_y):
            p.drawRoundedRect(QRectF(cx - hw / 2, cy - hh / 2, hw, hh), rad, rad)
    p.restore()


def _lettermark(p, family, text, px, tracking=0.0):
    """그림자 + 틸→앰버→오렌지 그러데이션 레터마크(가운데 정렬)."""
    f = QFont(family, 1)
    f.setWeight(QFont.Weight.Black)
    f.setPixelSize(px)
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
    fm = QFontMetrics(f)
    x = (S - fm.horizontalAdvance(text)) / 2
    y = S / 2 + fm.capHeight() / 2
    p.setPen(Qt.PenStyle.NoPen)
    for dy, a in ((6, 46), (3, 60)):
        sh = QPainterPath()
        sh.addText(x, y + dy, f, text)
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


def render_large(family):
    """RAW + 홀 5개 (48px 이상용 마스터)."""
    img, p = _tile()
    _lettermark(p, family, "RAW", 76, tracking=97)
    _holes(p, 5, hw=24, hh=17, inset_y=33, rad=5.5)
    p.end()
    return img


def render_small(family):
    """R + 홀 3개 (32px 이하용 마스터 — 축소돼도 홀이 점으로 살아남게 크고 적게)."""
    img, p = _tile()
    _lettermark(p, family, "R", 140)
    _holes(p, 3, hw=36, hh=24, inset_y=36, rad=8)
    p.end()
    return img


def main():
    app = QGuiApplication([])                                        # noqa: F841 (렌더에 필요)
    family = _font_black()
    out_dir = os.path.join(PROJ, "icons")
    os.makedirs(out_dir, exist_ok=True)

    import io
    from PIL import Image

    def to_pil(qimg):
        buf = qimg.bits().tobytes()
        return Image.frombuffer("RGBA", (qimg.width(), qimg.height()),
                                buf, "raw", "BGRA", 0, 1)

    large = to_pil(render_large(family).convertedTo(QImage.Format.Format_ARGB32))
    small = to_pil(render_small(family).convertedTo(QImage.Format.Format_ARGB32))

    # 크기별 아트: 48px+ = RAW, 32px- = R (.ico 는 엔트리마다 다른 이미지 허용)
    entries = [large.resize((s, s), Image.LANCZOS) for s in (256, 128, 64, 48)] + \
              [small.resize((s, s), Image.LANCZOS) for s in (32, 24, 16)]
    ico = os.path.join(out_dir, "app.ico")
    entries[0].save(ico, format="ICO", append_images=entries[1:],
                    sizes=[(im.width, im.height) for im in entries])
    print(f"OK -> {ico}  ({os.path.getsize(ico) / 1024:.0f} KB, "
          f"sizes: {[im.width for im in entries]})")


if __name__ == "__main__":
    main()
