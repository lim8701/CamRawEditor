# -*- coding: utf-8 -*-
"""RAW Peek — 디모자이크 **이전**(pre-demosaic) 센서 데이터 시각화.

앱의 일반 경로는 `raw.postprocess()` 로 디모자이크된 RGB 만 다룬다(`raw_loader._decode_native`).
이 모듈은 그 앞단, 즉 센서가 실제로 기록한 CFA 모자이크 자체를 그림으로 만든다. 진단·호기심용
읽기 전용 뷰이며 **export/룩 파이프라인과 접점이 전혀 없다**(새 셰이더 uniform·룩 키 0개).

의존성은 numpy + rawpy + QtGui 만(requirements.txt 범위). matplotlib/Pillow 를 쓰지 않는다.

⚠️`QT_QPA_PLATFORM=offscreen` 프로세스에서는 `QFontDatabase.families()` 가 0개라 여기서 그리는
  라벨이 전부 두부(tofu)로 나온다. 앱 본체는 native 플랫폼이라 문제 없지만, 헤드리스 검증
  스크립트에서 이 모듈의 그림을 뽑을 때는 offscreen 을 쓰지 말 것.
"""

import numpy as np
import rawpy
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from wb import baked_wb, linear_to_srgb, srgb_to_linear

# CFA 색 인덱스 -> 표시색 (LibRaw: 0=R 1=G 2=B 3=G2)
CFA_RGB = [(1.0, 0.15, 0.15), (0.15, 1.0, 0.20), (0.30, 0.45, 1.0), (0.55, 1.0, 0.35)]
CFA_NAME = ["R", "G", "B", "G2"]
BG = (24, 24, 26)

MIN_ZOOM, MAX_ZOOM = 1, 32
MODE_GRAY, MODE_CFA, MODE_PLANES, MODE_DEMOSAIC = range(4)
MODE_NAMES = ["Gray", "CFA", "Planes", "Demosaic"]


# ------------------------------------------------------------------ 유틸
def _bg_canvas(h, w):
    """`BG` 로 채운 (h,w,3) uint8 캔버스.

    ⚠️`canvas[:, :] = BG` 로 쓰지 말 것 — 3원소를 (H,W,3) 에 브로드캐스트하는 경로가 원소별로
      떨어져 **1580x998 에서 6.9ms** 다(Planes 전체 렌더 26ms 중 7ms 가 이 한 줄이었다).
      한 줄(w,3)을 만들어 행 단위 연속 복사로 뿌리면 **0.11ms** — 62배. 채널별 스칼라 대입도
      1.47ms 라 그만 못하다(스트라이드 3 write 3회).
    """
    a = np.empty((h, w, 3), np.uint8)
    a[:] = np.tile(np.array(BG, np.uint8), (w, 1))
    return a


