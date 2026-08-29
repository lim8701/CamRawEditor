# RAW 디모자이크 정책 (결정 기록)

## 현재 정책 (2026-08-29 갱신)
- **프록시(프리뷰)**: 항상 `LINEAR`(쌍선형). full 디코드 후 max_edge 2560 으로 축소 —
  축소되므로 디모자이크 화질이 체감에 거의 영향 없음, 속도 우선.
- **Export(풀해상도, CPU `pipeline.render_full` + GPU `raw_loader.load_full`)**:
  - **Bayer(2×2 CFA)** = `AHD` — 쌍선형 대비 색 모아레·지퍼링·경계 무름 개선(Canon/Nikon/Sony 등).
  - **X-Trans(6×6)** = `AHD` = **Markesteijn 3-pass** (LibRaw 가 X-Trans 에서 quality>2 를
    Markesteijn 3-pass 로 실행 — 아래 매핑 절). 2026-08-29 육안 증거로 LINEAR 에서 전환.
  - **CFA 없음/이형(None, Foveon, 모노 등)** = `LINEAR`(안전 폴백).
- 판별: `raw_loader._export_demosaic(raw)` — `raw_pattern.shape` 가 (2,2) 또는 (6,6)이면 AHD.

## 이렇게 정한 이유
- 디모자이크 화질이 실제로 중요한 곳은 **풀해상도 export**(100% 확인). 프록시는 2560 축소라
  미세 디테일이 어차피 사라져 화질 영향이 작음.
- 그래서 "화질이 필요한 곳(export)만 고품질, 속도가 중요한 프록시는 LINEAR" 로 균형.

## 알려진 트레이드오프 (수용됨)
- Bayer·X-Trans 모두 **프록시(LINEAR 축소) ↔ export(고품질)** 의 텍스처/샤픈/NR **미세 결이
  살짝 다름**. 프록시가 저해상도라 체감은 작지만, 프리뷰=Export 원칙에 대한 부분적 예외.
  (Bayer 는 2026-07 부터, X-Trans 는 2026-08-29 부터.)

## X-Trans 알고리즘 실측 (2026-08) — 결론: **변경 보류**

RAW Peek(`R`)의 Demosaic 패널을 만들면서 X-Trans 4장(X100V x2 · X-T5 · X100VI) x 각 4크롭으로
LibRaw 지원 7종을 측정했다. ⚠️**무참조(no-reference) 지표다** — LibRaw 는 RAW 파일만 읽어
합성 모자이크를 넣을 수 없으므로 정답 대조(PSNR/SSIM)가 불가능하다. 잰 것은 '정답에 가까운가'가
아니라 '아티팩트가 얼마나 남는가'다.

| | 위색(크로마HF/루마HF) ↓ | 디테일(루마HF) ↑ | 디코드 |
|---|---|---|---|
| **LINEAR** (현재 X-Trans 정책) | **0.951** 최악 | **0.755** 최저 | 1.49s |
| VNG | **0.431** 최선 | 1.022 | 5.16s |
| PPG | 0.511 | **1.081** | 2.65s |
| AHD ≈ AAHD ≈ DCB ≈ DHT | 0.508 | **1.083** | 4.41~4.71s |

- **LINEAR 이 두 축 모두에서 측정상 최악**이다: 디테일 −30%, 위색 2배. 4파일 전부 같은 방향.
- CFA 6px 격자(미로 무늬) 지표는 **어느 알고리즘도 뚜렷한 봉우리를 남기지 않았다**(0.86~0.95,
  1.0=봉우리 없음) → 이 축은 판별력이 없었다.
- 품질/속도 최적은 **PPG**(AHD 계열과 통계적으로 동일한데 1.7배 빠르다).
- ⚠️X-Trans 에서 **AHD/AAHD/DCB/DHT 는 실질 동일**하다: 다른 픽셀 0.04~0.08%, 평균 절대차
  0.01~0.05 코드(16bit). `array_equal` 은 False 를 주지만 반올림 수준이다.
  (PPG vs AHD 는 다른 픽셀 21.7% / 평균 17.3 코드로 **진짜** 다르다.)

### ★rawpy 라벨 ≠ 실제 알고리즘 — X-Trans 는 Markesteijn 이 이미 측정돼 있었다 (2026-08 확인)

LibRaw 디스패치(`dcraw_process.cpp`)는 X-Trans(filters==9)에서 rawpy 가 넘긴 quality 를
**Bayer 와 다른 알고리즘으로** 해석한다:

| rawpy 라벨(quality) | X-Trans 에서 실제 실행 |
|---|---|
| LINEAR(0) | bilinear |
| VNG(1) | VNG |
| PPG(2) | **Markesteijn 1-pass** |
| AHD(3)·DCB·DHT·AAHD (quality>2 전부) | **Markesteijn 3-pass** |

그래서 위 표의 "AHD≈AAHD≈DCB≈DHT 실질 동일"은 당연했다 — **넷 다 같은 Markesteijn 3-pass**다.

