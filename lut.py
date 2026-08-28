"""표준 .cube 3D LUT 로더 + 셰이더용 2D 아틀라스 변환.

3D LUT 를 셰이더에서 쓰려면 sampler3D 가 필요한데, Qt Quick ShaderEffect 는
2D 텍스처(Image)만 property 로 받는다. 그래서 3D LUT 를 가로로 N 개 타일을
이어 붙인 2D 아틀라스(폭 N*N, 높이 N)로 펴서 넘기고, 셰이더에서 수동으로
트라이리니어 보간한다.

아틀라스 좌표 규약 (셰이더와 반드시 일치):
    blue = b 슬라이스를 b 번째 타일에 배치
    픽셀 (x = b*N + r,  y = g) 위치에 LUT[r, g, b] 값

사용자가 추가한 .cube (`user:<파일명>` 키)는 **번들 `luts/` 가 아니라 사용자 데이터 폴더**에
둔다 — 설치 폴더는 쓰기 권한이 없고 업데이트마다 새로 풀린다. 규약·함정은 스탬프 사용자 폰트
(`date_stamp.user_fonts_dir` 이하)와 같은 것을 그대로 따른다.
"""

import os
import shutil
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage


def load_cube(path: str, strict_domain: bool = False):
    """Adobe .cube 파일을 (N, N, N, 3) float32 배열과 크기 N 으로 반환.

    데이터 순서는 red 가 가장 빠르게 변함: index = r + g*N + b*N*N

    `strict_domain=True` 면 비표준 DOMAIN 을 경고가 아니라 **거부**한다(가져오기 경로 전용) —
    번들 LUT 을 읽는 기존 호출부의 거동은 바뀌지 않는다.
    """
    size = None
    dom_min, dom_max = None, None
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:  # BOM 있는 익스포터 대응(첫 키워드 보존)
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            key = parts[0].upper()
            if key == "LUT_3D_SIZE":
                if len(parts) < 2:
                    raise ValueError("LUT_3D_SIZE has no value.")
                try:
                    size = int(parts[1])
                except ValueError:
                    raise ValueError(f"LUT_3D_SIZE is not an integer: {parts[1]!r}") from None
            elif key == "DOMAIN_MIN":
                dom_min = [float(x) for x in s.split()[1:4]]
            elif key == "DOMAIN_MAX":
                dom_max = [float(x) for x in s.split()[1:4]]
            elif key in ("TITLE", "LUT_1D_SIZE"):
                continue
            else:
                parts = s.split()
                if len(parts) == 3:
                    try:
                        rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                    except ValueError:
                        continue
    if size is None:
        raise ValueError("No LUT_3D_SIZE found — 1D-only LUTs are not supported.")
    # ⚠️크기 범위를 검사한다. `LUT_3D_SIZE 1` 은 파싱을 통과해 **사진 전체를 단색으로** 만들고,
    #   `0` 은 아래 인덱싱에서 numpy 내부 오류 문구가 나오는데 가져오기 경로가 그 문구를 그대로
    #   UI 배너에 띄운다. 상한 256 은 방어선(256³×3×4B ≈ 201MB) — 64 초과는 가져올 때 리샘플된다.
    if not 2 <= size <= 256:
        raise ValueError(f"Unsupported LUT_3D_SIZE {size} (expected 2..256).")
    # 파이프라인/셰이더는 입력을 [0,1]로 가정하고 LUT 를 샘플한다. 비표준 도메인
    # (예: DOMAIN_MAX 4 4 4)은 조용히 잘못된 색을 내므로 최소한 경고한다(미지원).
    if (dom_min is not None and any(abs(v) > 1e-6 for v in dom_min)) or \
       (dom_max is not None and any(abs(v - 1.0) > 1e-6 for v in dom_max)):
        if strict_domain:
            # Log 입력(S-Log3/V-Log/Cineon) LUT 이 거의 전부 여기 걸린다. 우리가 LUT 에 넣는 값은
            # 이미 filmic() 을 거친 display-referred 라, 도메인을 맞춰줘도 인코딩이 안 맞는다 —
            # 조용히 틀린 색을 내는 대신 가져오기 자체를 막는다.
            raise ValueError(f"Non-standard DOMAIN (min={dom_min} max={dom_max}). "
                             f"Only [0,1]-input LUTs are supported (log-input LUTs are not).")
        print(f"[lut] ⚠️비표준 DOMAIN(min={dom_min} max={dom_max}) — [0,1] 로 가정해 로드"
              f"(색이 어긋날 수 있음): {path}")

    data = np.asarray(rows, dtype=np.float32)
    if data.shape[0] != size ** 3:
        raise ValueError(
            f"Entry count mismatch: {data.shape[0]} != {size ** 3} (LUT_3D_SIZE {size})."
        )

    idx = np.arange(size ** 3)
    r = idx % size
    g = (idx // size) % size
    b = idx // (size * size)
    lut = np.zeros((size, size, size, 3), dtype=np.float32)
    lut[r, g, b, :] = data
    return lut, size


def atlas_qimage(lut: np.ndarray, size: int) -> QImage:
    """(N,N,N,3) LUT 를 폭 N*N, 높이 N 의 RGB888 아틀라스 QImage 로 변환."""
    n = size
    atlas = np.zeros((n, n * n, 3), dtype=np.uint8)
    vals = np.clip(lut, 0.0, 1.0)
    vals = np.rint(vals * 255.0).astype(np.uint8)
    for b in range(n):
        # lut[r, g, b] -> atlas[y=g, x=b*n + r]  (r,g 축 transpose)
        tile = vals[:, :, b, :]                 # [r, g, 3]
        atlas[:, b * n:(b + 1) * n, :] = np.transpose(tile, (1, 0, 2))

    atlas = np.ascontiguousarray(atlas)
    h, w, _ = atlas.shape
    return QImage(atlas.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


# ---------- 사용자가 추가한 .cube (`user:<파일명>`) ----------
# 규약·함정은 스탬프 사용자 폰트(`date_stamp.py:206` 이하)와 같은 것을 그대로 따른다.
# 다른 점 하나: 폰트는 Qt 가 파일을 잠그므로 삭제 전 등록 해제가 필수였지만, .cube 는
# 우리가 읽고 바로 닫으므로 그냥 지우면 된다.

USER_PREFIX = "user:"   # 사용자 LUT 키 접두사. 번들 카탈로그 키(provia…)와 절대 겹치지 않는다.
# 아틀라스 폭이 N² px 라 GPU 2D 텍스처 한도(D3D11 등 통상 16384)가 실질 상한이다
# (N=144 → 20736px 로 초과). 다만 한도에 딱 붙이지 않고 여유를 둔다: 96² = 9216px.
# ★**64 가 아니라 96 인 이유**: 65³ 는 DaVinci Resolve 의 기본 export 크기라 인터넷 LUT 에서
#   가장 흔한 값 중 하나인데, 65² = 4225px 는 한도에서 한참 멀다. 상한을 64 로 두면 그 흔한
#   파일이 이유 없이 리샘플되고 "Resampled" 경고까지 뜬다.
MAX_N = 96


def is_user(key) -> bool:
    """사용자가 추가한 LUT 인가. 보정 노출 게이트와 Remove 버튼 활성 판정에 쓴다."""
    return str(key).startswith(USER_PREFIX)


def user_luts_dir(create=False):
    """사용자가 추가한 .cube 폴더. 설치 폴더가 아니라 사용자 데이터 폴더에 두는 이유는
    models/fonts 와 같다(설치 폴더는 쓰기 권한이 없고 업데이트마다 새로 풀린다).
    app_dirs 는 지연 임포트.
    ⚠️`create` 는 **추가할 때만** True — 읽기 경로에서 mkdir 를 돌리면 사용자가 폴더를
    지워도 즉시 되살아난다(`date_stamp.user_fonts_dir` 와 같은 규칙)."""
    import app_dirs
    d = Path(app_dirs.user_data_path("luts"))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def user_lut_keys():
    """추가된 사용자 LUT 의 키 목록(정렬). 키 = `user:<파일명>`."""
    try:
        return sorted(USER_PREFIX + f.name for f in user_luts_dir().iterdir()
                      if f.suffix.lower() == ".cube" and f.is_file())
    except Exception:
        return []


def lut_path(key, bundled_dir=None):
    """LUT 키 → .cube 경로. 사용자 LUT 은 사용자 폴더, 나머지는 `bundled_dir`.
    ⚠️경로는 항상 **파일명만** 이어붙인다 — 키 문자열을 그대로 경로로 쓰면
    `user:../../x.cube` 같은 값이 폴더 밖을 가리킬 수 있다(`date_stamp.font_path` 와 같은 가드)."""
    k = str(key)
    if k.startswith(USER_PREFIX):
        name = os.path.basename(k[len(USER_PREFIX):])
        # 빈 이름이면 폴더 자체를 가리키게 된다 — 파일이 아닌 이름으로 바꿔 폴백을 타게 한다.
        return user_luts_dir() / (name or "_")
    if bundled_dir is None:
        raise ValueError(f"bundled_dir is required for a bundled LUT key: {key}")
    return Path(bundled_dir) / f"{os.path.basename(k)}.cube"



def _resample(lut, n_src, n_dst):
    """N_src³ → N_dst³ 트라이리니어 리샘플. 트라이리니어는 **축 분리 가능**이라 축별 3회로 끝난다."""
    g = np.linspace(0.0, 1.0, n_dst, dtype=np.float32) * (n_src - 1)
    i0 = np.floor(g).astype(np.intp)
    i1 = np.minimum(i0 + 1, n_src - 1)
    f = (g - i0).astype(np.float32)
    out = lut[i0] * (1.0 - f)[:, None, None, None] + lut[i1] * f[:, None, None, None]
    out = out[:, i0] * (1.0 - f)[None, :, None, None] + out[:, i1] * f[None, :, None, None]
    out = out[:, :, i0] * (1.0 - f)[None, None, :, None] + out[:, :, i1] * f[None, None, :, None]
    return np.ascontiguousarray(out, dtype=np.float32)


def _write_cube(path, lut, n, title=""):
    """(N,N,N,3) LUT → .cube. 데이터 순서는 `load_cube` 와 같은 red-fastest
    (index = r + g*N + b*N*N) — `lut[r,g,b]` 를 `[b,g,r]` 로 transpose 하면 그 순서가 된다."""
    flat = np.clip(lut, 0.0, 1.0).transpose(2, 1, 0, 3).reshape(-1, 3)
    with open(path, "w", encoding="utf-8") as f:
        if title:
            f.write(f'TITLE "{title}"\n')
        f.write(f"LUT_3D_SIZE {n}\n")
        for r, g, b in flat:
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")


# `image://lut/<key>` 로 실려 가므로 URL 을 깨는 문자는 파일명에서 뺀다. 실측(오프스크린
# 엔진에 Image 를 태워 프로바이더가 받은 image_id 를 찍음):
#   'user:my look.cube'          -> 'user:my look.cube'            (콜론·공백은 그대로 도착)
#   'user:100% pro (v2).cube'    -> 'user:100%25 pro (v2).cube'    (**% 는 인코딩된 채 도착**)
# `?` 는 `requestImage` 가 쿼리스트링으로 잘라내고 `#` 은 프래그먼트로 잘린다. 그래서 우리가
# 만드는 파일명에서는 이 셋을 제거한다(수동으로 폴더에 넣은 파일은 `requestImage` 의 unquote
# 가 % 만 되살린다 — 그쪽은 사용자가 이름을 고칠 수 있는 경로다).
_UNSAFE = "#?%:/" + chr(92)      # chr(92)=역슬래시 (이스케이프 혼선 방지)


def _safe_name(name: str) -> str:
    """사용자 LUT 파일명을 프로바이더 URL 에 안전한 형태로. 표시명이 되는 값이라 공백·괄호·
    한글은 그대로 둔다(실측에서 문제없음) — 위 `_UNSAFE` 와 제어문자만 뺀다."""
    stem = os.path.basename(name)
    if stem.lower().endswith(".cube"):
        stem = stem[:-5]
    out = "".join(" " if c in _UNSAFE else c for c in stem if ord(c) >= 32)
    out = " ".join(out.split()).strip(". ")      # 공백 접기 + 윈도우가 조용히 지우는 끝 점/공백
    return (out or "lut") + ".cube"


def add_user_lut(src):
    """사용자가 고른 .cube 를 사용자 폴더로 **복사**하고 키를 돌려준다.
    복사하는 이유: 원본이 옮겨지거나 지워져도 사이드카·레시피가 계속 열려야 한다.
    같은 이름이 있으면 덮어쓴다(같은 파일을 다시 고른 흔한 경우 — 새 키를 만들면 목록에
    중복이 쌓인다).

    ⚠️**검증이 복사보다 먼저**다. 못 읽는 파일을 폴더에 남기면 목록에는 뜨는데(존재만 보는
    경로가 있다) 렌더는 조용히 빈 텍스처가 된다 — `add_user_font` 가 같은 실수를 한 번
    하고 고친 자리다.

    반환: `{"key": 성공 시 키, "error": 실패 사유, "note": 알려야 할 변경,
             "replaced": 기존 파일을 덮어썼는가(호출측 롤백 판단용)}`
    """
    try:
        srcp = Path(str(src))
        if srcp.suffix.lower() != ".cube" or not srcp.is_file():
            return {"key": "", "error": "Not a .cube file.", "note": ""}
        arr, n = load_cube(str(srcp), strict_domain=True)
        safe = _safe_name(srcp.name)
        dst = user_luts_dir(create=True) / safe
        # ⚠️번호 붙이기는 **이름이 실제로 접힌 경우에만** 한다. 판정은 대소문자를 무시한
        #   비교다 — `_safe_name` 이 확장자를 항상 소문자로 다시 붙이므로 `safe != srcp.name`
        #   으로 보면 `LOOK.CUBE` 를 다시 가져올 때마다 `LOOK (2)`, `LOOK (3)` … 이 쌓인다.
        #   ⚠️`_UNSAFE` 문자 유무로 보면 안 된다 — `_safe_name` 은 **공백 연속도 접고 끝의
        #   점/공백도 지운다**. `Kodak  1.cube`(공백 2칸)가 `Kodak 1.cube` 로 접히는데 그 분기를
        #   못 타서 기존 LUT 을 조용히 덮어썼다(실측: replaced=True, 내용 교체).
        if safe.lower() != srcp.name.lower():
            # ⚠️새니타이즈는 **서로 다른 이름을 같은 이름으로 접을 수 있다**
            #   (`Kodak#1.cube` / `Kodak%1.cube` / `Kodak  1.cube` → 전부 `Kodak 1.cube`). 이건
            #   같은 파일을 다시 고른 경우가 아니므로 덮어쓰면 **남의 LUT 이 사라지고**, 그 키를
            #   저장한 사진·레시피가 조용히 다른 룩으로 렌더된다. 번호를 붙여 피한다.
            #   이름이 접히지 않은 경우는 예전처럼 덮어쓴다 — 같은 LUT 을 다시 가져오는 흔한
            #   경우이고, 새 키를 만들면 목록에 중복이 쌓인다(사용자 폰트와 같은 규칙).
            k = 2
            while dst.exists() and srcp.resolve() != dst.resolve():
                dst = dst.parent / f"{safe[:-5]} ({k}).cube"
                k += 1
            safe = dst.name
        replaced = dst.exists() and srcp.resolve() != dst.resolve()
        note = "" if safe == srcp.name else f'Saved as "{safe}".'
        if replaced:
            note = ((note + " " if note else "")
                    + "Replaced the LUT already stored under that name.")
        if n > MAX_N:
            # 원본을 그대로 두면 아틀라스가 GPU 한도를 넘어 프리뷰만 죽는다. 파일 하나 = N 하나로
            # 맞춰야 프리뷰·GPU export·CPU export 가 같은 N 을 본다.
            # ⚠️`dst` 가 고른 파일 자신일 수 있다(사용자가 폴더에 직접 넣어둔 큰 큐브를
            #   Add 로 고른 경우). 제자리에 바로 쓰면 실패 시 **절단된 파일만 남는다** →
            #   임시 파일에 쓰고 원자적으로 교체한다(export 저장과 같은 규칙).
            _tmp = dst.with_name(dst.name + ".part")
            _write_cube(_tmp, _resample(arr, n, MAX_N), MAX_N, title=dst.stem)
            os.replace(_tmp, dst)
            note = ((note + " " if note else "")
                    + f"Resampled {n}³ → {MAX_N}³ "
                      f"(larger LUTs exceed GPU texture limits).")
        elif srcp.resolve() != dst.resolve():
            shutil.copyfile(srcp, dst)      # 이미 그 폴더의 파일을 고른 경우는 복사 생략
        # `replaced` 는 호출측 롤백 판단용이다 — 실패해도 **덮어쓴 경우엔 지우면 안 된다**.
        return {"key": USER_PREFIX + dst.name, "error": "", "note": note, "replaced": replaced}
    except Exception as exc:
        return {"key": "", "error": str(exc), "note": "", "replaced": False}


def remove_user_lut(key) -> bool:
    """추가한 사용자 LUT 을 지운다. 그 LUT 을 쓰던 사진은 목록에 없는 키가 되므로,
    경고와 함께 None(필름시뮬 미적용)으로 열린다."""
    if not is_user(key):
        return False
    try:
        # ⚠️`missing_ok` — 파일이 이미 없어도 **성공**으로 본다. False 를 돌려주면 UI 가
        #   "Could not delete that file." 을 띄우고 `filmSimsChanged` 도 안 나가서, 죽은
        #   항목이 목록에 남고 프로바이더가 옛 아틀라스를 계속 내준다.
        lut_path(key).unlink(missing_ok=True)
        return True
    except Exception as exc:
        print(f"[lut] 삭제 실패: {exc}")
        return False
