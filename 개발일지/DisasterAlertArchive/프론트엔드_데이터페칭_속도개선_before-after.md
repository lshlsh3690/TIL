# 프론트엔드 데이터 페칭 속도 개선 — Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults` (base: `develop`)
- 관련 커밋: `504b55d` perf(frontend): React Query 전역 기본 staleTime 설정으로 불필요한 재요청 제거
- 파일: `frontend/src/lib/queryClient.ts`

---

## 1. 문제 제기

"프론트엔드 데이터 뜨는 속도가 느리다"는 체감을 조사한 결과, 원인이 하나가 아니라 여러 겹으로 쌓여 있었음을 확인. 이번 작업은 그중 **가장 파급력이 크고 리스크가 낮은 1건(React Query 전역 캐시 설정)**을 우선 적용한 기록이며, 나머지 항목은 4장에 다음 단계로 정리.

---

## 2. 원인 분석 (Before)

### 2-1. 핵심 원인: QueryClient에 전역 기본 옵션이 없었음

`frontend/src/lib/queryClient.ts` (수정 전 전체):

```ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient();
```

`new QueryClient()`를 옵션 없이 생성하면 `@tanstack/react-query`의 라이브러리 기본값이 그대로 적용된다.

| 옵션 | 라이브러리 기본값 | 의미 |
|---|---|---|
| `staleTime` | `0` | 데이터를 받아온 즉시 "stale(오래됨)" 상태가 됨 |
| `refetchOnWindowFocus` | `true` | 브라우저 탭/창이 다시 포커스될 때마다 stale한 쿼리를 자동 재요청 |
| `gcTime` | `5분` | 언마운트된 쿼리를 캐시에서 메모리 해제하기까지의 시간 |

React Query는 "쿼리가 stale하면, 해당 쿼리를 구독하는 컴포넌트가 새로 마운트되거나 창이 포커스될 때 자동으로 재요청"하는 것이 기본 동작이다. `staleTime: 0`이면 **데이터를 받은 바로 다음 순간부터 이미 stale** 상태이므로, 사실상 "컴포넌트가 리마운트될 때마다 무조건 네트워크 재요청"과 동일하게 동작한다.

이 프로젝트는 SPA 라우팅(Next.js App Router, 전 페이지 `"use client"`)을 쓰기 때문에, 사용자가

- 목록 페이지 → 상세 페이지 → 뒤로가기(목록 페이지 재마운트)
- 다른 탭으로 갔다가 다시 이 탭으로 포커스
- 필터를 살짝 바꿨다가 원래 값으로 되돌림

같은 아주 흔한 조작만 해도 방금 받아온 데이터를 버리고 다시 네트워크를 타는 구조였다.

### 2-2. 영향받은 범위

`frontend/src/lib/queries/` 아래 훅들을 전수 조사한 결과, `staleTime`을 개별적으로 지정한 훅(`useLatestAlerts`, `useAlertStats`, `useDailyStats`, `useDashboardSummary` 등 일부)만 캐시가 실질적으로 동작했고, 나머지 다수는 기본값(0)에 노출되어 있었다:

- `useAlerts.ts` — `useSearchAlerts`, `useSearchCombinedAlerts` (alerts 목록 페이지 핵심 쿼리), `useAlert`(상세), `useSidoStats`, `useUserAlerts`
- `useEvents.ts` — `useSearchEvents`, `useEvent`
- `useCommunity.ts` — `useCommunityPosts`
- `useComments.ts`
- `useFavoriteRegions.ts`

즉 **alerts/events/community 목록·상세를 포함한 사실상 모든 핵심 화면**이 "탭 전환 한 번, 페이지 재방문 한 번마다 재요청"에 노출되어 있었다.

### 2-3. 추가로 확인된 부수 원인 (이번 작업 범위 밖, 3-2 참고)

같은 조사에서 다음도 함께 발견됐으나 이번 커밋에는 포함하지 않음 (범위를 좁혀 리스크를 낮추기 위해 1건만 우선 적용):

- `KakaoPolygonMap.tsx:188-203` — 지도에서 시/도 하나 선택 시 `useSigunguStats`가 base/L1/L2/L3 총 4회, 여기에 `alerts/page.tsx:151`의 1회까지 더해 **동일 계열 API가 5건 동시 발화**.
- `AlertRiskSection.tsx:236-237`, `events/[id]/page.tsx:32-47` — 앞 쿼리 응답을 받아야 다음 쿼리가 시작되는 **순차 워터폴** 구조.
- 전 페이지 `"use client"` — SSR prefetch(`dehydrate`/`HydrationBoundary`) 패턴이 전무해 **JS 로드 → hydrate → 쿼리 시작** 순서로 초기 요청 자체가 늦게 출발.
- `KakaoPolygonMap`, `KoreaMap25D` 등 무거운 컴포넌트가 `next/dynamic` 없이 정적 import되어 초기 번들이 커짐.

---

## 3. 적용한 변경 (After)

### 3-1. 수정 내용

`frontend/src/lib/queryClient.ts`:

```ts
import { QueryClient } from "@tanstack/react-query";

// 기본값(staleTime: 0, refetchOnWindowFocus: true)이 페이지 재방문·탭 전환마다
// 불필요한 재요청을 유발해 전역 기본 staleTime을 지정. 실시간성이 필요한 쿼리는
// 훅 단에서 개별적으로 더 짧은 staleTime을 지정해 오버라이드한다.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});
```

> **변경 이력**: 최초 적용 시 `staleTime: 30_000`(30초)이었으나, 사용자 판단에 따라 `60_000`(1분)으로 상향 조정함(2026-07-27). 30초보다 캐시 유지 시간을 늘려 재요청 절감 효과를 더 키우는 방향. 아래 본문의 "30초" 서술은 최초 적용 당시 근거를 그대로 남기되, 현재 코드 값은 60초임에 유의.

### 3-2. 왜 이렇게 바꿨는가

- **`staleTime: 60_000`(1분)** — 데이터를 1분간은 "신선함"으로 간주해, 그 안에 재마운트/재방문이 일어나도 네트워크를 타지 않고 캐시를 즉시 반환한다. 재난문자 데이터는 초 단위 실시간성이 필수는 아니라서(수집 스케줄러 주기가 분 단위), 1분은 "체감 지연 제거"와 "너무 오래된 데이터를 보여주는 리스크" 사이의 절충값으로 선택(최초에는 30초로 적용했다가 캐시 유지 구간을 넓히기 위해 1분으로 상향). 이미 개별 훅에서 `staleTime: 60_000` 등을 지정한 곳은 그대로 우선 적용되므로 충돌 없음(React Query는 훅 단 옵션이 전역 기본값을 오버라이드).
- **`refetchOnWindowFocus: false`** — 브라우저 탭 전환/복귀만으로 전체 화면의 쿼리가 재요청되는 것을 막음. 이미 `frontend/src/app/user/me/page.tsx:22`에서 동일한 이유로 개별 지정돼 있던 패턴을 전역화한 것 — 즉 이 프로젝트에서 이미 검증된 선택.
- **개별 훅의 `staleTime`/`enabled`는 그대로 둠** — 실시간성이 더 필요하다고 판단해 짧게 잡은 값이나, `enabled` 기반 워터폴 로직은 이번 변경과 무관하게 유지. 전역값은 "지정 안 한 쿼리들의 최소 안전장치"로만 작동.

### 3-3. 효과가 발생하는 구체적 시나리오

React Query의 결정론적 동작(공식 문서 기준)에 따라 다음 상황에서 네트워크 요청 발화 여부가 바뀐다:

| 사용자 행동 | Before (staleTime: 0) | After (staleTime: 60s) |
|---|---|---|
| alerts 목록 → 상세 → 뒤로가기 (1분 이내) | 목록 쿼리 재요청 발생 | 캐시에서 즉시 렌더, 요청 없음 |
| 다른 탭 갔다가 이 탭으로 복귀 | 화면의 모든 쿼리 일괄 재요청 | 재요청 없음 |
| 필터 값을 바꿨다가 즉시 원래 값으로 복귀 | 두 번 모두 네트워크 요청 | 캐시 히트, 요청 없음 |
| 첫 진입, 또는 마지막 요청 후 1분 경과 | 요청 발생 | 동일하게 요청 발생 (차이 없음 — 최초 로딩 속도 자체는 이 변경으로 개선되지 않음) |

**주의**: 이번 변경은 "재방문 시 불필요한 재요청 제거"가 목적이며, **최초 페이지 진입 시 첫 데이터 로딩 속도 자체는 개선하지 않는다.** 최초 로딩 속도 개선은 4장의 SSR prefetch(3-3 항목)가 담당.

### 3-4. 실측 결과 (before / after, 초·% 단위)

"alerts 목록 → 홈 → alerts 재방문(수 초 이내)" 시나리오를 실제 개발 서버(Next.js dev, Turbopack) + Playwright(Chromium)로 자동화해 측정. 측정 방법과 원본 수치는 6장 참고.

> 이 실측은 `staleTime: 30_000`(최초 적용값) 기준으로 수행했다. 재방문이 클릭 후 수 초 이내에 이뤄지는 시나리오라 30초·60초 어느 쪽 임계값이든 "아직 stale하지 않음" 조건은 동일하게 만족하므로, `staleTime`을 60초로 올린 뒤에도 아래 결과(재요청 0회)는 그대로 유효하다 — 재측정하지 않은 이유.

| 지표 | Before | After | 변화 |
|---|---|---|---|
| 재방문 시 API 재요청 발생 여부 | 매번 발생 (3/3회) | 전혀 발생 안 함 (0/3회) | 재요청 100% 제거 |
| 재방문 시 데이터 대기시간(응답 수신까지) | 평균 **0.50초** (503.7ms, 3회 측정: 0.528s / 0.500s / 0.483s) | **0.00초** (캐시 즉시 반환, 네트워크 요청 자체가 없음) | **약 0.50초 → 0.00초, 100% 감소** |
| 최초 진입(첫 방문) 시 요청 수 | 1회 | 1회 | 변화 없음 (의도된 동작 — 최초 로딩은 이번 변경의 대상이 아님) |

