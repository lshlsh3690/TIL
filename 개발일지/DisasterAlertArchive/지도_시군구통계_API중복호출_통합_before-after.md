# 지도(KakaoPolygonMap) 시군구 통계 API 중복 호출 통합 — Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults` (base: `develop`)
- 관련 커밋: `0e80b2e` perf(map): 지도 시/도 선택 시 sigungu 통계 API 4중 호출을 1회로 통합
- 관련 문서: [프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) (React Query 캐시 설정, 이 작업의 이전 단계) 의 "다음 단계" 1번 항목

---

## 1. 문제 (Before)

`/alerts` 페이지 지도에서 시/도 하나를 선택할 때마다, 같은 계열의 시군구 통계 API가 여러 번 동시에 호출되고 있었다.

### 1-1. 원인: 레벨별로 4번 나눠 부르던 구조

`frontend/src/components/map/KakaoPolygonMap.tsx` (수정 전):

```ts
const sigunguStatsQuery = useSigunguStats({ ...params, region: ... }, !!selectedSido);
const sigunguStatsL1    = useSigunguStats({ ...params, region: ..., level: "LEVEL_1" }, !!selectedSido);
const sigunguStatsL2    = useSigunguStats({ ...params, region: ..., level: "LEVEL_2" }, !!selectedSido);
const sigunguStatsL3    = useSigunguStats({ ...params, region: ..., level: "LEVEL_3" }, !!selectedSido);
```

지도 사이드바에 "안전안내/긴급재난/위급재난" 레벨별 건수를 보여주기 위해, 같은 `/api/v1/alerts/stats/sigungu` 엔드포인트를 `level` 파라미터만 바꿔가며 4번(전체 1회 + 레벨별 3회) 호출하는 구조였다. 여기에 같은 페이지의 `frontend/src/app/alerts/page.tsx:151`이 별도 목적(최다 발생 시군구 계산)으로 이 API를 1번 더 호출하고 있어서, **시/도 하나를 선택할 때마다 동일 계열 API가 최대 5건 동시에 발화**했다.

### 1-2. 백엔드도 같은 쿼리를 4번 실행

각 프론트 호출은 백엔드에서 `DisasterAlertRepositoryImpl.getStatsSigungu()`가 매번 WHERE절의 `level` 필터만 바꿔 재실행하는 구조였다 (`byAlertCondition()` 안의 `levelEq(level)`). DB 입장에서도 사실상 같은 GROUP BY 집계 쿼리를 4번 실행한 셈이라, 프론트 네트워크 요청 수뿐 아니라 DB 부하도 4배였다.

---

## 2. 해결 (After)

### 2-1. 백엔드: 시군구 × 레벨 breakdown을 한 쿼리로

새 엔드포인트 `/api/v1/alerts/stats/sigungu/breakdown`을 추가해, WHERE절 필터 대신 SELECT 단계에서 레벨별로 조건부 집계하도록 바꿨다.

`backend/.../DisasterAlertRepositoryImpl.java` (`getStatsSigunguBreakdown`, 핵심 부분):

```java
NumberExpression<Long> level1Count = new CaseBuilder()
        .when(disasterAlert.emergencyLevel.eq(DisasterLevel.LEVEL_1)).then(disasterAlert.id)
        .otherwise((Long) null)
        .countDistinct();
// level2Count, level3Count도 동일 패턴

return queryFactory
        .select(Projections.constructor(DisasterAlertStatResponse.RegionLevelStat.class,
                sigungu, disasterAlert.id.countDistinct(), level1Count, level2Count, level3Count))
        .from(disasterAlert)
        .join(...).join(...)
        .where(byAlertCondition(request), regionFilterOnJoin(request))
        .groupBy(sigungu)
        .fetch();
```

`SUM(CASE ...)`이 아니라 `COUNT(DISTINCT CASE WHEN ... THEN id END)`를 쓴 이유: 이 쿼리는 `disasterAlertRegions`/`legalDistrict`와 조인하는데, 한 알림이 같은 시군구에 여러 법정동 행으로 매핑되면 조인 시 행이 늘어나는(fan-out) 경우가 있다. `SUM(CASE WHEN level=X THEN 1 ELSE 0)`이면 이런 경우 중복 카운트되지만, `COUNT(DISTINCT CASE WHEN level=X THEN id END)`는 알림 id 기준으로 중복 제거되어 기존 4회 분리 쿼리(각각 `countDistinct` 사용)와 정확히 같은 값을 낸다.

