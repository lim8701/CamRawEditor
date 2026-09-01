"""GPX 트랙 x 촬영시각 매칭 — 휴대폰으로 기록한 경로로 한 롤을 지오태깅한다.

카메라의 블루투스 연결이 끊기기 쉬워 촬영 시점 지오태깅이 사실상 불가능하다. 대신 대부분의
사람이 주머니에 로거(휴대폰)를 갖고 다니므로, 그 트랙과 EXIF 촬영시각을 맞춰 위치를 복원한다.

⚠️**EXIF 촬영시각에는 시간대가 없다**(`DateTimeOriginal` 은 카메라 로컬시다). 그래서 호출부가
  UTC 오프셋을 반드시 넘겨야 하고, 카메라 시계 오차용 미세 보정도 그 안에 합쳐 넣는다.
  이 값 없이는 매칭이 원리적으로 불가능하다 — 추정하지 않는다.

표준 라이브러리만 쓴다(xml.etree). 새 의존성 없음.
"""
import datetime
import xml.etree.ElementTree as ET

# 트랙 점과 촬영시각이 이만큼 넘게 떨어져 있으면 **매칭 실패로 돌려준다.**
# 로거를 껐던 구간이나 트랙과 무관한 날 찍은 사진에 엉뚱한 좌표를 붙이는 것보다,
# 아무것도 안 붙이는 쪽이 낫다(위치는 '대충'이 의미 없는 값이다).
DEFAULT_TOLERANCE_SEC = 120


def _parse_time(text: str):
    """GPX `<time>` (ISO 8601, 보통 `...Z`) -> UTC epoch 초. 실패 시 None."""
    if not text:
        return None
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        # 소수 초 자릿수가 6을 넘는 로거가 있다(fromisoformat 이 거부한다) -> 6자리로 자른다.
        try:
            head, _, tail = t.partition(".")
            if not tail:
                return None
            off, cut = "+00:00", len(tail)
            for sign in ("+", "-"):
                i = tail.find(sign)
                if i > 0:
                    off, cut = tail[i:], i
                    break
            frac = "".join(c for c in tail[:cut] if c.isdigit())[:6] or "0"
            dt = datetime.datetime.fromisoformat(f"{head}.{frac}{off}")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)   # 시간대 없는 GPX 는 UTC 로 본다(스펙)
    return dt.timestamp()


def parse(path) -> list:
    """.gpx -> `[(utc_epoch, lat, lon, ele|None), ...]` 시각 오름차순.

    `<trkpt>` 와 `<wpt>` 를 모두 본다(로거에 따라 다르다). 시각이 없는 점은 매칭에 못 쓰므로
    버린다. 네임스페이스는 GPX 1.0/1.1 이 다르므로 태그 로컬명으로 비교한다.
    """
    pts = []
    for _, el in ET.iterparse(str(path), events=("end",)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("trkpt", "wpt"):
            continue
        try:
            lat, lon = float(el.get("lat")), float(el.get("lon"))
        except (TypeError, ValueError):
            el.clear()
            continue
        t = ele = None
        for ch in el:
            name = ch.tag.rsplit("}", 1)[-1]
            if name == "time":
                t = _parse_time(ch.text)
            elif name == "ele":
                try:
                    ele = float(ch.text)
                except (TypeError, ValueError):
                    ele = None
        el.clear()
        if t is not None:
            pts.append((t, lat, lon, ele))
    pts.sort(key=lambda p: p[0])
    return pts


def _lerp(a, b, f):
    return a + (b - a) * f


def match(track: list, shot_epoch: float, tolerance_sec: int = DEFAULT_TOLERANCE_SEC):
    """트랙에서 `shot_epoch`(UTC) 위치를 보간해 `(lat, lon, alt|None)`. 못 맞추면 None.

    앞뒤 두 점 사이는 선형 보간한다. 트랙 **밖**(첫 점 이전 / 마지막 점 이후)이거나 가장 가까운
    점이 `tolerance_sec` 보다 멀면 **None** — 추측해서 붙이지 않는다.

    ⚠️경도 보간은 날짜변경선을 넘어가는 구간에서 틀린다(179 -> -179 를 지구 반 바퀴로 읽는다).
      두 점의 경도 차가 180도를 넘으면 보간하지 않고 **가까운 점을 그대로 쓴다** — 그 간격에서
      1~2초 사이 위치차는 어차피 무의미하다.
    """
    n = len(track)
    if n == 0:
        return None
    if n == 1:
        t0, la, lo, el = track[0]
        return (la, lo, el) if abs(shot_epoch - t0) <= tolerance_sec else None

    # 오른쪽 이웃 찾기(이분 탐색).
    lo_i, hi_i = 0, n - 1
    while lo_i < hi_i:
        mid = (lo_i + hi_i) // 2
        if track[mid][0] < shot_epoch:
            lo_i = mid + 1
        else:
            hi_i = mid
    j = lo_i                       # track[j][0] >= shot_epoch (또는 j == n-1)
    i = max(0, j - 1)

    if shot_epoch < track[0][0] or shot_epoch > track[-1][0]:
        # 트랙 밖 — 끝점과 충분히 가까울 때만(로거를 사진 직후에 켠 경우 등) 그 점을 쓴다.
        end = track[0] if shot_epoch < track[0][0] else track[-1]
        if abs(shot_epoch - end[0]) > tolerance_sec:
            return None
        return (end[1], end[2], end[3])

    t0, la0, lo0, el0 = track[i]
    t1, la1, lo1, el1 = track[j]
    if t1 == t0:
        return (la0, lo0, el0)
    if min(abs(shot_epoch - t0), abs(shot_epoch - t1)) > tolerance_sec:
        return None                # 두 점 사이 간격이 커서 그 안 어디에 있었는지 모른다
    near = track[i] if abs(shot_epoch - t0) <= abs(shot_epoch - t1) else track[j]
    if abs(lo1 - lo0) > 180.0:
        return (near[1], near[2], near[3])       # 날짜변경선 — 보간하지 않는다(위 주석)
    f = (shot_epoch - t0) / (t1 - t0)
    alt = None if (el0 is None or el1 is None) else _lerp(el0, el1, f)
    return (_lerp(la0, la1, f), _lerp(lo0, lo1, f), alt)


def shot_epoch(exif_date: str, utc_offset_sec: int):
    """`exif_info` 가 만든 `"YYYY-MM-DD HH:MM:SS"` + UTC 오프셋 -> UTC epoch. 실패 시 None.

    ⚠️오프셋은 **카메라 시계가 어느 시간대였는가** + 시계 오차 보정을 합친 값이다
      (EXIF 자체에는 시간대가 없다 — 모듈 주석).
    """
    if not exif_date:
        return None
    try:
        dt = datetime.datetime.strptime(str(exif_date).strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp() - float(utc_offset_sec)
