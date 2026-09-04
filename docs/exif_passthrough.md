# EXIF 메타데이터 통과

> 상태: **1단계 구현됨**(`exif_pass.py`, v1.11.1). 2·3단계는 계획.
> 결정된 것: MakerNote **유지** · 원본 GPS **기본 통과 + 끄는 토글** · 원본 Software 를
> **`0x000B ProcessingSoftware`** 로 보존.

## 구현 결과 (실측, RAF 기준)

| | 이전 | 이후 |
|---|---|---|
export JPEG 의 EXIF 태그 | **2개**(Software/DateTime) | **115개** |
MakerNote | 없음 | **63/63 값까지 동일** |
APP1 크기 | 90 B | **3122 B**(소스 65450 B 의 4.8%) |
픽셀 | — | **비트 동일**(변화 없음) |

착지점: `exif_pass.py`(파서 + 빌더) · `pipeline.save_image(..., src_path=, keep_gps=)` ·
`pipeline._TIFF_TYPE_SIZE`/`_TIFF_SWAP_UNIT` · CPU/GPU export 두 호출부 ·
Export 옵션의 `Keep original GPS (JPEG)` 체크박스(`prefs.json` 의 `export.keepGps`).

### ★⚠️ 구현 중 실제로 났던 버그 — 바이트순서를 안 보고 뒤집었다

값을 little-endian 으로 정규화하는 함수를 **소스 바이트순서와 무관하게 항상 뒤집도록** 짰다.
`II`(little) 소스에서 `0x8769`(ExifIFD 포인터)가 뒤집혀 엉뚱한 오프셋이 되고, 파서가 그 자리에서
IFD 를 못 찾아 **ExifIFD 전체가 조용히 0개**가 됐다 — 예외도 경고도 없고, `MakerNote 63 → 0`,
`DateTimeOriginal/ISO/조리개 전부 None` 으로만 드러난다. 실측 소스가 전부 `II` 라 **정상 경로에서
100% 재현**되는데도 "값이 비네" 정도로 보인다. `_to_le` 는 `bo == "<"` 이면 **즉시 반환**한다.

교훈: 통과 기능은 **태그 수를 세는 검증이 필수**다. 파일은 멀쩡히 열리고 픽셀도 맞으므로
"저장 성공"만 보면 아무 문제가 없어 보인다.

### ★⚠️ MakerNote 는 화이트리스트다 — 캐논은 옮기면 깨진다

계획서는 후지 실측(63/63)을 근거로 MakerNote 를 '유지'로 정하면서 니콘·캐논은 2단계로 미뤘다.
그런데 **캐논 JPEG 은 1단계 범위 안이다**(일반 이미지 입력). 실측했다:

| 소스 | src MakerNote | out | 값 일치 |
|---|---|---|---|
후지 X100V RAF | 63 | 63 | **63/63** ✅ |
**캐논 EOS R6 JPEG** | 114 | 114 | **39/114** ❌ |

캐논은 내부 오프셋이 **TIFF 헤더 기준**이라 blob 을 옮기면 값이 엉뚱한 곳을 가리킨다.
★⚠️**태그 개수는 114 로 그대로다** — 개수만 세는 검증으로는 못 잡고, **값을 대조해야** 보인다.

→ `_MAKERNOTE_SAFE` **화이트리스트**로 바꿨다(현재 `FUJIFILM` 만). 확인 안 된 제조사는 **싣지
않는다** — 조용히 틀린 메타데이터는 없는 것보다 나쁘다. 캐논도 나머지 EXIF(Make/Model/렌즈명/
DateTimeOriginal/조리개/ISO/셔터/초점거리 **8/8**)는 그대로 통과한다(태그 61 → 50).
제조사를 추가하려면 위 대조 실측을 먼저 통과시킬 것.

### 좌표 없는 GPS IFD 는 버린다

여러 바디가 위치를 못 받아도 `GPSVersionID` 하나뿐인 **껍데기 GPS IFD** 를 써 넣는다 —
실측: 보유 사진 427장 중 **121장**(Canon EOS R6 전량)이 이 상태. 그대로 실으면 의미 없는 빈
IFD 약 30 B 가 붙는다. 판정 기준은 `GPSLatitude`(0x0002) 존재 여부다.

