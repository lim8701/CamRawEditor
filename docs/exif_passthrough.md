# EXIF 메타데이터 통과 — 구현 계획 (미착수)

> 상태: **계획**. 아직 코드 없음. 착수 전에 아래 '결정이 필요한 것'을 먼저 정할 것.
> 작성 시점 기준 브랜치: `dev` @ `ea3a658`(지오태깅 머지 직후), 앱 v1.11.1.

## 왜 하나

export 파일에 **원본 EXIF 가 전혀 복사되지 않는다.** `pipeline._exif_app1` 이 `Software`(0x0131)
와 `DateTime`(0x0132) **2개만** 담은 APP1 을 새로 만들어 끼우는 구조라, 카메라가 적어 둔 것은
통째로 버려진다. 결과:

- 구글 포토·아이클라우드에서 **촬영일 정렬이 깨진다**(현상 시각으로 들어간다)
- **지도 뷰가 안 뜬다**(GPS 소실) — ⚠️지오태깅 기능이 붙은 지금은 사용자가 직접 찍은 좌표만
  나가고 카메라/폰이 적어 둔 원본 좌표는 여전히 버려진다
- 라이트룸·digiKam·Bridge 로 임포트하면 **카메라·렌즈·ISO·조리개·초점거리가 전부 빈다**

기능 부재가 아니라 **사실상 결함**에 가깝다. 다른 시스템과의 상호운용이 여기서 끊긴다.

---

## 측정 (2026-09-01)

### 소스 APP1 구조 — `DSCF8035.RAF`(X100V)

RAF 의 임베드 프리뷰 JPEG 이 **카메라의 진짜 EXIF** 를 들고 있다.

| 항목 | 값 |
|---|---|
APP1 세그먼트 | **65450 B** (EXIF 상한 65535 B → **여유 85 B**) |
바이트순서 | `II`(little-endian) |
IFD0 | 13 엔트리, `Software` 슬롯 **29 B**, `DateTime` **20 B** |
ExifIFD | 39 엔트리, 값 1545 B, **MakerNote 1094 B**, `PixelX/Y` 있음 |
IFD1(썸네일) | @3120, 썸네일 데이터 8852 B |
exifread 태그 | **52개**(details=False) / MakerNote 포함 시 **63개 추가** |

### 소스별 EXIF 가용성 — 15개 파일 실측

| 소스 | 임베드 프리뷰 APP1 | 파일 직접(exifread) | 판정 |
|---|---|---|---|
**RAF** | 65450 B, **카메라 실제 EXIF** + MakerNote, 7/8 | 0 태그 | **1단계 대상** |
JPEG/TIFF 입력 | 파일 자체가 APP1 보유 | 동일 | **1단계 대상** |
NEF·CR2·ARW·ORF·PEF·SRW·DNG | 1384 B, `Software = dcraw v9.26` → **LibRaw 생성 껍데기**, 6/8 | 55~82 태그, 7~8/8 | 2단계 |
CR3 | 위와 동일(6/8) | **0 태그**(BMFF) | 2단계 |

★**타사 RAW 의 임베드 프리뷰 EXIF 를 통과 소스로 쓰면 안 된다** — 9개 기종에서 APP1 이
정확히 1384 B 로 같았고 `Software` 가 전부 `dcraw v9.26` 이었다. 카메라가 적은 게 아니라
**LibRaw 가 프리뷰를 뽑으며 새로 쓴 것**이다. 렌즈·GPS·MakerNote 가 없다.

### 인플레이스 패치 실증 — **채택하지 않지만 근거로 남긴다**

APP1 을 통째로 복사하고 **같은 크기로만 덮어쓰면** 오프셋 재계산이 0이 된다. 실제로 해 봤다:

| 검증 | 결과 |
|---|---|
APP1 크기 | 65450 → **65450 B (불변)** |
픽셀 | **비트 동일** |
전달 태그 | 2개 → **52개** |
MakerNote | **63/63 태그, 값까지 동일** |
Orientation | 6 → **1** |
썸네일 | IFD1 분리로 제거 |
파일 증가 | **+65 KB/장** |

★그런데 **지오태깅과 공존할 수 없다**(아래 참조). 이 표는 "MakerNote 는 블록이 안 움직이면
100% 살아남는다"는 사실의 근거로만 유효하다.

---

## ★ 설계 결정 — 인플레이스가 아니라 **재직렬화**

**기각: 인플레이스 패치 단독안.** 이유는 한 줄로 측정된다.

```
소스 APP1 여유 =  85 B
GPS IFD 크기   = 178 B   (지오태깅 커밋의 실측)
178 > 85  ->  들어가지 않는다
```

지오태깅(`Ctrl+6`)이 붙은 뒤로 export APP1 은 사용자가 붙인 GPS IFD 를 담아야 한다.
소스 APP1 을 통째로 들어다 쓰면 **지오태그가 export 에서 조용히 사라진다** — 새 기능의 회귀다.
⚠️두 기능을 배타적으로 두는 안(지오태그 있으면 통과 끄기)도 기각한다 — 위치를 붙인 사진만
카메라 EXIF 를 전부 잃는다.

