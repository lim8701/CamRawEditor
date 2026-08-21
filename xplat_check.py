"""FilmRawstery 크로스 플랫폼 렌더 동일성 검사.

같은 소스 이미지 + 같은 조정값으로 numpy export 파이프라인을 돌려
각 케이스 결과 배열의 sha256 과 통계를 JSON 으로 남긴다.
Windows/macOS 양쪽에서 실행해 JSON 을 비교하면 플랫폼 간 차이를 정량으로 잡는다.

사용법 (저장소 루트에서, venv 파이썬으로):
    python xplat_check.py                 # 결과 -> xplat_<platform>.json
    python xplat_check.py other.json      # 기존 JSON 과 비교까지
"""
import hashlib, json, os, platform, sys, time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtGui import QGuiApplication
_app = QGuiApplication(sys.argv[:1])
import numpy as np
import pipeline, lut, image_loader

SRC = os.path.join(ROOT, "_xplat_src.png")


def make_source(path, H=1200, W=1800):
    """결정론적 합성 소스(하늘/그레이램프/컬러패치/고주파 체커/딥섀도)."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = x / (W - 1), y / (H - 1)
    img = np.zeros((H, W, 3), np.float32)
    sky = v < 0.4
    img[..., 0] = np.where(sky, 0.35 + 0.45 * v / 0.4, 0)
    img[..., 1] = np.where(sky, 0.55 + 0.35 * v / 0.4, 0)
    img[..., 2] = np.where(sky, 0.85 - 0.10 * v / 0.4, 0)
    mid = (v >= 0.4) & (v < 0.62)
    for c in range(3):
        img[..., c] = np.where(mid, np.clip(u, 0, 1), img[..., c])
    for i, rgb in enumerate([(0.86,0.35,0.28),(0.30,0.62,0.32),(0.28,0.38,0.80),
                             (0.92,0.80,0.25),(0.78,0.55,0.45),(0.55,0.30,0.62)]):
        x0, x1 = int(W*(0.04+i*0.155)), int(W*(0.04+i*0.155+0.13))
        img[int(H*0.64):int(H*0.80), x0:x1] = rgb
    low = v >= 0.82
    chk = (((x // 3).astype(int) + (y // 3).astype(int)) % 2).astype(np.float32)
    det = 0.45 + 0.25 * chk
    for c, k in enumerate((1.0, 1.0, 0.95)):
        img[..., c] = np.where(low, det * (0.2 + 0.8 * u) * k, img[..., c])
    img[(v >= 0.80) & (v < 0.82)] = 0.02
    out = (np.clip(img, 0, 1) ** (1 / 2.2) * 255).round().astype(np.uint8)
    assert pipeline.save_image(out, path), "source save failed"
    return out


IDENT = [i / 255.0 for i in range(256)]
CURVE_ID = pipeline.compose_curves(IDENT, IDENT, IDENT, IDENT)
SCURVE = pipeline.compose_curves(
    [min(1.0, max(0.0, t - 0.12 * np.sin(2 * np.pi * t))) for t in IDENT],
    IDENT, IDENT, IDENT)

BASE = {"temp": 0, "tint": 0, "exposure": 0.0, "contrast": 1.0, "sat": 0, "saturation": 0,
        "clarity": 0, "texture": 0, "dehaze": 0, "vibrance": 0,
        "highlights": 0, "shadows": 0, "whites": 0, "blacks": 0,
        "hslH": [0.0]*8, "hslS": [0.0]*8, "hslL": [0.0]*8,
        "cgShadowHue": 0.0, "cgShadowSat": 0.0, "cgMidHue": 0.0, "cgMidSat": 0.0,
        "cgHighHue": 0.0, "cgHighSat": 0.0, "cgBalance": 0.0,
        "sharpenAmt": 0.0, "sharpenRadius": 1.0, "sharpenDetail": 0.25, "sharpenMask": 0.0,
        "lumaNR": 0, "colorNR": 0, "grainAmt": 0, "grainSize": 0.5, "grainRough": 0.1,
        "grainColor": 0.3, "grainShape": 0.0, "vignette": 0,
        "dateStamp": False, "lutEnabled": False, "lutStrength": 1.0, "hlDesat": 0.0,
        "maskLayers": [], "outEdge": 0, "lensCorrection": False,
        "cropX": 0.0, "cropY": 0.0, "cropW": 1.0, "cropH": 1.0,
        "rotateAngle": 0.0, "quarterTurns": 0, "flipH": False, "flipV": False,
        "geoH": 0.0, "geoV": 0.0, "geoScalePct": 100.0}

# (이름, 파라미터 오버라이드, LUT 이름, 켈빈, 커브)
CASES = [
    ("identity",        {}, None, 5500, CURVE_ID),
    ("exposure+1EV",    {"exposure": 1.0}, None, 5500, CURVE_ID),
    ("tone",            {"contrast": 1.25, "highlights": -0.4, "shadows": 0.5,
                         "whites": 0.2, "blacks": -0.3}, None, 5500, CURVE_ID),
    ("scurve",          {}, None, 5500, SCURVE),
    ("wb8000",          {}, None, 8000, CURVE_ID),
    ("wb3500tint",      {"tint": 0.5}, None, 3500, CURVE_ID),
    ("color",           {"saturation": 0.3, "vibrance": 0.4,
                         "hslS": [0.3, 0, -0.3, 0, 0.2, 0, 0, 0]}, None, 5500, CURVE_ID),
    ("colorgrade",      {"cgShadowHue": 210.0, "cgShadowSat": 0.4,
                         "cgHighHue": 40.0, "cgHighSat": 0.3}, None, 5500, CURVE_ID),
    ("detail",          {"clarity": 0.5, "texture": 0.4, "dehaze": 0.3,
                         "sharpenAmt": 0.6, "sharpenMask": 0.3}, None, 5500, CURVE_ID),
    ("nr",              {"lumaNR": 0.5, "colorNR": 0.5}, None, 5500, CURVE_ID),
    ("lut_classic_chrome", {}, "classic_chrome", 5500, CURVE_ID),
    ("lut_velvia_half", {"lutStrength": 0.5}, "velvia", 5500, CURVE_ID),
    ("grain_square",    {"grainAmt": 0.6, "grainSize": 0.4}, None, 5500, CURVE_ID),
    ("grain_round",     {"grainAmt": 0.6, "grainSize": 0.4, "grainShape": 1.0}, None, 5500, CURVE_ID),
    ("vignette",        {"vignette": -0.5}, None, 5500, CURVE_ID),
    ("geometry",        {"cropX": 0.08, "cropY": 0.06, "cropW": 0.8, "cropH": 0.85,
                         "rotateAngle": 4.0, "quarterTurns": 1, "flipH": True,
                         "geoH": 15.0, "geoV": -10.0, "geoScalePct": 105.0}, None, 5500, CURVE_ID),
    ("datestamp",       {"dateStamp": True, "stampText": "26 8 20", "stampStyle": "7c_bold",
                         "stampSize": 0.05, "stampMargin": 0.05, "stampRot": 0}, None, 5500, CURVE_ID),
    ("outEdge1024",     {"outEdge": 1024, "grainAmt": 0.3}, None, 5500, CURVE_ID),
    ("bit16",           {"contrast": 1.1}, None, 5500, CURVE_ID),   # bitdepth=16
]


def run():
    if not os.path.exists(SRC):
        make_source(SRC)
    res = {"platform": {
        "system": platform.system(), "release": platform.release(),
        "machine": platform.machine(), "python": platform.python_version(),
        "numpy": np.__version__, "scipy": __import__("scipy").__version__,
        "rawpy": __import__("rawpy").__version__,
        "cv2": __import__("cv2").__version__,
        "PySide6": __import__("PySide6").__version__,
        "onnxruntime": __import__("onnxruntime").__version__,
        "ort_providers": __import__("onnxruntime").get_available_providers(),
    }, "cases": {}}
    for name, over, sim, kelvin, curve in CASES:
        p = dict(BASE); p.update(over)
        la, ln = (None, 0)
        if sim:
            la, ln = lut.load_cube(os.path.join("luts", f"{sim}.cube")); p["lutEnabled"] = True
        depth = 16 if name == "bit16" else 8
        t0 = time.time()
        arr = pipeline.render_full(SRC, kelvin, p.get("tint", 0.0) or 0.0, p, la, ln, curve,
                                   proxy_edge=2560, bitdepth=depth)
        el = time.time() - t0
        a = np.ascontiguousarray(arr)
        res["cases"][name] = {
            "sha256": hashlib.sha256(a.tobytes()).hexdigest(),
            "shape": list(a.shape), "dtype": str(a.dtype),
            "mean": round(float(a.mean()), 4), "std": round(float(a.std()), 4),
            "min": int(a.min()), "max": int(a.max()),
            "sec": round(el, 3),
        }
        print(f"{name:22} {el:6.2f}s  {res['cases'][name]['sha256'][:16]}  "
              f"mean={res['cases'][name]['mean']:8.3f} std={res['cases'][name]['std']:7.3f}")
    return res


def compare(mine, other_path):
    other = json.load(open(other_path, encoding="utf-8"))
    print(f"\n=== 비교: {platform.system()} vs {other['platform']['system']} ===")
    for k, v in other["platform"].items():
        m = mine["platform"].get(k)
        if m != v:
            print(f"  env  {k}: {m}  vs  {v}")
    same = diff = 0
    for name, c in mine["cases"].items():
        o = other["cases"].get(name)
        if o is None:
            print(f"  {name:22} 상대에 없음"); continue
        if c["sha256"] == o["sha256"]:
            same += 1
        else:
            diff += 1
            print(f"  {name:22} DIFF  mean {c['mean']:.3f} vs {o['mean']:.3f}"
                  f"  (Δ{c['mean']-o['mean']:+.4f})  std {c['std']:.3f} vs {o['std']:.3f}"
                  f"  shape {c['shape']} vs {o['shape']}")
    print(f"\n  일치 {same} / 불일치 {diff}")


if __name__ == "__main__":
    r = run()
    out = f"xplat_{platform.system().lower()}.json"
    json.dump(r, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n저장: {out}")
    if len(sys.argv) > 1:
        compare(r, sys.argv[1])