**해석**: "0.50초 → 0.00초, 100% 개선"이라는 수치는 재현 가능한 사실(요청 자체가 사라짐)이지만, 절대 시간(0.50초)은 테스트에서 인위적으로 준 모의 백엔드 지연(400ms)에 좌우된 값이다. 실제 운영 환경에서의 절대 개선폭은 그 시점의 실제 백엔드 응답 시간만큼이며(예: 백엔드가 150ms면 재방문 시 150ms→0ms), **핵심은 절대값이 아니라 "재방문 시 캐시된 데이터를 쓰느냐(요청 0회) vs 매번 새로 네트워크를 타느냐(요청 1회 이상)"라는 구조 자체가 바뀌었다는 점**이다. 이 구조적 변화는 백엔드 속도와 무관하게 100% 재현된다.

---

## 4. 다음 단계 (이번 커밋에 포함되지 않은 나머지 원인)

우선순위 순:

1. ~~**`KakaoPolygonMap` 4중 `useSigunguStats` 호출 통합**~~ — **완료** (커밋 `0e80b2e`, 브랜치 `perf/frontend-query-cache-defaults`). 별도 문서 참고.
2. **워터폴 제거** — `AlertRiskSection`(위험도 섹션), `events/[id]` 페이지의 순차 fetch를 `useQueries` 병렬화 또는 백엔드 응답 통합으로 해소.
3. **SSR prefetch 도입** — 핵심 목록 페이지에 `dehydrate`/`HydrationBoundary` 패턴을 적용해 최초 로딩 시 서버에서 미리 데이터를 가져와 hydrate. 클라이언트 마운트를 기다리지 않고 데이터가 보이게 됨 (최초 로딩 속도 개선은 이 항목이 핵심).
4. **무거운 컴포넌트 동적 import** — `KakaoPolygonMap`, `KoreaMap25D`, stats 차트를 `next/dynamic`으로 전환해 초기 JS 파싱/실행 시간 단축.

---

## 5. 검증

- `npx tsc --noEmit` 통과 확인 (타입 에러 없음).
- 변경 파일은 `frontend/src/lib/queryClient.ts` 1개, 11줄 추가/1줄 삭제로 범위가 매우 좁아 회귀 리스크 낮음.
- 3-4의 실측 벤치마크로 의도한 동작(재방문 시 재요청 제거)이 실제로 재현됨을 확인.

---

## 6. 실측 방법 (재현 가능한 절차)

정적 코드 분석만으로는 "몇 초가 줄었는지"를 주장할 수 없어, 실제 Next.js dev 서버 + 브라우저 자동화(Playwright, Chromium)로 직접 측정했다.

**환경**
- `npm run dev` (Next.js 16.2.4, Turbopack)로 개발 서버 기동
- 백엔드는 기동하지 않고, `/api/v1/alerts/search/combined` 요청을 Playwright의 `page.route()`로 가로채 **400ms 인위 지연 후 고정 JSON 응답**으로 모킹 (모든 회차에서 동일 조건 유지가 목적 — 실제 백엔드 지연은 네트워크·부하에 따라 매회 달라져서 재현 가능한 비교가 안 됨)

**시나리오 (자동화 스크립트)**
1. `http://localhost:3000/alerts` 최초 접속 → 첫 요청 발생 확인 (양쪽 동일해야 정상)
2. 헤더 로고(`a[href="/"]`) 클릭 → 홈으로 **클라이언트 사이드 이동**(풀 리로드 아님 — 풀 리로드면 캐시고 뭐고 항상 새로 요청되므로 의미 없는 비교가 됨)
3. `a[href="/alerts"]` 클릭 → 다시 alerts로 클라이언트 사이드 이동
4. 이 두 번째 진입 시점에 API 요청이 다시 발생하는지, 발생한다면 클릭 시점부터 응답 수신까지 몇 ms인지 기록

**비교 방법**
- `frontend/src/lib/queryClient.ts`를 `git show`로 수정 전/후 내용으로 번갈아 갈아끼우며(Next dev의 HMR로 즉시 반영) 동일 스크립트를 각 3회씩 실행
- 측정 후 파일은 `git checkout`으로 최종 수정본(현재 커밋 상태)으로 복원 완료 — 저장소에는 흔적 남지 않음
- 측정에 사용한 `playwright` 패키지는 `npm install --no-save`로 임시 설치 후 측정 종료 시 `npm uninstall`로 제거 (`package.json`/`package-lock.json` 변경 없음, `node_modules`는 git 추적 대상 아님)

**원본 측정값**

| 회차 | Before: 재요청 발생 | Before: 응답까지(ms) | After: 재요청 발생 | After: 응답까지(ms) |
|---|---|---|---|---|
| 1 | O | 528 | X | - (요청 없음) |
| 2 | O | 500 | X | - (요청 없음) |
| 3 | O | 483 | X | - (요청 없음) |
| 평균 | 3/3 | 503.7 | 0/3 | 0 |