def _to_qimage(a):
    """(H,W,3) uint8 -> QImage(RGB888). 버퍼 detach 를 위해 .copy() 필수."""
    # 이미 uint8 이면 astype 을 부르지 않는다(1600x1000 에서 astype 은 매 프레임 4.8MB 사본).
    a = np.ascontiguousarray(a if a.dtype == np.uint8 else a.astype(np.uint8))
    h, w, _ = a.shape
    return QImage(a.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _gamma8(v):
    """선형 0..1 -> sRGB 8bit (raw_loader/셰이더와 같은 wb.linear_to_srgb)."""
    return (np.clip(linear_to_srgb(v), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _box_down(a, factor):
    f = int(factor)
    if f <= 1:
        return np.asarray(a, np.float32)
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    x = a[:h, :w].astype(np.float32).reshape(h // f, f, w // f, f)
    return x.mean(axis=(1, 3))


def _nearest_up(a, k):
    if k <= 1:
        return a
    return np.repeat(np.repeat(a, k, axis=0), k, axis=1)


def _mono(size):
    f = QFont("Consolas", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f


def _wrap(texts, fm, avail_px):
    """라벨 줄들을 주어진 폭에 맞게 접는다(고정폭 폰트라 글자수로 계산).

    ★캔버스를 넓히는 대신 접는다. 넓히면 그림이 **뷰포트보다 커져** 화면 밖으로 밀리는데,
      이 뷰는 `fillMode: Pad` + centerIn 이라 넓어진 캔버스의 가운데만 보이게 된다 — 라벨이
      양쪽으로 잘려 나가고 모자이크도 어긋나 '모드를 바꿔도 안 바뀌는 것처럼' 보였다(실측:
      요청 1600px 에 CFA 1940px / Boundary 1863px).
    """
    ch = max(1.0, fm.horizontalAdvance("0"))
    ncol = max(20, int((avail_px - 20) / ch))
    out = []
    for t in texts:
        while len(t) > ncol:
            cut = t.rfind(" ", 0, ncol)         # 되도록 공백에서 접는다
            if cut < ncol // 2:
                cut = ncol
            out.append(t[:cut].rstrip())
            t = "  " + t[cut:].lstrip()         # 이어지는 줄은 살짝 들여쓴다
        out.append(t)
    return out


def _label_band(a, texts, size=15, width=None):
    """이미지 위에 어두운 라벨 밴드를 덧붙인다(픽셀을 텍스트로 덮지 않는다).

    ⚠️기본적으로 캔버스 폭은 **이미지 폭을 넘지 않는다** — 텍스트는 접는다(`_wrap` 주석).
    `width` 는 폭을 아는 호출부(우측 패널 도판)가 목표 폭을 지정할 때만 쓴다."""
    if not texts:
        return _to_qimage(a)
    font = _mono(size)
    fm = QFontMetrics(font)
    w = max(a.shape[1], int(width)) if width else a.shape[1]
    lines = _wrap(texts, fm, w)
    line = int(size * 1.55)
    hh = line * len(lines) + 12
    canvas = _bg_canvas(a.shape[0] + hh, w)
    canvas[hh:, :a.shape[1]] = a
    img = _to_qimage(canvas)
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QPen(QColor("#ececec")))
    for i, t in enumerate(lines):
        p.drawText(10, 6 + line * (i + 1) - 4, t)
    p.end()
    return img


# ★메인 뷰(mosaic/boundary/demosaic)는 캡션을 **이미지에 굽지 않는다.**
#   굽던 시절엔 "크롭 높이를 out_h 로 잡고 그 위에 밴드를 더한다"는 구조라 총 높이가 항상
#   뷰포트를 넘었고(상단 라벨 잘림), 밴드 높이를 미리 빼려 하면 **줄바꿈 여부를 알 수 없어**
#   좁은 폭에서 또 넘쳤다(예산 83px vs 실제 117px). 세 번 같은 자리를 고친 뒤 구조를 바꿨다:
#   캡션은 문자열로 돌려주고 **QML 이 고정 높이 밴드에 그린다** → 이미지 높이 예산이 정확해진다.
#   상세 수치는 어차피 우측 정보 패널(`rawPeekInfo`)에 있으므로 캡션은 짧게 유지한다.
#   `pattern_chart`/`histogram` 은 폭이 고정된 패널 도판이라 라벨을 계속 굽는다.


def _auto_gain(v, target=0.85, pct=99.0, cap=24.0):
    """표시용 게인 — raw 는 scene-linear 라 그냥 그리면 어둡다. 앱 자동노출과 같은 취지."""
    p = float(np.percentile(v, pct))
    return float(np.clip(target / max(p, 1e-4), 1.0, cap))


# ------------------------------------------------------------------ 상태
class RawPeek:
    """rawpy.imread 1회로 디모자이크 이전 배열 + 메타를 뽑아 보관한다.

    메모리: raw_image(uint16) + raw_colors(uint8) ≈ 26MP 기준 53MB + 26MB.
    오버레이를 닫을 때 참조를 버리면 해제된다(Controller.rawPeekClose).
    """

    def __init__(self, path: str):
        self.path = path
        with rawpy.imread(path) as r:
            self.full = r.raw_image.copy()               # 마스크(광학 블랙) 마진 포함
            self.vis = r.raw_image_visible.copy()
            # ★★색 맵은 `raw_colors_visible` 배열이 아니라 **`raw_color()`(LibRaw 의 COLOR()
            #   매크로)를 타일링**해서 만든다. X-T5/X-H2(40MP X-Trans 5 HR) 파일에서 그 배열이
            #   **쓰레기값**으로 나온다 — 6x6 에 0이 31개, 1·3·9·18 이 하나씩. 그러면 CFA_RGB[9]
            #   에서 IndexError 로 RAW Peek 이 죽는다(사용자 보고).
            #   ⚠️편집 경로는 무죄다 — LibRaw 는 이 파일을 제대로 디모자이크한다(프록시가 임베드
            #     JPEG 와 채널별 상관 0.85~0.97). 배열 노출만 어긋난 것이다.
            #   전 코퍼스 대조 결과 `raw_color()` 타일은 배열이 정상인 파일에서 **100% 동일**하고
            #   (X100V·X100VI 15장 / NEF / CR2 / ARW / DNG 3종), T5 에서만 22.22% 다르다
            #   (= 배열이 틀린 쪽). 그래서 조건 분기 없이 항상 이쪽을 쓴다.
            self.colors = self._color_map(r)
            # ⚠️`raw_pattern` **값**도 T5 에서 쓰레기다(위 `_color_map` 주석). 주기(모양)는
            #   그쪽에서 받고 **값은 방금 만든 색 맵의 좌상단 주기 블록**에서 가져온다.
            #   (`period`·`is_xtrans`·`pattern_chart` 가 이 배열을 쓴다)
            self.pattern = None
            if getattr(r, "raw_pattern", None) is not None:
                _p = max(1, int(r.raw_pattern.shape[0]))
                self.pattern = self.colors[:_p, :_p].copy()
            s = r.sizes
            self.top, self.left = int(s.top_margin), int(s.left_margin)
            # LibRaw 의 flip 코드. RAW Peek 의 다른 탭은 **센서 공간**을 그대로 보여 주지만
            # (그게 관찰 대상이다) Develop 탭은 프록시 렌더와 겹쳐야 하므로 회전을 맞춰야 한다.
            self.flip = int(s.flip)
            self.raw_h, self.raw_w = int(s.raw_height), int(s.raw_width)
            self.black = [int(v) for v in list(r.black_level_per_channel)[:4]]
            self.white = int(r.white_level)
            cwl = r.camera_white_level_per_channel
            self.cam_white = None if not cwl else [int(v) for v in cwl]
            self.color_desc = bytes(r.color_desc).decode("ascii", "replace")
            self.num_colors = int(r.num_colors)
            self.raw_type = str(r.raw_type).split(".")[-1]
            # ⚠️raw_loader.py 의 가드와 동형 — 제네릭/폰/드론 DNG 는 이 메타가 0/비유한일 수 있다.
            cam = np.asarray(r.camera_whitebalance, float)
            self.cam_wb = cam if np.all(np.isfinite(cam)) else np.zeros(4)
            day = np.asarray(r.daylight_whitebalance, float)
            self.day_wb = day if np.all(np.isfinite(day)) else np.zeros(4)

        self.period = 1 if self.pattern is None else int(self.pattern.shape[0])
        self.is_xtrans = self.pattern is not None and self.pattern.shape == (6, 6)
        self.present = sorted(int(c) for c in np.unique(self.colors))
        bl = np.array((self.black + [self.black[-1] if self.black else 0] * 4)[:4],
                      np.float32)
        self._bmap = bl[np.clip(self.colors, 0, 3)]
        self._span = max(float(self.white) - float(bl.mean()), 1.0)
        self._hist_img = None            # 오픈당 1회 계산 후 캐시
        self._pattern_img = None
        self._stats = None
        self._full_img = None            # zoom==1 전체 축소 모자이크 캐시(mode 별)
        self._mini_img = None            # 미니맵(전체 프레임 축소) — 오픈당 1회
        # LibRaw 디모자이크 **전체 프레임** 결과 캐시 {algo 이름: uint16 (H,W,3)}.
        # ★디코드는 크롭·줌과 무관한데 예전엔 줌을 한 칸 돌릴 때마다 다시 했다(실측 1.2~2.0s,
        #   전체 시간의 80~95%) — 휠을 못 쓸 수준이었다. 한 번 받아 두면 크롭은 즉시다.
        #   비용: 26MP X-Trans 156MB / 12MP Bayer 2알고 148MB. Demosaic 모드를 처음 쓸 때만
        #   채우고(lazy), 오버레이를 닫으면 st 와 함께 해제된다.
        self._dm_cache = {}
        # 마지막으로 그린 크롭 사각형(센서 픽셀 x,y,w,h) — 미니맵의 현재 위치 표시용.
        # ★QML 이 zoom 과 뷰 크기로 추정하면 모드마다 틀린다(Planes 는 폭을 색 수로 나누고,
        #   Demosaic 는 정사각 크롭이며, Boundary 는 크롭 개념이 없다) → 실제 값을 보고한다.
        self.last_rect = None
        # 마지막으로 그린 배율(표시 픽셀 / 센서 픽셀). 드래그 환산에 쓴다.
        # ★모드마다 다르다 — Demosaic 은 패널이 화면의 1/n 이라 요청 zoom 과 다르고(캡도 걸린다),
        #   전체 보기는 축소(<1)다. QML 이 zoom 으로 환산하면 드래그가 n 배 느려진다.
        self.last_scale = 1.0

    # ---- 정규화: v = (raw - black[c]) / (white - black) -------------
    @staticmethod
    def _color_map(r):
        """`raw_color()` 를 패턴 주기만큼 찍어 visible 전체로 타일링한다.

        파이썬 호출은 주기²번(X-Trans 36 / 베이어 4)뿐이라 비용이 없다. 좌상단은 **visible
        원점**(top/left 마진)에 맞춘다 — `raw_color()` 는 절대 좌표를 받는다.
        """
        s = r.sizes
        p = int(r.raw_pattern.shape[0]) if getattr(r, "raw_pattern", None) is not None else 2
        p = max(1, p)
        top, left = int(s.top_margin), int(s.left_margin)
        tile = np.array([[r.raw_color(top + y, left + x) for x in range(p)]
                         for y in range(p)], np.uint8)
        h, w = int(s.height), int(s.width)
        return np.tile(tile, (h // p + 1, w // p + 1))[:h, :w].copy()

    def norm_vis(self):
        return np.clip((self.vis.astype(np.float32) - self._bmap) / self._span, 0.0, 1.0)

    def _norm_crop(self, y, x, h, w):
        sub = self.vis[y:y + h, x:x + w].astype(np.float32)
        return np.clip((sub - self._bmap[y:y + h, x:x + w]) / self._span, 0.0, 1.0)

    @property
    def vis_h(self):
        return int(self.vis.shape[0])

    @property
    def vis_w(self):
        return int(self.vis.shape[1])

    # ---------------------------------------------------------- 통계
    def stats(self):
        """CFA 색별 (중앙값, 중앙값-black, 최대, 클립%) — 히스토그램/정보 패널 공용."""
        if self._stats is None:
            out = []
            for ci in self.present:
                vals = self.vis[self.colors == ci]
                med = float(np.median(vals))
                bl = float(self.black[min(ci, len(self.black) - 1)]) if self.black else 0.0
                out.append((ci, med, med - bl, int(vals.max()),
                            100.0 * float((vals >= self.white * 0.999).mean()),
                            float((self.colors == ci).mean())))
            self._stats = out
        return self._stats

    def margins(self):
        """마스크 마진(l,t,r,b) 과 각 띠의 실측 성격. 광학 블랙인지 단순 패딩인지 판정."""
        vh, vw = self.vis.shape
        dead_r = self.raw_w - (self.left + vw)
        dead_b = self.raw_h - (self.top + vh)
        rows = []
        cand = [("left", self.full[self.top:self.top + vh, :self.left], self.left),
                ("top", self.full[:self.top, self.left:self.left + vw], self.top),
                ("right", self.full[self.top:self.top + vh, self.left + vw:], dead_r),
                ("bottom", self.full[self.top + vh:, self.left:self.left + vw], dead_b)]
        bl = float(np.mean(self.black)) if self.black else 0.0
        for name, s, n in cand:
            if n <= 0 or s.size == 0:
                continue
            f = s.astype(np.float32)
            zeros = 100.0 * float((f == 0).mean())
            nz = f[f > 0]
            mu = float(nz.mean()) if nz.size else 0.0
            sd = float(nz.std()) if nz.size else 0.0
            # ★두 가지를 **따로** 판정한다. 예전에 하나로 묶었더니 D90(평평한 광학 블랙이지만
            #   pedestal 255 ≠ 보고된 black_level 0)이 "광학 블랙 아님"으로 나왔다 — 오답이다.
            #   flat  : 0 패딩이 거의 없고 sd 가 작다 → 덮인 픽셀(읽기 노이즈만)
            #   agrees: 그 평균이 메타의 black_level 과 맞는다
            flat = zeros < 5.0 and sd < max(40.0, 0.01 * self._span)
            agrees = abs(mu - bl) < max(8.0, 0.005 * self._span)
            rows.append((name, n, zeros, mu, sd, flat, agrees))
        return (self.left, self.top, dead_r, dead_b), rows

    def margin_verdict(self, zeros, sd, flat, agrees):
        if not flat:
            return "padding / leftover, not covered pixels" if zeros >= 5.0 \
                else "not flat - not covered pixels"
        return ("optical black, matches black_level" if agrees
                else "optical black, but pedestal disagrees with black_level")


def _wb_norm(wb) -> str:
    """WB 배수를 G=1 기준으로 정규화한 표시 문자열(파일 간 스케일 차 흡수)."""
    a = np.asarray(wb, float)[:3]
    if a.size < 3 or not np.all(np.isfinite(a)) or a[1] <= 0:
        return "n/a"
    a = a / a[1]
    return "  ".join(f"{v:.3f}" for v in a)


def probe(path: str) -> dict:
    """오버레이 정보 패널용 메타 요약(무거운 배열 없이 dict 만)."""
    st = RawPeek(path)
    return summary(st)


def summary(st: "RawPeek") -> dict:
    (ml, mt, mr, mb), rows = st.margins()
    kind = ("X-Trans" if st.is_xtrans
            else "Bayer" if st.period == 2
            else "no CFA" if st.pattern is None else f"{st.period}x{st.period}")
    obs_max = int(st.full.max())
    bits = int(np.ceil(np.log2(max(obs_max, 2))))
    ratio = []
    for ci, _med, above, _mx, _clip, frac in st.stats():
        ratio.append(f"{CFA_NAME[ci]} {100.0 * frac:.1f}%")
    lines = [
        f"pattern      {st.period}x{st.period}  {kind}",
        f"colours      {' '.join(CFA_NAME[c] for c in st.present)}"
        f"   ({st.color_desc}, num_colors={st.num_colors})",
        f"sampling     {'  '.join(ratio)}",
        f"raw size     {st.raw_w} x {st.raw_h}",
        f"visible      {st.vis_w} x {st.vis_h}",
        f"margins      left {ml}  top {mt}  right {mr}  bottom {mb}",
        f"black level  {st.black}",
        f"white level  {st.white}"
        + (f"   camera {st.cam_white[0]}" if st.cam_white else ""),
        f"observed max {obs_max}  (~{bits} bit)",
        # ⚠️camera_whitebalance 의 스케일은 파일마다 다르다(Nikon 475/256/319 vs DNG 1.78/1.0/1.72)
        #   → G=1 로 정규화해서 보여준다. 그래야 파일 간 비교가 된다.
        f"camera WB    {_wb_norm(st.cam_wb)}",
        f"daylight WB  {_wb_norm(st.day_wb)}",
        f"raw type     {st.raw_type}",
    ]
    for name, n, zeros, mu, sd, flat, agrees in rows:
        lines.append(f"margin {name:6s} {n:4d}px  zeros {zeros:5.1f}%  "
                     f"mean {mu:7.1f}  sd {sd:6.2f}  "
                     f"-> {st.margin_verdict(zeros, sd, flat, agrees)}")
    return {"text": "\n".join(lines), "isXTrans": st.is_xtrans, "period": st.period,
            "visW": st.vis_w, "visH": st.vis_h, "kind": kind}


# ------------------------------------------------------------- 모자이크 뷰
def mosaic(st: RawPeek, mode: int, cx: float, cy: float, zoom: int,
           out_w: int, out_h: int, gain: bool = True):
    """현재 팬(cx,cy = visible 안의 정규화 중심 0..1)·줌으로 모자이크 타일 1장.

    반환: `(QImage, [캡션줄])` — ★캡션은 이미지에 굽지 않는다(파일 상단 주석 참조).

    ★크롭을 **파이썬에서 잘라** nearest 확대하므로 거대 텍스처를 만들지 않는다
      (26MP 를 32× 로 올린 텍스처는 존재할 수 없다). 결과는 요청 뷰포트를 넘지 않는다.
    """
    req_w, req_h = out_w, out_h          # ★캐시 키는 **보정 전** 요청값으로 만든다(_full_key)
    out_w, out_h = _panel_fit(st, mode, out_w, out_h)
    zoom = int(np.clip(zoom, MIN_ZOOM, MAX_ZOOM))

    if zoom <= 1:
        # 전체 보기 — 정수 박스 축소(모자이크가 평균화되는 것 자체가 관찰 포인트다)
        key = _full_key(st, mode, req_w, req_h, gain)
        # ⚠️`last_rect`/`last_scale` 은 **캐시 적중보다 먼저** 세운다 — 뒤에 두면 캐시로 돌아온
        #   전체 보기가 직전 줌의 값을 그대로 물고 있어(실측: 줌 8 → 1 복귀 후에도
        #   rect=(1794,1200,150,100) scale=8.0) 미니맵이 안 사라지고 드래그가 48배 느린 채로
        #   화면은 안 움직인다. 축소 배수 f 는 `norm_vis()` 와 같은 모양(vis_h×vis_w)에서 나오므로
        #   배열을 만들지 않고 계산한다.
        f = max(1, int(np.ceil(max(st.vis_h / out_h, st.vis_w / out_w))))
        st.last_rect = (0, 0, st.vis_w, st.vis_h)      # 전체 보기
        st.last_scale = 1.0 / f
        if _cache_hit(st, key):
            return st._full_img[1]           # (QImage, 캡션줄) 튜플을 그대로 캐시한다
        out = _render(st, _box_down(st.norm_vis(), f), _colors_down(st, f), mode, 1,
                      note=f"whole frame, box/{f}", gain=gain)
        st._full_img = (key, out)
        return out

    # 1:1 이상 — 화면에 들어갈 픽셀 수만 잘라낸다
    # ⚠️ceil 로 잡으면 cw*zoom 이 요청 폭을 넘어(1608 > 1600) 뷰포트 밖으로 밀린다 → 내림.
    cw = max(st.period, int(out_w // zoom))
    ch = max(st.period, int(out_h // zoom))
    cw, ch = min(cw, st.vis_w), min(ch, st.vis_h)
    p = st.period
    x = int(np.clip(cx * st.vis_w - cw / 2, 0, st.vis_w - cw)) // p * p
    y = int(np.clip(cy * st.vis_h - ch / 2, 0, st.vis_h - ch)) // p * p
    v = st._norm_crop(y, x, ch, cw)
    c = st.colors[y:y + ch, x:x + cw]
    st.last_rect = (x, y, cw, ch)
    st.last_scale = float(zoom)
    return _render(st, v, c, mode, zoom, note=f"{cw}x{ch} @ ({x},{y})  {zoom}x",
                   grid=True, gain=gain)


def _panel_fit(st, mode: int, out_w: int, out_h: int):
    """요청 뷰포트 → 실제로 그릴 크기. Planes 는 색당 패널 1장을 가로로 늘어놓으므로 크롭 폭을
    색 수로 나눠야 뷰포트에 들어간다(안 나누면 3~4배 넓은 그림이 나와 화면 밖으로 나간다).
    높이는 패널 제목줄(`_render_planes` 의 top)만큼 뺀다."""
    out_w, out_h = max(16, int(out_w)), max(16, int(out_h))
    if mode == MODE_PLANES:
        n = max(1, len(st.present))
        out_w = max(64, (out_w - 10 * (n - 1)) // n)
        out_h = max(64, out_h - 22)
    return out_w, out_h


def _full_key(st, mode: int, out_w: int, out_h: int, gain: bool):
    """zoom<=1 전체보기 캐시 키. **저장(`mosaic`)과 조회(`is_heavy`)가 이 함수 하나를 쓴다.**

    ★게인 플래그가 키에 들어간다 — 빠지면 토글해도 캐시된 그림이 그대로 온다.
    ⚠️예전엔 두 곳이 키를 각자 조립했다가 어긋나 **캐시가 한 번도 안 맞았다**: `mosaic` 은
      4-튜플(gain 포함)로 저장하는데 `is_heavy` 는 3-튜플로 조회했고, Planes 의 `out_h` 보정도
      조회 쪽에만 빠져 있었다. 그림은 맞으니 눈에 안 띄지만 **전체보기가 캐시가 있어도 매번
      워커로 갔다**(busy 배지 + 한 턴 지연, 실측 0.25~0.38s). 인자는 반드시 **보정 전** 요청
      크기로 넘길 것 — `_panel_fit` 을 여기서 부르므로 두 번 적용하면 또 어긋난다.
    """
    w, h = _panel_fit(st, mode, out_w, out_h)
    return (mode, w, h, bool(gain))


def _cache_hit(st, key):
    return st._full_img is not None and st._full_img[0] == key


def render(st: RawPeek, mode: int, cx: float, cy: float, zoom: int,
           out_w: int, out_h: int, progress=None, gain: bool = True):
    """모드 디스패치의 **단일 진실원**. 반환: `(QImage, [캡션줄])`.

    ★동기 경로(main 의 `rawPeekView`)와 워커 경로가 각자 디스패치를 적고 있었는데, 캐싱으로
      Demosaic 이 동기로 넘어간 순간 **동기 쪽에 MODE_DEMOSAIC 분기가 없어** `mosaic()` 로
      떨어졌다 → mode 3 이 `_render` 의 CFA 분기로 흘러 **패널 하나만 CFA 처럼** 나왔다
      (사용자 보고: "드래그시 CFA 모드처럼 하나만 나오네"). 그래서 디스패치를 여기 하나로 합쳤다.
    """
    if mode == MODE_DEMOSAIC:
        return demosaic_steps(st, cx, cy, max(2, zoom), out_w, out_h,
                              progress=progress, gain=gain)
    return mosaic(st, mode, cx, cy, zoom, out_w, out_h, gain=gain)


def is_heavy(st: RawPeek, mode: int, zoom: int, out_w: int, out_h: int,
             cx: float = 0.5, cy: float = 0.5, gain: bool = True) -> bool:
    """이 요청이 워커로 보내야 할 만큼 무거운가 — 판정을 여기 한 곳에 둔다.

    ★캐시 키는 **`_full_key` 하나**로 만든다. 여기서 따로 조립하면 `mosaic()` 이 저장한 키와
      어긋나 히트를 통째로 놓친다(실제로 그랬다 — `_full_key` 주석 참조).
    ⚠️그래서 `gain` 도 받아야 한다. 호출부가 안 넘기면 기본값 True 가 잡히므로, 게인을 끈
      상태에서 다시 키가 어긋난다.
    실측(X100V 26MP): 디모자이크 비교 1.13s / 전체보기 0.25~0.38s / 그 외 22~100ms.
    """
    if mode == MODE_DEMOSAIC:
        # 캐시가 비었거나 **팬으로 창을 벗어난** 렌더만 무겁다(LibRaw 전체 디코드).
        return not demosaic_cached(st, demosaic_crop(st, cx, cy, zoom, out_w, out_h))
    if zoom > 1:
        return False
    return not _cache_hit(st, _full_key(st, mode, out_w, out_h, gain))


def _colors_down(st, f):
    """축소 뷰용 색 인덱스 — 축소하면 색 구분이 의미 없으므로 최근접 샘플만."""
    return st.colors[::f, ::f][:max(1, st.colors.shape[0] // f),
                               :max(1, st.colors.shape[1] // f)]


def _gain_note(on: bool, g: float) -> str:
    """캡션 꼬리 — 밝기를 올렸는지 그대로인지 **화면에 밝혀 둔다.**

    이 값이 있어서 "왜 어둡나 / 왜 밝나" 를 되묻지 않을 수 있다. 끈 상태에서는 센서가 적어 둔
    밝기 그대로이고, Develop 탭 머리 그림과 같은 상태다.
    """
    return f"display gain x{g:.1f}" if on else "display gain off (as recorded)"


def _render(st, v, c, mode, zoom, note="", grid=False, gain=True):
    g = _auto_gain(v) if gain else 1.0
    lin = np.clip(v * g, 0.0, 1.0)
    h, w = lin.shape
    # 색 배열이 값 배열과 어긋나면(축소 경로) 잘라 맞춘다
    c = c[:h, :w]
    if c.shape != lin.shape:
        ch, cw = c.shape
        pad = np.zeros_like(lin, np.uint8)
        pad[:ch, :cw] = c
        c = pad

    if mode == MODE_GRAY:
        a = _nearest_up(_gamma8(lin), zoom)
        rgb = np.dstack([a, a, a])
        texts = [f"Gray — sensor mosaic, no demosaic",
                 f"{note}   {_gain_note(gain, g)}"]
    elif mode == MODE_PLANES:
        return _render_planes(st, lin, c, zoom, g, note, gain)
    else:                                   # MODE_CFA
        col = np.zeros((h, w, 3), np.float32)
        for ci in st.present:
            m = (c == ci)
            for k in range(3):
                col[..., k] += m * lin * CFA_RGB[ci][k]
        rgb = _nearest_up(_gamma8(col), zoom)
        kind = "X-Trans" if st.is_xtrans else "Bayer" if st.period == 2 else "CFA"
        texts = [f"CFA — every pixel in its own filter colour   {kind} "
                 f"{st.period}x{st.period}",
                 f"{note}   {_gain_note(gain, g)}"]

    if grid and st.period >= 3 and st.period * zoom >= 12 and mode == MODE_CFA:
        # 패턴 반복 유닛 경계 — Bayer(2px 주기)는 너무 촘촘해 방해만 되므로 제외.
        # ⚠️QPainter 로 그리지 않는다 — 선 218개(1600x1000)에 **19~21ms** 였다. 알파를 빼도,
        #   `drawLines` 로 묶어도, ARGB32 로 바꿔도 같았다(픽셀당 ~90ns = 래스터 경로가 느린 것).
        #   같은 그림을 numpy 로 블렌드하면 ~2ms 고, 줌>1 은 **동기 렌더**라(is_heavy) 이 차이가
        #   드래그 체감에 그대로 실린다. 결과는 QPainter 판과 화소 단위로 같다(실측).
        step = st.period * zoom
        a = 55.0 / 255.0                       # 흰 선 알파 55/255 — 예전 QPen 과 같은 값
        rgb[::step, :] = rgb[::step, :] * (1.0 - a) + (255.0 * a + 0.5)
        rgb[:, ::step] = rgb[:, ::step] * (1.0 - a) + (255.0 * a + 0.5)
    img = _to_qimage(rgb)
    return img, texts


def _render_planes(st, lin, c, zoom, g, note, gain=True):
    """색별 평면 3(4)장을 가로로 — 표본 밀도 비교("나머지는 진짜로 없는 데이터")."""
    h, w = lin.shape
    gap = 10
    panels, titles = [], []
    for ci in st.present:
        m = (c == ci)
        col = np.zeros((h, w, 3), np.float32)
        for k in range(3):
            col[..., k] = m * lin * CFA_RGB[ci][k]
        panels.append(_nearest_up(_gamma8(col), zoom))
        titles.append(f"{CFA_NAME[ci]}  {100.0 * m.mean():.1f}%")
    ph, pw = panels[0].shape[:2]
    top = 22
    canvas = _bg_canvas(ph + top, pw * len(panels) + gap * (len(panels) - 1))
    xs = []
    for i, pan in enumerate(panels):
        x0 = i * (pw + gap)
        canvas[top:top + ph, x0:x0 + pw] = pan
        xs.append(x0)
    img = _to_qimage(canvas)
    pnt = QPainter(img)
    pnt.setFont(_mono(14))
    pnt.setPen(QPen(QColor("#dddddd")))
    for x0, t in zip(xs, titles):
        pnt.drawText(x0 + 4, 16, t)
    pnt.end()
    return img, ["Planes — each colour's own samples (the rest is genuinely missing)",
                 f"{note}   {_gain_note(gain, g)}"]


# --------------------------------------------------------- 디모자이크 비교
# 이 패널의 목적: `docs/raw_demosaic.md` 의 **정책 결정을 내 사진에서 검증**하는 계측기.
#   현재 정책 = 프록시 항상 LINEAR / Bayer export 만 AHD / X-Trans 는 양쪽 LINEAR.
#   그 문서의 "추후 재검토 트리거"에 DCB·DHT 가 적혀 있어 후보로 함께 세운다.
# ★예전엔 "naive box fill" 을 패널로 뒀는데 **어떤 결정에도 기여하지 않았다**(파이프라인 후보가
#   아니고 실측 집계도 LINEAR 와 거의 같았다) → 실제 후보 비교로 교체했다.

# 후보 집합 — **export 가 실제로 쓰는 것만** 세운다(`raw_loader._export_demosaic` 과 같아야 한다).
#   X-Trans·Bayer 모두 export=AHD(X-Trans 에선 LibRaw 가 Markesteijn 3-pass 로 실행 — rawpy
#   라벨과 실제 알고리즘이 다르다, docs/raw_demosaic.md 매핑 절) → 패널은 "모자이크 vs export".
#   프록시가 쓰는 LINEAR 는 후보에서 뺐다(2026-08-29 사용자 결정) — 프록시는 2560 축소라 이
#   100% 비교와 체감이 무관하고, 빼면 첫 렌더가 LINEAR 디코드만큼 빨라지고 패널이 커진다.
#   ⚠️예전엔 4종(LINEAR/VNG/PPG/AHD 또는 LINEAR/AHD/DCB/DHT)을 세워 '정책 검증 계측기' 로 썼는데
#     X-Trans 첫 렌더가 **13s** 였고(종당 1.1~3.6s) 눈으로 판별이 안 됐다 → 사용자 결정으로
#     축소. 측정 표와 판단 경위는 `docs/raw_peek.md`·`docs/raw_demosaic.md` 에 있다 —
#     되살릴 때 그 표를 먼저 볼 것.
_CANDS_XTRANS = ("AHD",)
_CANDS_BAYER = ("AHD",)


def demosaic_candidates(st):
    """이 사진에서 비교할 후보. ⚠️`_app_choice` 의 정책 표시와 짝이다."""
    return _CANDS_BAYER if st.period == 2 else _CANDS_XTRANS

# 창 단위 캐시. 디코드는 전체 프레임밖에 안 되지만 **보관은 창만** 한다 —
#   전체를 담으면 26MP 에서 종당 156MB 다. 2048px 창이면 종당 25MB 이고, 크롭이 최대
#   ~397px(패널 2칸·zoom 2 기준)이라 디코드 중심에서 **±825px 팬까지** 미스가 안 난다
#   (여유 = (2048−크롭변)/2 — 고배율일수록 크롭이 작아 여유가 ±975px 까지 늘어난다).
DM_WINDOW = 2048


def _app_choice(st, name):
    """앱이 실제로 쓰는 것 — 라벨에 표시한다. `raw_loader._export_demosaic` 과 같아야 한다."""
    # Bayer·X-Trans 공통: 프록시 LINEAR / export AHD(X-Trans 에선 Markesteijn 3-pass).
    if name == "LINEAR":
        return "app: proxy"
    return "app: export" if name == "AHD" else ""


def _cand_label(st, name):
    """표시용 이름. X-Trans 에선 rawpy 라벨과 **실제 실행되는 알고리즘이 다르다** — LibRaw 가
    quality>2(AHD/DCB/DHT/AAHD)를 Markesteijn 3-pass, quality 2(PPG)를 1-pass 로 실행한다
    (docs/raw_demosaic.md 매핑 절). 캐시 키·rawpy 호출은 원래 이름을 쓰고 표시만 바꾼다."""
    if getattr(st, "is_xtrans", False):
        if name in ("AHD", "DCB", "DHT", "AAHD"):
            return f"Markesteijn 3-pass ({name})"
        if name == "PPG":
            return f"Markesteijn 1-pass ({name})"
    return name


def _dm_covers(ent, rect):
    """캐시된 창이 이 크롭을 덮는가."""
    arr, wx, wy, fy, fx = ent
    nx, ny = int(rect[0] * fx), int(rect[1] * fy)
    nw, nh = max(1, int(rect[2] * fx)), max(1, int(rect[3] * fy))
    return (wx <= nx and wy <= ny
            and nx + nw <= wx + arr.shape[1] and ny + nh <= wy + arr.shape[0])


def _dm_get(st, name, need):
    """후보 `name` 의 디코드 결과에서 `need`(센서 x,y,w,h)를 덮는 창을 얻는다.

    반환 `(arr, wx, wy, fy, fx)` — arr 은 postprocess 출력 좌표계의 창, (wx,wy) 가 그 원점.
    캐시 미스면 전체를 디코드하고 창만 잘라 보관한다(실측 1.1~3.6s/종).
    """
    ent = st._dm_cache.get(name)
    if ent is False:
        return None
    if ent is not None and _dm_covers(ent, need):
        return ent
    try:
        with rawpy.imread(st.path) as r:
            # ★export 계약과 같은 디코드(`raw_loader._decode_native` 와 동일 파라미터) — WB 는
            #   디모자이크 **전에** 곱해지므로(LibRaw scale_colors) 유니티 WB 로 디코드하면 export
            #   가 실행하는 디모자이크와 입력부터 다르다(Markesteijn 은 채널 균형에 민감). 예전
            #   유니티 WB+선형 표시는 평탄부 노이즈 결을 과장해 올바른 정책(Mark3)을 의심하게
            #   만들었다(2026-08-29 실측, docs/raw_demosaic.md). 유일한 의도적 차이는
            #   user_flip=0 — 크롭 창 매핑이 센서 좌표에 의존한다(회전만 다르고 픽셀은 동일).
            cam_xyz = np.array(r.rgb_xyz_matrix)[:3, :3]
            ref = np.array(r.daylight_whitebalance, dtype=float)[:3]
            ref = ref / ref[1] if (ref[1] > 0 and np.all(np.isfinite(ref))) else np.ones(3)
            rgb = r.postprocess(demosaic_algorithm=getattr(rawpy.DemosaicAlgorithm, name),
                                output_color=rawpy.ColorSpace.raw,
                                user_wb=baked_wb(cam_xyz, ref),
                                output_bps=16, no_auto_bright=True,
                                gamma=(2.4, 12.92), user_flip=0,
                                highlight_mode=rawpy.HighlightMode.Clip)
    except Exception:
        st._dm_cache[name] = False           # 실패도 기억한다(매번 재시도하지 않게)
        return None
    # postprocess 출력이 visible 과 크기가 다를 수 있어(후지 crop) 상대좌표로 맞춘다
    fy = rgb.shape[0] / st.vis_h
    fx = rgb.shape[1] / st.vis_w
    ww = min(int(DM_WINDOW * fx), rgb.shape[1])
    wh = min(int(DM_WINDOW * fy), rgb.shape[0])
    cxp = int((need[0] + need[2] / 2) * fx)
    cyp = int((need[1] + need[3] / 2) * fy)
    wx = int(np.clip(cxp - ww // 2, 0, max(0, rgb.shape[1] - ww)))
    wy = int(np.clip(cyp - wh // 2, 0, max(0, rgb.shape[0] - wh)))
    ent = (np.ascontiguousarray(rgb[wy:wy + wh, wx:wx + ww]), wx, wy, fy, fx)
    st._dm_cache[name] = ent
    return ent


def _dm_cache_bytes(st):
    return sum(e[0].nbytes for e in st._dm_cache.values() if e is not False)


def demosaic_cached(st, rect=None) -> bool:
    """후보 전부가 캐시돼 있고 `rect` 가 그 창 안에 있는가 — 동기/워커 판정에 쓴다.
    ⚠️팬으로 창을 벗어나면 재디코드가 필요하므로 False 여야 한다(그때는 워커로)."""
    for name in demosaic_candidates(st):
        ent = st._dm_cache.get(name)
        if ent is None:
            return False
        if ent is False:
            continue                         # 못 쓰는 후보는 다시 시도하지 않는다
        if rect is not None and not _dm_covers(ent, rect):
            return False
    return True


# Demosaic 은 전체 보기(zoom 1)라는 개념이 없다 — 최소 2배.
DEMOSAIC_MIN_ZOOM = 2


def _demosaic_slot(st, out_w: int, out_h: int):
    """(패널수, 간격, 제목줄높이, 슬롯변) — 패널 하나가 차지할 정사각 칸 크기.
    ★`demosaic_steps`·`zoom_range`·`demosaic_crop` 이 **같은 계산**을 봐야 UI 상한과 캐시
      히트 판정이 실제 렌더와 어긋나지 않는다."""
    n_panels = 1 + len(demosaic_candidates(st))  # 모자이크(입력) + 후보들
    gap, title_h = 12, 22
    avail_w = max(64, int(out_w) - gap * (n_panels - 1))
    slot = max(48, min(avail_w // n_panels, max(48, int(out_h) - title_h - 4)))
    return n_panels, gap, title_h, slot


def _demosaic_side_k(st, slot: int, zoom_req: int):
    """(크롭 변, 실제 확대율). 크롭은 패턴 4주기 아래로 못 내려가고, 확대율은 요청 배율이
    슬롯에 들어가면 그대로 쓴다(min 없이 slot//side 만 쓰면 Bayer 32x 가 33x 로 넘어갔다)."""
    min_side = max(8, st.period * 4)
    side = int(min(max(min_side, slot // zoom_req), st.vis_w, st.vis_h))
    return side, min(zoom_req, max(1, slot // side))


def zoom_range(st, mode: int, out_w: int, out_h: int):
    """이 모드·뷰포트에서 **실제로 서로 다른 결과가 나오는** 줌 범위 (min, max).

    ★UI 가 1..32 를 그대로 쓰면 휠이 무동작하거나 같은 상태가 두 번 나온다(사용자 보고):
      (1) Demosaic 은 내부에서 zoom 을 2 로 올려 잡으므로 zoom 1 과 2 가 **같은 그림**인데
          `canPan` 만 달라 "2x 인데 패닝 안 되는 상태" 가 하나 더 생겼다.
      (2) 크롭 하한 때문에 X-Trans 는 16x·32x 가 둘 다 14x 로 그려져 휠 두 칸이 무동작이었다.
    """
    if mode != MODE_DEMOSAIC:
        return MIN_ZOOM, MAX_ZOOM
    _n, _gap, _th, slot = _demosaic_slot(st, out_w, out_h)
    zmax, prev_k = DEMOSAIC_MIN_ZOOM, None
    z = DEMOSAIC_MIN_ZOOM
    while z <= MAX_ZOOM:
        _side, k = _demosaic_side_k(st, slot, z)
        if prev_k is not None and k == prev_k:
            break                         # 더 올려도 같은 배율 → 여기가 상한
        zmax, prev_k = z, k
        z *= 2
    return DEMOSAIC_MIN_ZOOM, zmax


def demosaic_crop(st, cx: float, cy: float, zoom: int, out_w: int, out_h: int):
    """이 요청이 쓸 크롭 사각형(센서 x,y,w,h)만 미리 계산 — `is_heavy` 가 창 히트를 보려면
    렌더 **전에** 알아야 한다. `demosaic_steps` 와 같은 식이어야 한다."""
    zoom_req = int(np.clip(zoom, DEMOSAIC_MIN_ZOOM, MAX_ZOOM))
    _n, _gap, _th, slot = _demosaic_slot(st, out_w, out_h)
    side, _k = _demosaic_side_k(st, slot, zoom_req)
    p = st.period
    x = int(np.clip(cx * st.vis_w - side / 2, 0, st.vis_w - side)) // p * p
    y = int(np.clip(cy * st.vis_h - side / 2, 0, st.vis_h - side)) // p * p
    return x, y, side, side


def demosaic_steps(st, cx: float, cy: float, zoom: int,
                   out_w: int, out_h: int, progress=None, gain: bool = True):
    """같은 크롭을 **실제 후보 알고리즘들**로 현상해 나란히 비교(+ 입력 모자이크).

    ★패널 표시 크기는 줌과 무관하게 고정이다 — 슬롯을 먼저 정하고 그 안에 넣는다(`_slots`).
      예전에 `크롭 x 줌` 에서 파생시켰더니 줌마다 폭이 흔들리고, 크롭 하한에 걸리는 고배율에서
      이미지가 1080->600px 로 쪼그라들었다(사용자 보고).
    ⚠️**캐시가 비었거나 팬으로 창을 벗어난 첫 호출만** 무겁다(AHD 1종: X-Trans ~3.7s /
      Bayer ~1.3s) — 그때는 워커에서 부를 것(`is_heavy` 가 `demosaic_cached` 로 판정). 이후는 수십 ms.
    progress: 선택적 콜백 `(done, total, name)` — 오래 걸리는 첫 디코드의 진행 표시용.
    """
    zoom_req = int(np.clip(zoom, DEMOSAIC_MIN_ZOOM, MAX_ZOOM))
    n_panels, gap, title_h, slot = _demosaic_slot(st, out_w, out_h)
    side, k = _demosaic_side_k(st, slot, zoom_req)
    x, y, _sw, _sh = demosaic_crop(st, cx, cy, zoom_req, out_w, out_h)

    st.last_rect = (x, y, side, side)
    st.last_scale = float(k)
    v = st._norm_crop(y, x, side, side)
    c = st.colors[y:y + side, x:x + side]
    g = _auto_gain(v) if gain else 1.0
    lin = np.clip(v * g, 0.0, 1.0)

    mos = np.zeros((side, side, 3), np.float32)
    for ci in st.present:
        m = (c == ci)
        for ch in range(3):
            mos[..., ch] += m * lin * CFA_RGB[ci][ch]
    panels = [_nearest_up(_gamma8(mos), k)]
    titles = ["CFA mosaic (input)"]

    cands = demosaic_candidates(st)
    total = len(cands)
    cand_gain = None                 # 후보 간 **동일** 표시 게인(첫 후보에서 1회 산출 — 공정 비교)
    for i, name in enumerate(cands):
        label = _cand_label(st, name)    # X-Trans 에선 실제 알고리즘 이름(Markesteijn)으로 표시
        if progress is not None:
            progress(i, total, label)
        ent = _dm_get(st, name, (x, y, side, side))
        blank = np.full((max(8, slot // 2), max(8, slot // 2), 3), BG, np.uint8)
        if ent is None:
            panels.append(blank)
            titles.append(f"{label} - unavailable")
            continue
        arr, wx, wy, fy, fx = ent
        sy, sx = int(y * fy) - wy, int(x * fx) - wx
        hh, ww = max(4, int(side * fy)), max(4, int(side * fx))
        sub = arr[sy:sy + hh, sx:sx + ww]
        if sub.shape[0] < 4 or sub.shape[1] < 4:
            panels.append(blank)
            titles.append(f"{label} - out of window")
            continue
        # 디코드가 이미 감마 인코딩(export 계약, `_dm_get`) — 표시 게인은 상단 토글을 따른다.
        # 켜면 선형화 후 `_auto_gain`(예전 p99→0.9 상시 게인은 어두운 크롭에서 노이즈 결을
        # 과장했다), 끄면 export 디코드 그대로(as recorded).
        sub = sub.astype(np.float32) / 65535.0
        kk = max(1, slot // max(sub.shape[1], 1))
        if gain:
            lin_sub = srgb_to_linear(sub)
            if cand_gain is None:
                cand_gain = _auto_gain(lin_sub)
            img8 = _gamma8(np.clip(lin_sub * cand_gain, 0.0, 1.0))
        else:
            img8 = (np.clip(sub, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        panels.append(_nearest_up(img8, kk))
        note = _app_choice(st, name)
        titles.append(label + (f"  [{note}]" if note else ""))
    if progress is not None:
        progress(total, total, "")

    cap = (f"Demosaic - mosaic vs {'/'.join(_cand_label(st, n) for n in cands)}, "
           f"same {side}x{side} crop "
           f"@ ({x},{y}), {'X-Trans' if st.is_xtrans else 'Bayer'}   {k}x"
           f"   {_gain_note(gain, g)}")
    if k != zoom_req:
        cap += (f"   (requested {zoom_req}x; crop floor is "
                f"{max(8, st.period * 4)}px = 4 pattern units)")
    return _slots(panels, titles, slot, gap, title_h), [cap]


def _slots(panels, titles, slot, gap, title_h):
    """패널들을 **고정 크기 슬롯**에 가운데 정렬로 배치 — 총 크기가 줌과 무관하게 일정해진다."""
    n = len(panels)
    w = slot * n + gap * (n - 1)
    canvas = _bg_canvas(slot + title_h, w)
    xs = []
    for i, pan in enumerate(panels):
        x0 = i * (slot + gap)
        ph, pw = pan.shape[:2]
        ph, pw = min(ph, slot), min(pw, slot)
        oy = title_h + (slot - ph) // 2
        ox = x0 + (slot - pw) // 2
        canvas[oy:oy + ph, ox:ox + pw] = pan[:ph, :pw]
        xs.append(x0)
    img = _to_qimage(canvas)
    pnt = QPainter(img)
    pnt.setFont(_mono(13))
    pnt.setPen(QPen(QColor("#cccccc")))
    for x0, t in zip(xs, titles):
        pnt.drawText(x0 + 4, 16, t)
    pnt.end()
    return img


# --------------------------------------------- Develop 애니메이션용 모자이크
# LibRaw flip 코드 → np.rot90 의 k. ★**추측이 아니라 실측**이다: 모자이크 gray 를 네 방향으로
# 돌려 프록시 휘도와 상관계수를 재 가장 높은 것을 골랐다(0/3/5 각각 +0.84 / +0.79 / +0.84~0.87,
# 반대 방향은 −0.27). flip=6 만 샘플이 없어 dcraw 대칭으로 둔 값이다(모르는 코드는 회전 없음).
_ROT_K = {0: 0, 3: 2, 5: 1, 6: 3}

# 격자선 — ★**표시 픽셀 단위의 가독성 값이고 CFA 패턴 유닛이 아니다.**
# ⚠️곱셈으로 어둡게 하던 것을 **고정값 쪽으로 블렌드**하도록 바꿨다. 머리 그림에서 표시 게인을
#   없앤 뒤로는(아래 `develop_mosaic` 주석) 평균 코드가 6~13 이라 어둡게 곱하면 선이 사라진다. 전체 프레임 축소본에서
# 출력 1px 은 광전자 우물 f개(실측 f=7~12)에 해당하므로 패턴 유닛(X-Trans 6, 베이어 2)은
# 1px 도 안 된다 — 격자로 그릴 수 있는 크기가 아니다. 그래서 "센서는 격자다" 를 말해 주는
# 순수한 표시 보조선으로 두고, 간격을 정수 px 로 고정해 굵기가 일정하게 나오도록 한다.
GRID_PX = 8
GRID_LEVEL = 0.30    # 격자선이 수렴하는 밝기
GRID_MIX = 0.5       # 그쪽으로 섞는 비율


def develop_pair(st: RawPeek, out_w: int, out_h: int, gain: bool = False):
    """Develop 머리 그림 **두 장**(gray, cfa)을 한 번에 만든다.

    ★`develop_mosaic` 을 두 번 부르면 `norm_vis()`(40MP float32 = 160MB 전이 배열)와
      `_box_down` 전체 프레임 패스가 **두 번** 돈다. 실측으로 그 한 패스가 0.25~0.38s 라
      (`is_heavy` 가 워커로 보내는 기준이 그것이다) GUI 스레드가 그만큼 멈춘다. 두 그림은
      표본 위치·게인·격자까지 공유하므로 한 번에 만드는 것이 자연스럽다.
    """
    return _develop_render(st, out_w, out_h, both=True, gain=gain)


def develop_mosaic(st: RawPeek, out_w: int, out_h: int, gray: bool = False,
                   gain: bool = False):
    """Develop 탭 머리 프레임 — **전체 프레임** 모자이크(라벨 없음, 뷰포트에 맞춤).

    `gray=True` 면 CFA 색을 칠하지 않고 **센서가 기록한 밝기 하나**만 그린다
    (Develop 애니메이션의 첫 단계 "Sensor readout").

    ★두 그림은 축소 방식이 **다르다.** 서로 교차 페이드하므로 크기·밝기는 같아야 하지만
      성격은 각 단계가 말하려는 것이 다르다:

    ★축소 방식이 다르다:
      - `gray` = **박스 평균**(깨끗한 값). 여기서 nearest 로 뽑으면 R/G/B 우물의 값 차이가
        그대로 남아 **그냥 노이즈로 보인다** — 지터를 규칙적으로 줘도(격자 무늬), 해시로
        흩어도(모래알) 둘 다 기각됐다(사용자 보고 2회, 후보 도판으로 확인).
      - `cfa` = **nearest 표본**. 박스 평균으로 줄이면 R/G/B 가 섞여 그냥 사진처럼 보이고
        "픽셀마다 색 하나뿐" 이 전달되지 않는다.

    ★대신 **격자선을 그린다**(두 그림 모두). 센서가 격자라는 것은 값의 반점이 아니라 선으로
      보여주는 게 읽힌다. ⚠️이 선은 **표시 보조선이고 CFA 패턴 유닛이 아니다** — 전체 프레임
      축소본에서 출력 1px 이 광전자 우물 f개라 패턴 유닛은 1px 도 안 된다. 디모자이크 단계에서
      두 그림이 걷히면 격자도 함께 사라진다 — 그게 디모자이크가 하는 일이다.

    ★★**표시 게인을 곱하지 않는다.** 셰이더의 t=0 은 `norm`(센서값을 포화로 정규화한 값)
      그대로다 — 프록시 native linear 가 `norm x 자동노출게인` 이고 t=0 이 그 게인을
      되돌리기 때문이다. 그래서 머리 그림도 `norm` 을 그대로 써야 이어진다.
      ⚠️예전에는 보이도록 `_auto_gain`(p99->0.85)을 곱했는데, 그러면 머리 세 칸이 계단처럼
        어두워져 **"디모자이크가 어둡게 만들었다"로 읽힌다.** 실측(DSCF2354, 게인 5.24배):
        이음새가 **48.8코드**였고 게인을 빼면 **5.0코드**로 닫힌다(셰이더 t=0 예상 17.8 vs 12.7).
      ⚠️대가는 어둡다는 것이다(gray 평균 12.7 / CFA 6.0). 그게 센서가 적어 둔 실제 밝기이고,
        "보이는 것보다 변해 온 과정을 보여준다"가 이 화면의 목적이다(사용자 결정).

    ⚠️감마를 걸지 않는다(다른 RAW Peek 탭의 `_gamma8` 과 다르다). 셰이더가 `filmicMix=0`
      (선형 그대로)으로 시작하므로 여기 sRGB 인코딩을 걸면 밝기가 튀어 연결이 끊긴다.
      선형끼리 이어야 filmic 단계가 "선형 → 눈이 보는 밝기" 라는 제 몫을 한다.
    """
    return _develop_render(st, out_w, out_h, gray=gray, gain=gain)


def _develop_render(st: RawPeek, out_w: int, out_h: int, gray: bool = False,
                    both: bool = False, gain: bool = False):
    """`gain` 기본값이 `mosaic()`(True)과 반대인 것은 의도다 — 이 머리 그림은 셰이더 t=0 과
    같은 밝기여야 이어지고, 올릴지는 UI 의 체크박스가 정한다."""
    """`develop_mosaic` / `develop_pair` 의 공용 본체. `both=True` 면 (gray, cfa) 튜플."""
    out_w, out_h = max(16, int(out_w)), max(16, int(out_h))
    # 회전이 걸리는 사진은 out_w/out_h 가 **프록시 방향**으로 들어온다 — 센서 공간 기준으로
    # 바꿔서 배율을 잡아야 요청한 사각형을 채운다.
    k = _ROT_K.get(st.flip, 0)
    if k % 2 == 1:
        out_w, out_h = out_h, out_w
    vh, vw = st.vis.shape
    f = max(1, int(np.ceil(max(vh / out_h, vw / out_w))))
    ny, nx = max(1, vh // f), max(1, vw // f)

    norm = st.norm_vis()
    base = _box_down(norm, f)[:ny, :nx]          # 두 그림 공통
    # 표시 게인 — 기본은 1.0(적힌 그대로). ⚠️켜면 머리가 밝아지는 대신 셰이더 렌더로 넘어가는
    #   이음새가 벌어진다(실측 DSCF2354: 2.7코드 -> 48.8코드). 그게 이 토글의 의미다.
    g = _auto_gain(base) if gain else 1.0

    # ★★블록 안에서 어느 픽셀을 뽑느냐가 곧 어느 색을 뽑느냐다. stride 를 그냥 f 로 쓰면
    #   X-Trans(주기 6)에서 **모든 표본이 같은 위상**에 떨어져 화면이 전부 초록이 됐다
    #   (실측: G 가 55.6% 인데 stride==period 면 사실상 100% G).
    #   ★위상은 **정수 해시**로 뽑는다. 후보를 재서 골랐다 — 초록 표본 여부의 공간 자기상관
    #     (가로·세로·대각 두 방향 × lag 1..7 중 최악):
    #       행/열 1차 램프    **1.000**  ← 완전 주기적. 이게 "조악한 격자 무늬"였다(사용자 보고)
    #       2D 선형 혼합       0.494     ← 평면이라 대각선 줄이 남는다
    #       XOR 해시           **0.011**  ← 채택 (잡음 수준)
    #     결정론적이라 프레임마다 깜빡이지 않고, 뽑힌 색 분포는 실제 CFA 비율과 같다.
    ii = np.arange(ny, dtype=np.int64)[:, None]
    jj = np.arange(nx, dtype=np.int64)[None, :]
    hsh = (ii * 73856093) ^ (jj * 19349663)          # 공간 해시 상용 소수
    ys = np.clip(ii * f + hsh % f, 0, vh - 1)
    xs = np.clip(jj * f + (hsh >> 8) % f, 0, vw - 1)
    def _gray():
        return np.repeat(np.clip(base * g, 0.0, 1.0)[..., None], 3, axis=2)

    def _cfa():
        lin = np.clip(norm[ys, xs] * g, 0.0, 1.0)
        col = st.colors[ys, xs]
        out = np.zeros(lin.shape + (3,), np.float32)
        for ci in st.present:
            m = (col == ci)
            for ch in range(3):
                out[..., ch] += m * lin * CFA_RGB[ci][ch]
        return out

    if both:
        return tuple(_finish_dev(x, out_w, out_h, k) for x in (_gray(), _cfa()))
    rgb = _gray() if gray else _cfa()

    return _finish_dev(rgb, out_w, out_h, k)


def _finish_dev(rgb, out_w, out_h, k):
    """정확한 크기로 맞추고 → 격자선 → 회전 → QImage. (두 그림이 공유하는 마무리)"""
    # ★★요청 크기와 **정확히 같게** 맞춘다(nearest). ny x nx 는 축소배율 f 의 정수 나눗셈이라
    #   요청과 몇 px 어긋나는데(실측 595x892 vs 600x900), 그러면 QML 이 1.009 배로 늘려 그리고
    #   **1px 격자선이 어떤 곳은 2px, 어떤 곳은 사라진다**(세로 사진에서 굵기가 들쭉날쭉하다는
    #   사용자 보고). 크기가 같으면 1:1 로 그려져 선 굵기가 일정하다.
    if (rgb.shape[0], rgb.shape[1]) != (out_h, out_w):
        yi = np.minimum((np.arange(out_h) * rgb.shape[0]) // out_h, rgb.shape[0] - 1)
        xi = np.minimum((np.arange(out_w) * rgb.shape[1]) // out_w, rgb.shape[1] - 1)
        rgb = rgb[np.ix_(yi, xi)]

    # 센서 격자선 — 정수 px 간격이라 굵기가 일정하다(위 GRID_PX 주석 참조).
    # ⚠️어두운 곳/밝은 곳 양쪽에서 보이도록 고정 밝기 쪽으로 섞는다(곱셈은 어두운 그림에서
    #   선이 사라진다 — 머리 그림 평균이 6~13코드다).
    rgb[::GRID_PX, :, :] += (GRID_LEVEL - rgb[::GRID_PX, :, :]) * GRID_MIX
    rgb[:, ::GRID_PX, :] += (GRID_LEVEL - rgb[:, ::GRID_PX, :]) * GRID_MIX

    # ★프록시와 **같은 방향**으로 돌린다. 안 돌리면 세로 사진에서 가로 그림이 나와
    #   레터박스가 투명하게 남고 그 틈으로 아래 셰이더가 비친다("뒷 배경이 겹쳐 보인다").
    if k:
        rgb = np.rot90(rgb, k, axes=(0, 1))
    return _to_qimage(np.ascontiguousarray((np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5)
                                           .astype(np.uint8)))

# ------------------------------------------------------- 기본 팬 위치
def default_center(st: RawPeek):
    """오픈 시 쓸 기본 팬 중심 (cx, cy) — 정규화 0..1.

    ★화면 중앙은 하늘·벽 같은 평탄면일 때가 많고, 그러면 Demosaic 전후 비교나 CFA 확대가
      볼 것 없는 곳에서 시작한다. 미니맵용 축소본을 그대로 재활용해 블록별로
      `표시공간 표준편차 x 중간톤 가중 x 클립 페널티` 로 점수를 매겨 가장 좋은 지점을 고른다.
      추가 디코드가 없다(정규화 배열 재계산뿐, 실측 0.45~1.2s).
    """
    lin = st.norm_vis()
    f = max(1, int(np.ceil(max(lin.shape[0] / 240, lin.shape[1] / 240))))
    disp = np.asarray(linear_to_srgb(lin * _auto_gain(lin)), np.float32)
    small = _box_down(disp, f)
    clipm = _box_down((lin > 0.98).astype(np.float32), f)
    k = 4                                    # 블록 = 축소본 4x4
    bh, bw = small.shape[0] // k, small.shape[1] // k
    if bh < 3 or bw < 3:
        return 0.5, 0.5
    blk = small[:bh * k, :bw * k].reshape(bh, k, bw, k)
    clp = clipm[:bh * k, :bw * k].reshape(bh, k, bw, k).mean(axis=(1, 3))
    mean = blk.mean(axis=(1, 3))
    tone = np.exp(-((mean - 0.58) ** 2) / (2 * 0.20 ** 2))
    score = blk.std(axis=(1, 3)) * tone * (1.0 - np.clip(clp * 4.0, 0.0, 0.95))
    m = np.zeros_like(score, bool)            # 가장자리는 크롭이 잘려 편향되므로 중앙 70% 만
    m[int(bh * .15):int(bh * .85) + 1, int(bw * .15):int(bw * .85) + 1] = True
    score = np.where(m, score, -1.0)
    by, bx = np.unravel_index(int(np.argmax(score)), score.shape)
    return float((bx + 0.5) / bw), float((by + 0.5) / bh)


# ---------------------------------------------------------------- 미니맵
def minimap(st: RawPeek, max_edge: int = 240) -> QImage:
    """전체 visible 프레임의 작은 그레이스케일 썸네일 — 확대 중 현재 위치 표시용.
    사진당 1회만 만들면 되므로(팬/줌과 무관) 오픈 시 워커에서 계산해 캐시한다."""
    if st._mini_img is not None:
        return st._mini_img
    v = st.norm_vis()
    f = max(1, int(np.ceil(max(v.shape[0] / max_edge, v.shape[1] / max_edge))))
    small = _box_down(v, f)
    a = _gamma8(small * _auto_gain(small))
    st._mini_img = _to_qimage(np.dstack([a, a, a]))
    return st._mini_img


# ------------------------------------------------------------- 패턴 차트
def pattern_chart(st: RawPeek, side: int = 260) -> QImage:
    """raw_pattern 반복 유닛을 색 + 라벨 격자로. 오픈당 1회 계산 후 캐시."""
    if st._pattern_img is not None:
        return st._pattern_img
    if st.pattern is None:
        img = _label_band(np.full((60, side, 3), BG, np.uint8), ["no CFA pattern"])
        st._pattern_img = img
        return img
    n = st.period
    cell = max(8, side // n)
    grid = cell * n
    canvas = _bg_canvas(grid, grid)
    for iy in range(n):
        for ix in range(n):
            ci = int(st.pattern[iy, ix])
            col = _gamma8(np.array(CFA_RGB[ci], np.float32) * 0.85)
            canvas[iy * cell + 1:(iy + 1) * cell - 1,
                   ix * cell + 1:(ix + 1) * cell - 1] = col
    counts = {int(c): int((st.pattern == c).sum()) for c in np.unique(st.pattern)}
    tot = n * n
    kind = "X-Trans" if st.is_xtrans else "Bayer" if n == 2 else ""
    img = _label_band(canvas, [
        f"raw_pattern {n}x{n}  {kind}",
        "  ".join(f"{CFA_NAME[c]} {counts[c]}/{tot} ({100.0 * counts[c] / tot:.1f}%)"
                  for c in sorted(counts)),
    ], size=13, width=440)
    off = img.height() - grid
    pnt = QPainter(img)
    pnt.setFont(_mono(max(9, cell // 3)))
    pnt.setPen(QPen(QColor(0, 0, 0, 190)))
    for iy in range(n):
        for ix in range(n):
            ci = int(st.pattern[iy, ix])
            pnt.drawText(QRect(ix * cell, off + iy * cell, cell, cell),
                         Qt.AlignmentFlag.AlignCenter, CFA_NAME[ci])
    pnt.end()
    st._pattern_img = img
    return img


# ------------------------------------------------------------- 히스토그램
def histogram(st: RawPeek, w: int = 460, lane: int = 74) -> QImage:
    """CFA 색별 raw 코드 히스토그램. ★채널을 겹쳐 그리면 색이 섞여 판독 불가 →
    채널마다 자기 레인을 준다. x 축 공유(0..white_level). 오픈당 1회 계산 후 캐시."""
    if st._hist_img is not None:
        return st._hist_img
    nb = st.white + 1
    n = len(st.present)
    h = lane * n + 22
    canvas = _bg_canvas(h, w)
    edges = np.linspace(0, nb, w + 1).astype(np.int64)
    for li, ci in enumerate(st.present):
        vals = st.vis[st.colors == ci]
        hist = np.bincount(vals.astype(np.int64), minlength=nb)[:nb].astype(np.float64)
        cols = np.array([hist[edges[i]:max(edges[i + 1], edges[i] + 1)].max()
                         for i in range(w)])              # max-pool: 스파이크 보존
        lg = np.log10(cols + 1.0)
        lg = lg / max(lg.max(), 1e-6)
        col = _gamma8(np.array(CFA_RGB[ci], np.float32) * 0.8)
        y0, y1 = li * lane + 16, (li + 1) * lane - 4
        canvas[y1:y1 + 1, :] = (70, 70, 74)
        for xi in range(w):
            canvas[y1 - int(lg[xi] * (y1 - y0)):y1, xi] = col
    img = _to_qimage(canvas)
    pnt = QPainter(img)
    pnt.setFont(_mono(12))
    for li, ci in enumerate(st.present):
        pnt.setPen(QPen(QColor("#ffffff")))
        pnt.drawText(4, li * lane + 13, CFA_NAME[ci])
    bl = float(np.mean(st.black)) if st.black else 0.0
    marks = [(bl, f"black {int(bl)}", "#ffffff"),
             (float(st.white), f"white {st.white}", "#ffffff")]
    if st.cam_white:
        marks.append((float(np.mean(st.cam_white)),
                      f"cam {int(np.mean(st.cam_white))}", "#ffd070"))
    for xv, name, hexcol in marks:
        xp = int(np.clip(xv / nb * w, 0, w - 1))
        pen = QPen(QColor(hexcol))
        pen.setStyle(Qt.PenStyle.DashLine)
        pnt.setPen(pen)
        pnt.drawLine(xp, 2, xp, h - 22)
        pnt.setPen(QPen(QColor(hexcol)))
        pnt.drawText(max(2, min(xp + 4, w - 96)), h - 6, name)
    pnt.end()
    lines = ["raw code histogram per CFA colour (log y)"]
    for ci, med, above, mx, clip, _frac in st.stats():
        lines.append(f"{CFA_NAME[ci]:2s} med {med:6.0f}  -blk {above:6.0f}"
                     f"  max {mx:5d}  clip {clip:.2f}%")
    img = _label_band(_qimage_to_np(img), lines, size=13, width=440)
    st._hist_img = img
    return img


def _qimage_to_np(img):
    """QImage -> (H,W,3) uint8. ⚠️행이 4바이트 정렬이라 bytesPerLine 슬라이스 필수."""
    im = img.convertToFormat(QImage.Format.Format_RGB888)
    h, w = im.height(), im.width()
    return (np.frombuffer(im.constBits(), np.uint8)
            .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy())
