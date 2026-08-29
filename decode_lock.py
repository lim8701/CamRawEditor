# -*- coding: utf-8 -*-
"""Qt 이미지 디코드/인코드 전역 직렬화 락 — GIL↔Qt 플러그인 뮤텍스 교착 방지.

2026-08-29 실측(py-spy --native, '응답 없음' 프로세스)으로 확정한 교착:

  - 썸네일(Qt 픽스맵 스레드): `QImage.loadFromData` — PySide6 가 이 호출에선 **GIL 을
    쥔 채** Qt 이미지 핸들러 팩토리 뮤텍스를 기다림(QBasicMutex::lockInternal).
  - 인덱싱 워커: 파이썬제 QBuffer 를 물린 `QImageReader.read()` — GIL 은 놓고 들어가
    그 뮤텍스를 쥔 뒤, 디바이스 가상 read 를 Shiboken 이 디스패치하며 **GIL 재획득**
    대기(Sbk_GetPyOverride → PyGILState_Ensure).
  → ABBA 완전 교착. 메인 스레드도 다음 파이썬 슬롯/타이머 디스패치에서 GIL 대기로
    합류해 앱 전체가 '응답 없음'이 된다(인덱싱 + 썸네일/프리뷰 조작 반복으로 재현).

규칙: **QImageReader/QImageWriter/QImage.loadFromData/QImage.save 등 이미지 플러그인
기계를 태우는 호출은 전부 `with QT_IMG_LOCK:` 안에서만.** 우리 쪽 호출이 서로 겹치지
않으면 위 순환의 한쪽 다리가 사라진다(ai_denoise.GPU_LOCK 과 같은 계열의 해법).
디코드는 ms 단위라 직렬화 비용은 체감 불가. 파일 경로를 직접 문 QImageReader 도
감싼다 — 교착은 아니지만 느린 장치(외장하드) I/O 동안 뮤텍스를 쥐면, GIL 을 쥔 채
그 뮤텍스를 기다리는 호출(loadFromData)이 앱 전체를 그 시간만큼 멈추게 하기 때문
(파이썬 락 대기는 GIL 을 놓으므로 앱이 살아 있다).

QImage(numpy)/convertToFormat/scaled 등 픽셀 연산은 플러그인 기계와 무관 — 잡지 말 것
(26MP 변환을 직렬화하면 그게 병목이 된다). RLock 인 이유: _display_preview_jpeg 처럼
디코드→재인코딩이 한 스레드에서 이어지는 경로의 중첩 획득을 허용(자기 자신과는 교착
불가 — 위 순환은 서로 다른 두 스레드가 전제다).
"""
import threading

QT_IMG_LOCK = threading.RLock()