⚠️**Markesteijn 3-pass 는 실행 간 비결정이다**(2026-08-29 실측, DSCF8035): 같은 파일·같은
파라미터로 두 번 디코드해도 **0.003~0.02% 픽셀이 다르다**(전체 평균 |Δ| 0.003~0.015 코드,
고립 픽셀에서 드물게 |Δ| 최대 ~2800 코드/16bit — LINEAR 는 완전 결정적). LibRaw OpenMP 병렬
탓으로 보인다. 함의: ①X-Trans 에서 **CPU export 와 GPU export 의 디코드는 비트 동일이 아니다**
(룩 동일 — 지터가 육안 무관 규모). ②디코드 결과의 비트 비교 검증은 `array_equal` 이 아니라
**지터 규모(다른 픽셀 <0.1%·평균 |Δ| <0.1 코드)로 판정**할 것. 위 표의 "AHD≈DCB≈DHT 미세차
0.04~0.08%"의 정체도 이 지터다.
재해석하면: "PPG 최적" = **Markesteijn 1-pass 가 품질/속도 최적**, "AHD 계열" = Markesteijn
3-pass. 즉 X-Trans 표준(Markesteijn)은 미구현 후보가 아니라 **rawpy 로 즉시 사용 가능**하고
이미 측정까지 끝나 있다. 참고로 darktable/RT 의 최근 결합형(dual demosaic = Markesteijn
3-pass + VNG4 로컬 가중 블렌드)은 LibRaw 에 없다 — 도입하려면 2회 디코드+numpy 블렌드 자체
구현이고, 평탄부 노이즈는 이 앱에선 AI 디노이즈 담당이라 실익이 작다.

### ★결정: 지금은 바꾸지 않는다 (2026-08, 사용자)

수치는 LINEAR 가 불리하다고 말하지만, **앱에서 나란히 놓고 봐도 눈으로 판별되지 않았다**
("내눈엔 잘 모르겠다"). 그래서 export 알고리즘 변경은 **보류**한다 — 기각이 아니라 보류다.

- 다시 열 때 필요한 것은 **눈에 보이는 증거**다(같은 사진 100% 확대 비교, 미세 디테일이 있는
  장면). 위 표를 근거로 곧바로 바꾸지 말 것 — 무참조 지표이고 시각적 확인이 안 된 상태다.
- 바꾸기로 하면 대상은 **export 뿐**이다(프록시는 2560 축소라 디테일이 덜 중요하고 속도가 중요).
  비용은 export 디코드 1.49s → 2.65s(PPG)인데 export 전체가 40~50s 이라 무의미한 증가다.
  그러면 X-Trans 도 Bayer 처럼 프록시(LINEAR)↔export 미세결 차이를 수용하게 된다.
- 측정 스크립트와 지표 정의는 `docs/raw_peek.md` 의 Demosaic 절 참조.

### ★보류 해제 → 전환 (2026-08-29, 사용자)

위 재개 조건(눈에 보이는 증거)을 채웠다: X100V 2장(DSCF8035·DSCF1839)·X-T5(FXT50017)·
X100VI(_DSF0470)를 export 계약 그대로(렌즈 워프·filmic 제외 — 리샘플 없는 공정 비교) LINEAR /
Markesteijn 1-pass / 3-pass 로 디코드해 고주파 크롭 3곳+평탄부 1곳씩 **200% nearest 블라인드
패널**로 비교 — 잔가지·잎 디테일에서 LINEAR 의 무름이 육안 판별됐고 사용자가 전환을 결정했다.

- 적용: `_export_demosaic` 이 X-Trans(6×6)에도 `AHD`(=Markesteijn 3-pass) 반환. 프록시 무변경.
- 디코드 시간 실측: LINEAR 0.7~1.7s → Mark3 3.9~5.4s(40MP 포함) — export 전체 40~50s 대비 미미.
- Mark1↔Mark3 차이는 육안으로 미미했으나, 비용 차 ~2s 라 표준(3-pass)을 채택.

**알려진 특성 — 평탄부 노이즈가 '결'로 보인다**: Markesteijn 은 방향성 보간이라 평탄부의 샷
노이즈를 짧은 지렁이/미로 모양 텍스처로 조직한다(모든 주요 현상기의 Markesteijn 공통 특성 —
darktable 의 dual demosaic(+VNG4)이 겨냥하는 바로 그 항목). bilinear 는 같은 노이즈를 뭉개서
'매끈해 보일' 뿐이다(디테일도 같이 뭉갠다). CFA 격자 고정 패턴은 아니다(위상 고정 에너지는
오히려 LINEAR 보다 낮게 실측). 고ISO 평탄부가 거슬리면 AI 디노이즈가 담당(그게 이 앱의
분업이다).

⚠️예전 RAW Peek Demosaic 패널은 이것을 크게 과장했다 — 유니티 WB(초록 캐스트)+선형+p99→0.9
상시 표시 게인+nearest 확대 조건이라, 같은 크롭을 export 계약으로 보면 훨씬 미묘했다
(2026-08-29 나란히 실측). 게다가 WB 는 디모자이크 **전에** 곱해지므로(LibRaw scale_colors)
유니티 WB 디코드는 export 가 실행하는 디모자이크와 입력부터 달랐다 — '정책 검증 계측기'가
정책이 실제로 겪는 조건을 재지 않던 것. **같은 날 패널을 export 계약 디코드(TREF WB+감마,
flip=0 만 유지)로 고쳤고 표시 게인은 Display gain 토글을 따른다**(`raw_peek._dm_get`).

## 추후 재검토 트리거 (다시 고민할 시점)
- 프리뷰=Export 정밀 정합이 Bayer 에서도 요구될 때 → 옵션 C(프록시도 Bayer AHD, 단 디코드 느려짐).
- X-Trans 고품질(Markesteijn = rawpy 라벨 PPG/AHD, 위 매핑 절 참조) 전환 검토 시 —
  재개 조건은 100% 크롭 육안 증거.
- 프록시 half_size 도입 등 디코드 파이프라인 개편 시 함께 재평가.

관련 코드: `raw_loader.py`(`_export_demosaic`, `_decode_native(export_quality)`, `load_full`),
`pipeline.py`(`render_full`). 관련 히스토리: 1차 검토에서 export 를 프록시와 맞추려 LINEAR 로 고정한 커밋.
