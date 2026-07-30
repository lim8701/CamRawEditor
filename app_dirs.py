# -*- coding: utf-8 -*-
"""OS별 사용자 데이터 디렉터리 — 모델 등 대용량 런타임 자산의 영속 저장 위치.

zip 배포(frozen)는 업데이트마다 새 폴더에 풀리므로, 앱 폴더(lib/models) 안에
모델을 두면 버전업 때마다 최대 ~1.3GB 를 재다운로드해야 한다. dev/frozen 구분 없이
항상 OS 표준 사용자 데이터 디렉터리에 저장해 버전·실행 환경과 무관하게 유지한다:

  - Windows: %LOCALAPPDATA%/FilmRawstery/models   (머신 전용 대용량 — Roaming 제외)
  - macOS:   ~/Library/Application Support/FilmRawstery/models
             (재다운로드 가능 파일이지만 ~/Library/Caches 는 OS/정리도구가 지울 수 있음)
  - Linux:   ${XDG_DATA_HOME:-~/.local/share}/FilmRawstery/models  (XDG 규약)

**legacy 마이그레이션**: 예전 저장 위치(모듈 옆 models/ — 구버전 frozen 은 lib/models,
dev 는 저장소 models/)에 이미 받아둔 파일이 있으면 재다운로드 대신 **복사**
(materialize, 다운로드와 동일한 원자적 tmp→rename). ⚠️존재 검사(have)는 부작용 없음 —
GB급 복사가 UI 스레드에서 일어나지 않도록 materialize 는 각 모듈의
ensure_model(워커 스레드)에서만 호출할 것.
"""
import os
import shutil
import sys
import threading

APP_NAME = "FilmRawstery"

# 예전 저장 위치(복사 원본): 구버전 frozen=lib/models, dev=저장소 models/.
LEGACY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def _user_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


MODELS_DIR = os.path.join(_user_data_dir(), "models")

_copy_lock = threading.Lock()


def model_path(name: str) -> str:
    """모델 파일의 정규(다운로드 대상) 경로. 존재를 보장하지는 않음."""
    return os.path.join(MODELS_DIR, name)


def have(name: str) -> bool:
    """모델 확보 가능 여부(정규 경로 또는 legacy 에 존재). 부작용 없음 — UI 스레드 안전.
    legacy 에만 있는 경우도 True — 다운로드가 필요 없다는 뜻(ensure 시 복사로 확보)."""
    return (os.path.exists(model_path(name))
            or os.path.exists(os.path.join(LEGACY_DIR, name)))


def migrate_legacy_async() -> None:
    """앱 시작 시 1회: legacy 에 있는 모델/캐시 파일을 사용자 디렉터리로 백그라운드
    일괄 복사(데몬 스레드 — 시작 속도 영향 없음). 이후 기능 첫 사용 시 복사 대기가
    없고, legacy 폴더(구버전/저장소 models)를 바로 지워도 된다.

    대상: *.onnx(기각된 scunet* 제외) + florence2_*(토크나이저 json 포함)
          + ai_denoise_device.json. README 등 문서는 제외."""
    def _worker():
        try:
            if not os.path.isdir(LEGACY_DIR) or os.path.realpath(LEGACY_DIR) \
                    == os.path.realpath(MODELS_DIR):
                return
            for name in sorted(os.listdir(LEGACY_DIR)):
                low = name.lower()
                wanted = ((low.endswith(".onnx") and not low.startswith("scunet"))
                          or low.startswith("florence2_")
                          or low == "ai_denoise_device.json")
                if wanted and not low.endswith(".part"):
                    materialize(name)
        except Exception as exc:      # 마이그레이션 실패해도 앱 동작엔 지장 없음(lazy 폴백)
            print(f"[models] legacy 마이그레이션 실패(무시): {exc}")
    threading.Thread(target=_worker, daemon=True).start()


def materialize(name: str) -> bool:
    """정규 경로에 파일을 준비. legacy 에 있으면 복사해 재다운로드를 피한다.
    반환: 정규 경로에 존재하게 됐으면 True, 아니면 False(호출측이 다운로드).
    GB급 복사 가능 — 워커 스레드에서만 호출할 것."""
    path = model_path(name)
    if os.path.exists(path):
        return True
    legacy = os.path.join(LEGACY_DIR, name)
    if not os.path.exists(legacy):        # dev 는 path==legacy 라 여기서 함께 걸러짐
        return False
    with _copy_lock:
        if not os.path.exists(path):
            # dirname(=MODELS_DIR 또는 그 하위) — 외부 데이터를 쓰는 모델은 하위 폴더에 받는다
            # (depth: `model.onnx_data` 이름이 프로토에 박혀 있어 플랫 저장 시 충돌).
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            shutil.copyfile(legacy, tmp)
            os.replace(tmp, path)         # 원자적(복사 중 크래시 시 부분파일 방지)
            print(f"[models] 구버전 폴더에서 복사(재다운로드 생략): {name}")
    return True