**그래서 재직렬화한다**: 소스 IFD 를 파싱 → 필요한 것만 남기고 → 오프셋을 다시 계산해 새 APP1 을
만든다. 원래 피하려던 방식이지만 **지오태깅이 그 기계의 대부분을 이미 만들어 놓았다**:

| 이미 있는 것 (`pipeline.py`) | 역할 |
|---|---|
`_ifd_block(entries, data_off, blobs)` | IFD 하나 직렬화. **인라인(≤4B) / 오프셋 두 경로**, 짝수 정렬 |
`_TIFF_TYPE_SIZE` | BYTE/ASCII/LONG/RATIONAL |
`_exif_app1(software, when, gps)` | **2-패스 오프셋 레이아웃**(IFD0 크기 확정 후 0x8825 값 채움) |
`_gps_entries` / `_dms_rational` / `gps_from_params` | GPS IFD |
`_insert_app1(jpeg, app1)` | 인코딩 끝난 바이트에 삽입(픽셀 불변) |
`save_image(arr, path, software, gps=None)` | CPU/GPU export 2곳에 배선 완료 |

재직렬화의 부수 이득: **썸네일 8852 B 를 실제로 버릴 수 있다**(인플레이스는 IFD1 포인터만
끊을 뿐 바이트가 남는다) → +65 KB 였던 증가분이 줄고 GPS 여유도 생긴다.

---

## 1단계 — RAF·일반이미지 통과 (이번 작업 범위)

**적용: JPEG export 만.** PNG 은 tEXt 크레딧 현행 유지, **TIFF 는 Qt 핸들러가 텍스트를 버려
원천 불가**(기존 확인 사항).

### 새 모듈 `exif_pass.py`

새 의존성 없음. `_exif_app1` 이 이미 손으로 APP1 을 만드는 것과 같은 결.

```
find_app1(jpeg_bytes)   -> (offset, length) | (None, None)
source_app1(path)       -> bytes | None
    RAF/RAW -> exif_info.embedded_preview_jpeg(), 일반 이미지 -> 파일 직접
parse_app1(app1)        -> {ifd0: [...], exif: [...], gps: [...]}   # 엔트리 목록
build_app1(parsed, *, software, when, width, height, gps=None, keep_gps=True) -> bytes
    pipeline._ifd_block 재사용, 2-패스 오프셋
```

⚠️`pipeline._exif_app1` 을 **대체하지 않는다.** 소스 APP1 이 없을 때의 폴백으로 그대로 남는다.

### 태그 정책

| 태그 | 처리 | 왜 |
|---|---|---|
`0x0112` Orientation | **1 로 고정** | 회전·플립을 이미 픽셀에 구웠다. 안 고치면 뷰어가 **이중 회전** |
`0x0131` Software | **우리 크레딧으로 교체** | 현상기가 우리다 |
`0x0132` DateTime | **현상 시각으로 교체** | 파일이 만들어진 시각(촬영 시각 아님) |
`0xA002/0xA003` PixelX/Y | **export 치수로 교체** | 실측 4416×2944 로 남아 있었다(크롭·리사이즈 무시) |
`0x8769` ExifIFD | **유지**(재귀 재직렬화) | DateTimeOriginal·조리개·ISO·렌즈 — 값의 본체 |
`0x8825` GPS IFD | **지오태그 우선, 없으면 원본 유지** | 아래 우선순위 규칙 |
`0x927C` MakerNote | **판단 필요** — 아래 ⚠️ | |
IFD1(썸네일) | **버린다** | 크롭 전 구도라 거짓. 8852 B 절약 |
`0x0201/0x0202`, StripOffsets 류 | **버린다** | 원본 파일 오프셋을 가리켜 무의미 |
`0xA005` Interop IFD | **버린다** | 값어치 대비 복잡도 |

⚠️★**MakerNote 는 재직렬화에서 살아남지 못할 수 있다.** 인플레이스 실증에서 63/63 이
살아남은 것은 **블록이 하나도 안 움직였기 때문**이다. 후지 MakerNote 는 내부에 TIFF 헤더 기준
오프셋을 담고 있어, 재직렬화로 위치가 바뀌면 **조용히 깨진다**(에러 없이 값만 엉킨다).
→ 1단계에서는 **MakerNote 를 버리는 것을 기본**으로 하고, 살리려면 별도 실측이 필요하다
(옮긴 뒤 `exifread details=True` 로 63개가 그대로인지 대조).

### GPS 우선순위

```
사용자가 Location 탭에서 붙인 좌표  >  원본 EXIF 의 GPS  >  없음
```

⚠️사이드카의 `gpsLat` 키가 **있으면(값이 null 이어도) 그것이 답이다** — 지오태깅 문서의 규칙과
같다. 사용자가 위치를 **지운** 사진에서 원본 EXIF GPS 가 되살아나면 안 된다.

### 배선 (★ 렌더 경로 체크리스트)

`save_image(arr, path, software="", gps=None)` → `src_path=""` 추가. 호출부 4곳:

| 위치 | 소스 경로 | 조치 |
|---|---|---|
`main.py` `_do_export` (CPU·배치) | `src[0]` 에 **이미 스냅샷됨** | 넘기기만 |
`main.py` `_finish_gpu_export` | ⚠️`_gpu_params` 에 소스 경로 **없음** | `exportImageGpu` 에서 `_gpu_params["srcPath"] = self._path` 스냅샷 추가 |
`main.py` 배경화면 합성 2곳 | 소스가 3장 | **통과 안 함**(크레딧만, 현행 유지) |

⚠️GPU export 는 `params` 를 안 거치고 `_gpu_params` 를 따로 읽는다 — `CLAUDE.md` 가 "가장 잘
빠진다"고 경고한 경로다. 스냅샷은 **요청 시점**에 떠야 한다(export 중 다른 사진을 열면 남의
EXIF 가 박힌다). 지오태깅이 `gps_from_params` 로 같은 문제를 푼 방식을 따를 것.

### 폴백

어느 단계든 실패하면 **손대지 않고 기존 `_exif_app1` 크레딧 경로로 되돌아간다.**
메타데이터 때문에 산출물을 깨뜨리지 않는다 — `_insert_png_chunks` 의 기존 원칙과 같다.

### 검증 — 함수 직접 호출 금지, 실제 경로로

1. ★**픽셀 불변** — 통과 전/후 JPEG 디코드 `array_equal`. 깨지면 즉시 중단
2. ★**세 경로 대조** — 같은 사진을 CPU / GPU / 배치 export 로 내보내 `exifread` **태그 집합이
   셋 다 동일**한지. (함수 직접 호출은 배선 누락을 못 잡는다)
3. **회전 사진** — 세로 RAF → `Orientation == 1` 이고 뷰어에서 안 눕는지
4. **치수** — `ExifImageWidth/Length` == 실제 export 치수(크롭·해상도 프리셋 각각)
5. **지오태그 공존** — 위치를 붙인 사진 / 지운 사진 / 안 붙인 사진 3종에서 GPS 우선순위 확인
6. **소스별** — RAF / JPEG 입력 / NEF(2단계 전이라 크레딧만) / APP1 없는 파일 → 안 깨지는지
7. `python xplat_check.py` 기준선 **17/19** 유지

---

## 2단계 — 타사 RAW 재구축 (조건부)

`exif_info._exif_tags()` 가 주는 dict(NEF 55 / DNG 82 태그)로 IFD0+ExifIFD 를 **조립**한다.
`_ifd_block` 이 이미 RATIONAL 과 인라인을 지원하므로 남는 일은 태그 매핑 약 13개
(Make, Model, DateTimeOriginal, ExposureTime, FNumber, ISO, FocalLength, LensModel …).

⚠️CR3 는 exifread 가 파일을 못 읽어(0 태그) **dcraw 프리뷰의 6/8 이 상한**이다.

**착수 판단은 1단계 평가 후.** 후지 아닌 기종을 실제로 쓰는지가 기준.

## 3단계 — XMP 사이드카 (별점 선행 필요)

`<파일명>.xmp` 에 **분류 정보만** — `xmp:Rating`, `xmp:Label`, `dc:subject`(캡션 해시태그),
`dc:description`. Bridge·digiKam·XnView·Photo Mechanic·darktable 이 즉시 읽는다.

- **룩은 안 넣는다.** 룩 키 44개·마스크·브러시 획을 Adobe `crs:` 스키마로 정직하게 옮길 수 없다
- ⚠️**기존 XMP 를 덮어쓰지 말 것** — 다른 앱이 쓴 파일이면 우리 필드만 병합
- 선행: 지금 ♥ 이진 플래그뿐이라 **별점/컬러 라벨을 먼저** 넣어야 의미가 있다

---

## 결정이 필요한 것

1. **MakerNote 를 살릴까?** 기본은 '버린다'. 살리려면 재직렬화 후 63개 대조 실측이 선행돼야
   한다. (살리면 후지 레시피 읽기 기능의 토대가 되기도 한다)
2. **원본 EXIF 의 GPS 를 통과시킬까?** 지도 뷰의 핵심이지만 집 위치가 공유 파일에 박힌다.
   → 기본 ON + 끄는 토글을 권함. 지오태깅 UI 옆에 두는 게 자연스럽다
3. **크레딧 vs 통과 충돌** — 재직렬화라 슬롯 길이 제약은 사라졌다. `Software` 는 우리 것으로
   교체하는 것이 맞다(현상기가 우리다). 원본 `Software`(카메라 펌웨어 버전)를 어딘가 남길지는
   선택 — 남긴다면 `0x000B ProcessingSoftware` 가 자리다

---

## 참고

- 지오태깅 설계·함정: [`geotagging.md`](geotagging.md)
- 렌더 경로 4중 계약·export 규칙: `../CLAUDE.md`
- 관련 코드: `pipeline.py`(`_ifd_block`, `_exif_app1`, `_insert_app1`, `save_image`),
  `exif_info.py`(`embedded_preview_jpeg`, `_exif_tags`, `read_gps`)