응답의 `level` 파라미터는 호출자가 실어 보내더라도 서버에서 무시하도록 `request.setLevel(null)`을 명시적으로 넣어, "이 엔드포인트는 항상 전체 레벨 breakdown을 반환한다"는 계약을 코드로 보장했다 (`DisasterAlertRepositoryImpl.java`).

기존 `/api/v1/alerts/stats/sigungu`(레벨 없는 전체 카운트 — `alerts/page.tsx`, `stats/page.tsx`에서 계속 사용 중)는 응답 계약을 그대로 유지하기 위해 건드리지 않았다.

### 2-2. 프론트: 4개 훅 호출 → 1개 훅 호출

`frontend/src/components/map/KakaoPolygonMap.tsx` (수정 후):

```ts
const sigunguStatsQuery = useSigunguStatsBreakdown(
  { ...params, region: selectedSido?.properties.CTP_KOR_NM ?? undefined },
  !!selectedSido
);
```

기존에 useEffect 2개(전체용 1개 + 레벨별용 1개, 총 4개 쿼리를 구독)로 나뉘어 있던 로직을 breakdown 응답 하나로부터 `total`/`level1Count`/`level2Count`/`level3Count`를 한 번에 파싱하는 useEffect 1개로 합쳤다.

---

## 3. 실측 결과 (Before → After)

Playwright(Chromium)로 실제 dev 서버 + 로컬 DB(RDS 개발 인스턴스)에 붙여 `/alerts` 페이지에서 "시/도(전체)" 드롭다운을 "서울특별시"로 바꾸고 검색을 눌렀을 때 발생하는 네트워크 요청을 직접 캡처해 확인했다 (모킹 없이 실제 백엔드 응답).

| 항목 | Before | After |
|---|---|---|
| 시/도 선택 시 `sigungu` 계열 API 호출 수 | **5건** (`KakaoPolygonMap` 4건 + `alerts/page.tsx` 1건) | **2건** (`KakaoPolygonMap` 1건 + `alerts/page.tsx` 1건, 변경 없음) |
| `KakaoPolygonMap`이 유발하는 호출 수 | 4건 | **1건 (-75%)** |
| 응답 데이터 정합성 | — | breakdown 응답의 `total`이 기존 무필터 API의 `count`와, `level1/2/3Count`가 기존 `level=LEVEL_n` API의 `count`와 **동일 값**으로 교차 검증됨 (예: 서울특별시 total=753, level1=750, level2=3, level3=0 — 4개 분리 API 응답과 일치) |

실제 캡처된 요청 (서울특별시 선택 후):
```
GET /api/v1/alerts/stats/sigungu?source=ALL&region=서울특별시            ← alerts/page.tsx (기존, 변경 없음)
GET /api/v1/alerts/stats/sigungu/breakdown?source=ALL&region=서울특별시  ← KakaoPolygonMap (신규, 4건→1건)
```

**해석**: 시/도를 바꾸거나 필터를 조정할 때마다 지도 쪽 요청이 4건에서 1건으로 줄어, 해당 상호작용에서 발생하던 네트워크 요청이 75% 감소했다. 이 4건은 원래도 거의 동시에 발화했으므로 체감 지연(가장 늦게 도착하는 응답까지 걸리는 시간) 자체보다는 **네트워크 요청 수 자체와 DB 부하(동일 GROUP BY 쿼리 4회→1회)가 줄어든 것**이 핵심 개선점이다.

---

## 4. 검증

- `./gradlew compileJava` 통과 (백엔드).
- `npx tsc --noEmit`, `npx eslint` 통과 (프론트 — 기존에 있던 Kakao SDK `any` 관련 lint 경고 16건은 이번 변경과 무관하게 그대로 남아있음, 새로 추가된 위반 없음).
- 위 3장의 실측으로 실제 요청 감소와 데이터 정합성을 백엔드 실행 결과로 직접 확인.
- 변경 파일: 백엔드 6개(DTO/컨트롤러/서비스/레포지토리 인터페이스·구현/캐시 이름 상수), 프론트 4개(타입/axios 래퍼/쿼리 훅/컴포넌트).

---

## 5. 남은 항목

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장에 정리된 나머지 항목:

1. ~~`KakaoPolygonMap` 4중 호출 통합~~ — **완료 (이 문서)**
2. 워터폴 제거 — `AlertRiskSection`(위험도 섹션), `events/[id]` 페이지의 순차 fetch
3. SSR prefetch 도입 — 핵심 목록 페이지 최초 로딩 속도 개선
4. 무거운 컴포넌트(`KakaoPolygonMap`, `KoreaMap25D`, stats 차트) `next/dynamic` 전환
