# SSR Prefetch 확장 — 재난문자 목록 페이지, 그리고 내가 범위를 과대평가했던 이유

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults`
- 관련 커밋: `3708850` perf(alerts): 재난문자 목록 페이지에 SSR prefetch 도입
- 파일: `frontend/src/lib/alertsSearchParams.ts`(신규), `frontend/src/app/alerts/page.tsx`, `frontend/src/app/alerts/AlertsClient.tsx`(신규)

---

## 0. 먼저 인정할 것 — "범위가 크다"는 판단은 틀렸다

[SSR_prefetch_상세페이지_before-after.md](./SSR_prefetch_상세페이지_before-after.md) 4장에서 나는 목록 페이지(alerts/events/community) SSR prefetch를 "필터 파싱 로직을 서버/클라이언트 공유하도록 리팩터링해야 하는 더 큰 작업"이라며 별도 과제로 미뤄뒀다. 사용자가 "왜 범위가 크다고 판단했냐"고 되물어서 코드를 다시 정독했는데 — **실제로 해보니 상세 페이지 작업과 비슷한 크기였다.** 과대평가했다.

무엇을 놓쳤었나:
- "react-hook-form 상태와 서버 파싱을 동기화해야 한다"는 게 막연히 크게 느껴졌지만, 실제로 `buildParams`(쿼리 파라미터 조립 로직)는 `formState`(순수 객체) → `params`(순수 객체)로 가는 **부수효과 없는 순수 함수**였다. React 훅이나 클라이언트 전용 API에 의존하지 않아서, 파일 하나로 뽑아 서버 컴포넌트에서도 그대로 재사용 가능했다.
- URL 검색 파라미터를 읽는 로직(`searchParams.get("sido")` 등)도 마찬가지로 순수 문자열 파싱이라, `URLSearchParams`(클라이언트의 `useSearchParams()`)든 일반 객체(서버의 `searchParams` prop)든 인터페이스만 맞추면 공유할 수 있었다.
- 즉 "복잡해 보이는 이유"는 폼 라이브러리(`react-hook-form`)가 껴 있다는 것뿐이었고, **폼은 화면에 보여주는 값(defaultValues)의 문제**일 뿐 서버 prefetch에 필요한 "쿼리 파라미터 계산"과는 레이어가 분리돼 있었다.

교훈: "이 페이지는 상태 관리가 복잡해 보인다"와 "이 페이지의 **데이터 요청 부분**이 복잡하다"는 다른 이야기다. 실제로 뜯어보지 않고 표면적인 복잡도(폼, dnd-kit, 위젯 시스템 등)로 작업량을 예단하지 말 것.

---

## 1. 실제로 필요했던 작업

### 1-1. 공유 파싱/빌드 함수 추출 (`alertsSearchParams.ts`)

기존에 `AlertsClient.tsx`(당시엔 `page.tsx`) 안에 있던 `buildParams`(목록용)와 `filteredStatsParams`를 만들던 인라인 로직(통계용, 코드가 `buildParams`와 90% 중복돼 있었다 — 이것도 발견한 겸 정리)을 별도 모듈로 뽑았다:

```ts
// frontend/src/lib/alertsSearchParams.ts
export function parseAlertsSearchForm(sp: URLSearchParams | Record<string, string | string[] | undefined>): AlertsSearchForm { ... }
export function buildAlertsListParams(f: AlertsSearchForm, page: number, size: number) { ... }
export function buildAlertsStatsParams(f: AlertsSearchForm) { ... }
```

`parseAlertsSearchForm`이 `URLSearchParams`(클라이언트)와 일반 객체(서버의 `searchParams` prop, Next 15+부터 `Promise<Record<string, string | string[] | undefined>>`)를 **둘 다** 받도록 오버로드 없이 유니온 타입 하나로 처리한 게 포인트 — 파일을 둘로 안 쪼개도 됐다.

### 1-2. 서버 컴포넌트 (`page.tsx`)

```tsx
export default async function AlertsPage({ searchParams }: { searchParams: Promise<...> }) {
  const sp = await searchParams;
  const form = parseAlertsSearchForm(sp);
  const listParams = buildAlertsListParams(form, 0, 10);   // page=0, size=10 고정
  const statsParams = buildAlertsStatsParams(form);

  const queryClient = new QueryClient();
  await Promise.all([
    queryClient.prefetchQuery({ queryKey: ["alerts-combined", listParams, "ko"], queryFn: () => searchCombinedAlerts(listParams, "ko") }),
    queryClient.prefetchQuery({ queryKey: ["alert-stats-sido", statsParams], queryFn: () => fetchLatestAlertsBySido(statsParams) }),
    queryClient.prefetchQuery({ queryKey: ["alert-stats", statsParams], queryFn: () => fetchStats(statsParams) }),
  ]);

  return <HydrationBoundary state={dehydrate(queryClient)}><AlertsClient /></HydrationBoundary>;
}
```

페이지네이션은 URL에 없고(`onSubmit`이 `page`를 querystring에 안 넣음) 항상 클라이언트 `useState(0)`으로 시작하는 걸 코드로 확인했기 때문에, 서버도 안심하고 `page=0`을 고정값으로 가정했다. 목록(`alerts-combined`) + 통계 요약 2개(`alert-stats`, `alert-stats-sido`)까지 3개 쿼리를 병렬 prefetch — 지도 쪽(`useSigunguStats`, 시군구 드롭다운 `useSigungu`)은 상호작용 이후에나 의미 있는 데이터라 범위에서 뺐다.

### 1-3. 덤으로 발견하고 고친 기존 버그 — 초기 렌더의 "빈 필터" 이중 요청

`AlertsClient.tsx`를 옮기면서 원래 코드를 자세히 보니, 기존에도 이미 작은 비효율이 있었다:

```ts
// Before (원래 있던 코드)
const [formState, setFormState] = useState<SearchForm>({});   // ← 항상 빈 객체로 시작
// ...
useEffect(() => {
  // searchParams를 읽어서 formState를 실제 값으로 채움
  setFormState({ sido, sigungu, ... });
}, [searchParams, reset]);
```

`/alerts?sido=서울특별시`로 들어와도 **첫 렌더는 무조건 필터 없는 상태**로 `useSearchCombinedAlerts`를 한 번 호출하고, `useEffect`가 실제 필터를 반영한 뒤 다시 호출하는 이중 요청이 있었다. SSR prefetch를 붙이려면 클라이언트의 첫 렌더가 서버가 prefetch한 것과 **정확히 같은 queryKey**를 써야 캐시가 맞물리는데, `{}`로 시작하면 그 요구조건 자체를 만족 못 한다는 걸 깨닫고:

```ts
// After
const [formState, setFormState] = useState<SearchForm>(() => parseAlertsSearchForm(searchParams));
```

으로 지연 초기화하도록 고쳤다. SSR prefetch를 위해 어쩔 수 없이 건드린 부분인데, 결과적으로 이 페이지 자체의 기존 이중 요청 버그도 같이 없앤 셈이다 — **필요에 의해 코드를 정독하다 보면 별개의 기존 버그를 같이 발견하게 되는** 흔한 케이스.

---

## 2. 검증 — 실제 백엔드로 SSR 데이터가 박히는 것까지 확인

이전 두 SSR 문서(홈, 상세 페이지)는 백엔드가 안 떠 있어서 "크래시 안 하는지"까지만 확인했는데, 이번엔 실제 백엔드가 떠 있는 상태에서 검증해 한 단계 더 나갔다.

```bash
curl "http://localhost:3000/alerts?sido=서울특별시" | grep -o 'href="/alerts/[0-9]+' | sort -u
# → /alerts/85234, /alerts/85340, /alerts/85360, /alerts/85371, /alerts/85412 ...
```

**서버가 렌더링한 HTML에 실제 alertId로 연결되는 링크가 이미 박혀 있다** — 즉 브라우저가 아무 JS도 실행하기 전, 서버 응답 자체에 진짜 목록 데이터가 포함돼 있다는 뜻. 필터 없는 `/alerts`도 동일하게 10개 항목(size=10)이 그대로 나왔고, dehydrate된 상태 안에서 `"alerts-combined"`/`"alert-stats"`/`"alert-stats-sido"` 세 쿼리 모두 `"status":"success"`로 확인됨.

---

## 3. 전체 진행 상황 (최종)

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md)에서 시작된 성능 개선 작업, 9개 커밋으로 마무리:

1. React Query 전역 캐시 설정
2. staleTime 30초 → 1분 조정
3. 지도 시군구 통계 API 4중 호출 → 1회 통합
4. 이벤트 상세 워터폴 제거 (`AlertRiskSection`은 구조적으로 보류)
5. 홈 페이지 SSR prefetch
6. 상세 페이지(`alerts/[id]`, `events/[id]`) SSR prefetch
7. 무거운 컴포넌트 `next/dynamic` 전환
8. `.claude/settings.json` git 추적 제외 (부수 정리)
9. **목록 페이지(`alerts`) SSR prefetch** ← 이 문서

`events`/`community` 목록 페이지는 같은 패턴(`alertsSearchParams.ts`에 해당하는 모듈만 각각 만들면 됨)으로 이어서 확장 가능 — 이번에 범위를 재는 눈이 교정됐으니 다음엔 "크다"고 미리 판단하지 말고 먼저 코드부터 열어볼 것.