⚠️참고로 **실제 좌표를 가진 사진은 427장 중 2장뿐**(드론 DNG)이었다. X100V 는 GPS 수신기가
없다 — 이 앱에서 위치를 붙이는 실질적 수단은 Location 탭이고, 그 좌표는 `keep_gps` 와 무관하게
항상 나간다. 반대로 **카메라가 파일에 남긴 좌표는 전적으로 `keep_gps` 가 결정한다**(아래
'GPS 우선순위' — 이 둘을 `gpsSrc` 로 가르지 않으면 체크박스가 먹지 않는다).

### 좌표 왕복 정밀도

사용자 좌표를 써서 다시 읽어 십진 도로 환산했을 때 **4분면 전부 오차 0.000 m**
(서울 N/E · 시드니 S/E · 리우 S/W · 뉴욕 N/W · 적도·본초자오선 0,0 · 음수 고도).
부호는 `GPSLatitudeRef`/`GPSLongitudeRef` 의 N/S·E/W 로 나가고 RATIONAL 은 절댓값이다
(`_dms_rational` 이 정수 산술로 쪼개 `59'60"` 반올림 밀림을 피한다).

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

★그런데 **지오태깅과 공존할 수 없다**(아래 참조). 이 표는 MakerNote 가 온전히 옮겨진다는
사실의 근거로 남는다 — ⚠️단 **"블록이 안 움직였기 때문"이라는 해석은 틀렸다.** 후지 MakerNote 는
움직여도 살아남는다(아래 '태그 정책'의 ★ 실증).

⚠️**위 `+65 KB/장` 은 인플레이스 기준이다.** 채택안(재직렬화)의 값은 아래를 볼 것 — 20배 작다.

### 재직렬화 후 APP1 크기 — 소스의 95%가 버릴 것이었다

| | |
|---|---|
소스 APP1 세그먼트 | 65452 B |
살아있는 값 바이트 합 | **2443 B** (MakerNote 1094 B 포함) |
남길 엔트리 52개 × 12 B | 624 B |
**재직렬화 APP1 개산** | **약 3101 B** |

★즉 소스 APP1 의 **약 95%가 썸네일과 패딩**이었다. 재직렬화하면 파일 증가가
**+65 KB → 약 +3 KB** 로 떨어진다. 함의:

- 파일 크기는 **논의 대상이 아니다**(공간을 아끼려 MakerNote 를 버릴 이유가 없다 — 전체의 1 KB다)
- 64 KB 상한도 **후지에서는 사실상 문제가 아니다**. ⚠️그래도 가드는 필요하다 — 소스별 조사에서
  **라이카 D-LUX MakerNote 가 28652 B**, 파나소닉 G1 이 7292 B 였다(후지 1094 B 가 작은 편이다).
  2단계에서 기종에 따라 상한에 닿을 수 있다 → 검증 8번

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

★⚠️**쓰기는 이미 되지만 읽기(파서)가 없다 — 값 타입 표를 먼저 채워야 한다.**
`_ifd_block` 은 `len(payload) <= 4` 로만 인라인/오프셋을 정하므로 **타입과 무관하게 이미
범용**이다. 반면 `parse_app1` 은 **타입 x count 로 값의 바이트 길이를 계산**해야 오프셋에서
값을 떼어낼 수 있다. 그런데 `pipeline._TIFF_TYPE_SIZE = {1,2,4,5}` 에는 실측 소스에 실제로
나오는 타입 셋이 빠져 있다:

| type | 이름 | 전체 등장 | 남길 IFD 만 | 표에 있나 |
|---|---|---|---|---|
2 | ASCII | 11 | 9 | ✅ |
**3** | **SHORT** | **22 (최다)** | **18 (최다)** | **❌** |
4 | LONG | 6 | 4 | ✅ |
5 | RATIONAL | 12 | 10 | ✅ |
**7** | **UNDEFINED** | **9** | **8** | **❌** |
**10** | **SRATIONAL** | **3** | **3** | **❌** |
| | **합** | **63** | **52** | |

