"""테스트용 .cube 생성기 — '내 LUT' 기능을 오프라인으로 재현 검증한다.

무료 LUT 팩은 대개 이메일 가입을 요구하고 버전마다 내용이 달라져 회귀 기준으로 못 쓴다.
여기서 만드는 세트는 **정상 7개 + 반드시 거부돼야 하는 3개**로, 가져오기 경로의 분기를
전부 밟는다(N=17/32/33/64/65 · 파일명 새니타이저 · 1D · 비표준 DOMAIN · 개수 불일치).

사용법 (프로젝트 venv 에서):
  python luts/make_test_luts.py [출력폴더]     # 기본 = 저장소 루트의 testluts/

만든 파일을 앱의 **Film Simulation → Add LUT…** 로 넣어 확인한다. 특히 두 개:
  - `Warm Fade`     : 중간톤이 밝아지는 룩 → 사용자 LUT 은 `simExpEV` 보정을 받지 않으므로
                      그 밝기가 **그대로 남아야** 한다(번들 LUT 이면 눌린다).
  - `Cross Process` : G 채널이 비단조 → `film_sim_ev` 의 단조증가 가정을 깨는 LUT.
                      보정을 안 돌리므로 밝기가 튀지 않아야 한다.

⚠️LUT 이 놓이는 자리는 `filmic()` **뒤**, 즉 display-referred sRGB [0,1] 이다(CLAUDE.md 의
파이프라인 순서). 그래서 여기의 룩들도 그 공간에서 정의한다 — 인터넷 크리에이티브 LUT 과
같은 전제다. Log 입력(S-Log3 등) LUT 은 그 전제가 안 맞아 앱이 거부한다.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lut as lutmod   # _write_cube 재사용(포맷/인덱스 순서를 앱과 한 곳에서 공유)

L = np.array([0.2126, 0.7152, 0.0722], np.float32)


def grid(n):
    """(n,n,n,3) 항등 격자. lut[r,g,b] = (r,g,b)/(n-1) — load_cube 와 같은 축 규약."""
    g = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1).astype(np.float32)


def sat(rgb, k):
    y = (rgb @ L)[..., None]
    return y + (rgb - y) * k


def teal_orange(n=33):
    """섀도=틸 / 하이라이트=오렌지 스플릿 톤 + 약한 대비. 휘도 단조."""
    c = grid(n)
    t = (c @ L)[..., None]
    shadow = np.array([-0.055, 0.010, 0.075], np.float32)
    high = np.array([0.075, 0.020, -0.065], np.float32)
    c = c + (1.0 - t) * shadow + t * high
    c = 0.5 + (c - 0.5) * 1.10
    return np.clip(sat(c, 1.12), 0, 1), "Teal and Orange"


def warm_fade(n=32):
    """블랙 리프트 + 하이라이트 롤오프 + 웜. ★중간톤이 **밝아진다** — '밝기가 곧 룩'인 케이스."""
    c = grid(n)
    c = 0.070 + c * 0.885                       # 페이드(블랙 들림)
    c = c ** 0.88                               # 중간톤 리프트
    c = c * np.array([1.045, 1.000, 0.945], np.float32)
    return np.clip(sat(c, 0.92), 0, 1), "Warm Fade"


def cross_process(n=33):
    """크로스프로세싱 — G 채널이 **비단조**라 film_sim_ev 의 단조증가 가정을 깬다.
    사용자 LUT 에는 솔버를 아예 안 돌리는 것이 맞다는 것을 눈으로 확인하는 용도."""
    c = grid(n)
    r = np.clip(0.5 + (c[..., 0] - 0.5) * 1.35, 0, 1)
    g = np.clip(np.sin(np.pi * c[..., 1]) * 0.45 + c[..., 1] * 0.55, 0, 1)   # 올랐다 내려간다
    b = np.clip(c[..., 2] ** 1.35 + 0.09, 0, 1)
    return np.stack([r, g, b], -1).astype(np.float32), "Cross Process"


def cool_bleach(n=17):
    """탈색 + 쿨 + 강한 대비. 작은 격자(N=17)."""
    c = grid(n)
    c = sat(c, 0.55)
    c = 0.5 + (c - 0.5) * 1.30
    c = c * np.array([0.955, 0.990, 1.055], np.float32)
    return np.clip(c, 0, 1), "Cool Bleach"


def deep_green(n=64):
    """녹 강조 — 앱이 그대로 받아들이는 최대 격자(N=64)."""
    c = grid(n)
    c[..., 1] = np.clip(c[..., 1] * 1.06 + 0.012, 0, 1)
    return np.clip(sat(c, 1.05) * np.array([0.98, 1.00, 0.97], np.float32), 0, 1), "Deep Green"


def main(out):
    os.makedirs(out, exist_ok=True)
    made = []

    for fn, (arr, title) in {
        "Teal and Orange.cube": teal_orange(),
        "Warm Fade.cube": warm_fade(),
        "Cross Process.cube": cross_process(),
        "Cool Bleach.cube": cool_bleach(),
        "Deep Green.cube": deep_green(),
    }.items():
        n = arr.shape[0]
        lutmod._write_cube(os.path.join(out, fn), arr, n, title=title)
        made.append((fn, f"N={n}", "정상"))

    # N=65 → 가져오기에서 64 로 리샘플되어야 한다(배너에 note).
    a, _ = teal_orange(65)
    lutmod._write_cube(os.path.join(out, "Huge Grid 65.cube"), a, 65, title="Huge Grid 65")
    made.append(("Huge Grid 65.cube", "N=65", "→ 64 로 리샘플 + note"))

    # 파일명 새니타이저: '#' 과 '%' 는 프로바이더 URL 을 깬다 → 이름이 바뀌어 저장돼야 한다.
    a, _ = cool_bleach(17)
    lutmod._write_cube(os.path.join(out, "Bad #Name 100%.cube"), a, 17, title="Bad Name")
    made.append(("Bad #Name 100%.cube", "N=17", "→ 이름 정리 + note"))

    # --- 거부돼야 하는 것들 ---
    n = 33
    a, _ = teal_orange(n)
    flat = np.clip(a, 0, 1).transpose(2, 1, 0, 3).reshape(-1, 3)
    with open(os.path.join(out, "REJECT log input.cube"), "w", encoding="utf-8") as f:
        f.write('TITLE "Log input (should be rejected)"\n')
        f.write("DOMAIN_MIN 0 0 0\nDOMAIN_MAX 4 4 4\n")      # 비표준 도메인 = Log 입력
        f.write(f"LUT_3D_SIZE {n}\n")
        for r, g, b in flat:
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")
    made.append(("REJECT log input.cube", "N=33 DOMAIN_MAX 4", "거부: Log 입력"))

    with open(os.path.join(out, "REJECT 1d only.cube"), "w", encoding="utf-8") as f:
        f.write('TITLE "1D only"\nLUT_1D_SIZE 16\n')
        for i in range(16):
            v = i / 15.0
            f.write(f"{v:.6f} {v:.6f} {v:.6f}\n")
    made.append(("REJECT 1d only.cube", "1D", "거부: 3D 아님"))

    with open(os.path.join(out, "REJECT truncated.cube"), "w", encoding="utf-8") as f:
        f.write('TITLE "Truncated"\nLUT_3D_SIZE 17\n')
        for r, g, b in flat[:500]:                            # 17**3 = 4913 이어야 하는데 500 개
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")
    made.append(("REJECT truncated.cube", "N=17 / 500행", "거부: 개수 불일치"))

    print(f"생성 위치: {out}\n")
    for fn, size, note in made:
        print(f"  {fn:24} {size:22} {note}")


if __name__ == "__main__":
    # ⚠️기본 출력은 cwd 가 아니라 저장소 안 고정 위치다 — 어디서 실행해도 같은 곳에 나오고,
    #   실수로 `luts/` 에 떨어져 번들(FilmRawstery.spec 의 `luts/*.cube`)에 섞이지 않는다.
    _default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "testluts")
    main(sys.argv[1] if len(sys.argv) > 1 else _default)