⚠️두 열의 차이 11개는 **IFD1(썸네일 9) + Interop IFD(2)** 다 — 아래 '태그 정책'에서 버리는
것들이라 값 길이를 계산할 일이 없다. **결론은 어느 쪽으로 세든 같다**(SHORT 최다, 3·7·10 누락).

곁들여 `_TIFF_TYPE_SIZE` 는 **지금 선언만 되고 어디서도 쓰이지 않는 죽은 코드**다(지오태깅
커밋의 잔재). 지우지 말고 **파서용으로 3·7·10(필요하면 6·8·9·11·12까지)을 채워 되살릴 것.**

### 태그 정책

| 태그 | 처리 | 왜 |
|---|---|---|
`0x0112` Orientation | **1 로 고정** | 회전·플립을 이미 픽셀에 구웠다. 안 고치면 뷰어가 **이중 회전** |
`0x0131` Software | **우리 크레딧으로 교체** | 현상기가 우리다 |
`0x0132` DateTime | **현상 시각으로 교체** | 파일이 만들어진 시각(촬영 시각 아님) |
`0xA002/0xA003` PixelX/Y | **export 치수로 교체** | 실측 4416×2944 로 남아 있었다(크롭·리사이즈 무시) |
`0x8769` ExifIFD | **유지**(재귀 재직렬화) | DateTimeOriginal·조리개·ISO·렌즈 — 값의 본체 |
`0x8825` GPS IFD | **지오태그 우선, 없으면 원본 유지** | 아래 우선순위 규칙 |
`0x927C` MakerNote | **유지**(UNDEFINED type 7 blob 그대로 복사) | 아래 ★ — 후지는 **이동해도 안 깨진다** |
IFD1(썸네일) | **버린다** | 크롭 전 구도라 거짓. 8852 B 절약 |
`0x0201/0x0202`, StripOffsets 류 | **버린다** | 원본 파일 오프셋을 가리켜 무의미 |
`0xA005` Interop IFD | **버린다** | 값어치 대비 복잡도 |

★**후지 MakerNote 는 재직렬화로 위치가 바뀌어도 안 깨진다 — 그대로 실으면 된다.**
(⚠️이 문서는 원래 "TIFF 헤더 기준 오프셋이라 조용히 깨진다"고 보고 **버리는 것을 기본**으로
뒀다. 그 전제가 틀렸다. 근거 셋:)

1. `exifread` 가 정반대를 말한다 — `exifread/core/exif_header.py:577`
   ```python
   # bug: IFD offsets are from beginning of MakerNote, not
   #      beginning of file header
   ```
2. 실측 헤더가 스스로 선언한다 — 머리 16 B 가 `b'FUJIFILM\x0c\x00\x00\x00…'` 로,
   `\x0c` = **IFD 오프셋 12, MakerNote 블록 시작 기준**이다(TIFF 헤더 기준이 아니다).
3. 실증 — 같은 1094 B blob 을 앞에 pad **0 / 7 / 64 / 1000** 바이트를 끼워 **네 개의 서로 다른
   절대 오프셋**에 놓고 `pipeline._ifd_block` 으로 재직렬화한 뒤 `exifread details=True` 로
   읽었다: 네 경우 모두 **63/63 태그, 값까지 전부 동일**.

⚠️**후지 한정이다.** 니콘·캐논은 MakerNote 형식이 달라(절대 오프셋을 쓰는 것이 있다)
2단계에서 기종별로 다시 재야 한다.

### GPS 우선순위

```
사용자가 Location 탭에서 붙인 좌표  >  원본 EXIF 의 GPS(= keep_gps 가 결정)  >  없음
```

⚠️사이드카의 `gpsLat` 키가 **있으면(값이 null 이어도) 그것이 답이다** — 지오태깅 문서의 규칙과
같다. 사용자가 위치를 **지운** 사진에서 원본 EXIF GPS 가 되살아나면 안 된다.

⚠️**두 좌표를 가르는 것은 `gpsSrc` 하나다.** `Controller._load` 는 사이드카에 좌표가 없으면
파일의 EXIF GPS 를 `_gps` 에 채우고 출처를 `"exif"` 로 적는데, `gpsSet` 만 보면 이 좌표가
사용자가 찍은 핀과 구분되지 않는다. 구분이 없으면 `build_app1` 의 `if gps:` 분기가
`keep_gps` 를 무조건 이기므로 **체크를 꺼도 카메라가 남긴 촬영 위치가 그대로 실린다**(체크박스
툴팁이 약속하는 바로 그 경우 — 아이폰/드론 DNG 에서 실측으로 확인). 그래서
`pipeline.gps_from_params` 가 `gpsSrc == "exif"` 를 **좌표 없음으로 친다**(CPU/GPU 공용 단일
판정 지점). 걸러 두면 `keep_gps=True` 일 때 원본 GPS IFD 가 통째로 통과해 좌표뿐 아니라
`GPSTimeStamp`/`GPSDateStamp`/방위각까지 살아남는다 — 그 통과 분기의 원래 의도다.

⚠️따라서 `"exif"` 는 **그 파일에 카메라가 직접 남긴 좌표**라는 뜻으로만 써야 한다. 일괄 적용
(`applyGpsToPaths`)은 사람이 남의 사진에 위치를 붙이는 행위라, 패널 초안이 물고 온 `"exif"`
라벨을 `"manual"` 로 바꿔 쓴다. 안 그러면 받은 사진들이 '카메라가 남긴 좌표'로 위장돼 export
에서 조용히 빠진다.

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
7. ★**MakerNote 대조** — `exifread details=True` 로 소스와 export 의 `MakerNote *` 태그가
   **63개 모두, 값까지** 같은지. 재직렬화가 blob 을 옮기므로 여기서 회귀가 난다
8. **APP1 크기** — `<= 65533 B` 확인. 넘으면 크레딧 전용으로 폴백(위 폴백 규칙).
   후지는 재직렬화하면 ~3 KB 라 여유가 크지만, **라이카 MakerNote 28652 B** 같은 기종이 있어
   가드 자체는 지운다는 뜻이 아니다
9. `python xplat_check.py` 기준선 **17/19** 유지

---

## 2단계 — 타사 RAW 재구축 (조건부)

`exif_info._exif_tags()` 가 주는 dict(NEF 55 / DNG 82 태그)로 IFD0+ExifIFD 를 **조립**한다.
`_ifd_block` 이 이미 RATIONAL 과 인라인을 지원하므로(**단 1단계의 파서가 선행이다** — 위 ★)
남는 일은 태그 매핑 약 13개
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

(MakerNote 는 결정이 끝났다 — 실측으로 이동 가능함이 확인돼 **유지**다. 위 '태그 정책' 참조.
후지 레시피 읽기 기능의 토대가 되기도 한다.)

1. **원본 EXIF 의 GPS 를 통과시킬까?** 지도 뷰의 핵심이지만 집 위치가 공유 파일에 박힌다.
   → 기본 ON + 끄는 토글을 권함. 지오태깅 UI 옆에 두는 게 자연스럽다
2. **크레딧 vs 통과 충돌** — 재직렬화라 슬롯 길이 제약은 사라졌다. `Software` 는 우리 것으로
   교체하는 것이 맞다(현상기가 우리다). 원본 `Software`(카메라 펌웨어 버전)를 어딘가 남길지는
   선택 — 남긴다면 `0x000B ProcessingSoftware` 가 자리다

---

## 참고

- 지오태깅 설계·함정: [`geotagging.md`](geotagging.md)
- 렌더 경로 4중 계약·export 규칙: `../CLAUDE.md`
- 관련 코드: `pipeline.py`(`_ifd_block`, `_exif_app1`, `_insert_app1`, `save_image`),
  `exif_info.py`(`embedded_preview_jpeg`, `_exif_tags`, `read_gps`)
