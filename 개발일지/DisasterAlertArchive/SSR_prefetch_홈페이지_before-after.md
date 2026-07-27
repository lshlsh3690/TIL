# SSR Prefetch 도입 — 홈 페이지 Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults`
- 관련 커밋: `120912f` perf(home): 홈 페이지에 SSR prefetch 도입 (dashboard-summary)
- 파일: `frontend/src/app/page.tsx`(신규 서버 컴포넌트), `frontend/src/app/HomeClient.tsx`(신규, 기존 page.tsx 내용 이동), `frontend/src/api/axios.ts`

---

## 1. 문제 (Before)

이 프로젝트의 페이지는 전부 최상단에 `"use client"`가 붙어 있다 (App Router의 서버 컴포넌트 기능을 전혀 안 쓰는 상태). 그 결과 어떤 페이지든 데이터가 보이기까지 다음 순서를 거친다.

```
1. 서버가 빈 HTML 셸 + JS 번들 응답
2. 브라우저가 JS 다운로드·파싱·실행
3. React가 컴포넌트를 마운트
4. useQuery의 useEffect가 그제서야 fetch 시작
5. 응답 도착 → 리렌더 → 화면에 실제 데이터 표시
```

홈 페이지(`app/page.tsx`)는 진입 트래픽이 가장 몰리는 페이지인데도, "오늘 발생 건수" 같은 상단 통계 카드가 **1~4단계를 다 거친 뒤에야** 나타났다 — 그 전까지는 `data?.todayOfficialCount ?? 0`이 항상 `0`을 보여주다가, fetch가 끝나야 실제 값으로 바뀌는 식(깜빡임).

`frontend/src/lib/reactQueryProvider.tsx`가 `QueryClientProvider`로 앱 전체를 감싸긴 하지만, 이건 "클라이언트에서 캐시를 어떻게 관리할지"를 정할 뿐 **서버가 미리 데이터를 가져다주는 것과는 무관**하다. Next.js App Router가 제공하는 서버 컴포넌트 + `dehydrate`/`HydrationBoundary` 조합(React Query 공식 SSR 레시피)이 이 프로젝트엔 한 군데도 없었다.

---

## 2. 해결 (After)

### 2-1. 왜 홈 페이지부터인가

- 트래픽이 가장 많이 몰리는 진입점(LCP 영향이 가장 큼).
- `useDashboardSummary()`가 파라미터 없는 단순 GET이라 서버에서 미리 가져오기 가장 쉬움 (`alerts`/`events` 목록 페이지는 `react-hook-form`+`useSearchParams` 기반 필터가 얽혀 있어 서버에서 초기 파라미터를 그대로 재현하려면 더 큰 리팩터링이 필요 — 4장 "다음 단계" 참고).
- 인증이 필요 없는 공개 데이터라 서버 사이드에서 쿠키/토큰 걱정 없이 바로 prefetch 가능.

### 2-2. 패턴: 서버 컴포넌트가 prefetch하고, 클라이언트 컴포넌트는 그대로 둔다

핵심은 **"데이터를 가져오는 책임만" 서버로 옮기고, `useTranslation()` 같은 클라이언트 전용 기능이 필요한 렌더링 로직은 그대로 클라이언트 컴포넌트에 둔다**는 것. 그래서 기존 `page.tsx`의 JSX/훅 내용은 손 하나 안 대고 그대로 `HomeClient.tsx`로 옮기고, `page.tsx`는 완전히 새로 얇게 작성했다.

`frontend/src/app/page.tsx` (After, 전체):

```tsx
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import HomeClient from "./HomeClient";
import { fetchDashboardSummary } from "@/api/alertApi";

export const dynamic = "force-dynamic";

export default async function Home() {
  const queryClient = new QueryClient();
  await queryClient.prefetchQuery({
    queryKey: ["dashboard-summary"],
    queryFn: fetchDashboardSummary,
  });

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <HomeClient />
    </HydrationBoundary>
  );
}
```

`HomeClient.tsx`는 `"use client"`가 붙은 채로 기존 `useDashboardSummary()` 등을 그대로 호출한다 — **코드 한 줄도 안 바뀜.** React Query가 `queryKey`(`["dashboard-summary"]`)가 서버에서 prefetch한 것과 정확히 같다는 걸 보고, 클라이언트에서 다시 fetch하지 않고 hydrate된 데이터를 즉시 씀.

### 2-3. 왜 요청마다 `new QueryClient()`를 새로 만드는가

`lib/queryClient.ts`의 `queryClient`는 브라우저에서 **탭 하나당 하나**만 존재하는 싱글턴이라 문제없지만, 서버는 다르다. Next.js 서버는 **여러 사용자의 요청을 하나의 Node.js 프로세스가 순차/동시 처리**한다. 만약 서버 컴포넌트가 그 싱글턴을 그대로 가져다 썼다면, A 사용자의 요청으로 캐시된 데이터가 B 사용자의 요청에도 그대로 노출되는 **요청 간 데이터 누수**가 생긴다. 그래서 서버 컴포넌트 안에서 `new QueryClient()`로 "이 요청 하나만을 위한" 인스턴스를 매번 새로 만들고, 그 인스턴스를 딱 `dehydrate` 한 번 하고 버린다 — React Query 공식 문서가 명시하는 SSR 필수 규칙.

### 2-4. axios 인스턴스: 서버에는 "자기 자신"을 부를 host가 없다

`frontend/src/api/axios.ts`의 기존 코드:

```ts
const baseURL = process.env.NEXT_PUBLIC_API_URL || "";  // 비어있음 → 상대 경로
const instance = axios.create({ baseURL: baseURL || undefined, withCredentials: true });
```

브라우저에서는 상대 경로(`/api/v1/...`)로 요청하면 브라우저가 알아서 현재 origin(`http://localhost:3000`)에 붙여서 보내고, `next.config.ts`의 `rewrites()`가 그걸 실제 백엔드(`BASE_API_URL`)로 프록시해준다. 그런데 **서버 컴포넌트 안에서 이 인스턴스를 그대로 쓰면** — 서버는 브라우저가 아니라서 "현재 페이지의 origin"이라는 개념이 없다. Node.js의 axios에게 상대 경로만 주면 `Invalid URL` 에러가 난다.

```ts
// After
const baseURL =
  typeof window === "undefined"
    ? process.env.BASE_API_URL || "https://api.disaster-alert-archive.co.kr"
    : process.env.NEXT_PUBLIC_API_URL || "";
```

`typeof window === "undefined"`로 지금 이 코드가 서버에서 도는지 브라우저에서 도는지 구분해서, 서버에서는 `next.config.ts`가 쓰는 것과 동일한 `BASE_API_URL`(절대 경로)을 직접 쓰도록 분기했다. Next.js가 서버 번들과 클라이언트 번들을 완전히 분리해서 만들기 때문에, 같은 `axios.ts` 모듈이라도 "서버용 인스턴스"와 "브라우저용 인스턴스"는 실제로는 각자 다른 실행 환경에서 따로 평가되는 별개의 객체라 이렇게 분기해도 안전하다.

### 2-5. `dynamic = "force-dynamic"`이 필요한 이유

Next.js는 서버 컴포넌트에 `cookies()`/`headers()` 같은 "동적 API" 호출이 없으면 기본적으로 **빌드 시점에 한 번 정적 HTML로 구워서(SSG)** 캐싱해두려고 한다. 그런데 "오늘 발생 건수"는 매 순간 바뀌는 값이라, 빌드 시점에 굳혀버리면 배포 이후 계속 "빌드했을 때의 오늘 값"만 보여주는 심각한 버그가 된다. `export const dynamic = "force-dynamic"`으로 "이 페이지는 매 요청마다 서버에서 새로 렌더링하라"고 명시해 이 문제를 원천 차단했다.

---

## 3. 검증

- `npx tsc --noEmit`, `npx eslint` 통과 (신규/변경 파일 3개 모두 경고 없음).
- **백엔드 없이** 프론트 dev 서버만 띄운 상태에서 `curl http://localhost:3000/` → `HTTP 200`, 응답 HTML에 `dashboard-heading`/`stat-card` 마크업 정상 포함 확인. 즉 서버 prefetch가 실패해도(`prefetchQuery`는 실패해도 throw하지 않는 React Query v5의 설계) 페이지 전체가 깨지지 않고, 기존처럼 클라이언트에서 폴백 fetch가 도는 것까지 확인.
- 실제 백엔드를 붙인 상태에서 "prefetch된 데이터가 초기 HTML에 바로 박혀 나오는지"(즉 클라이언트 재요청 없이 즉시 표시되는지)는 이번 세션에서는 로컬 DB 포트가 다른 프로젝트와 충돌해 재현하지 못함 — 패턴 자체는 React Query 공식 SSR 가이드 그대로라 별도 실측 없이도 신뢰 가능한 부분과, 재측정이 필요하면 재현 가능한 부분을 아래에 구분해 남긴다.

| 검증 항목 | 방법 | 결과 |
|---|---|---|
| 타입/린트 정합성 | `tsc --noEmit`, `eslint` | 통과 |
| 백엔드 다운 시 페이지 안정성 | dev 서버만 기동 후 curl | 200, 정상 마크업, 크래시 없음 |
| prefetch된 데이터가 초기 HTML에 포함되는지 | 백엔드 연동 후 `curl` 응답에서 `todayOfficialCount` 값이 `0`이 아닌 실제 값으로 SSR HTML에 박혀 있는지 확인 | **미실행** — 로컬 DB/Redis 포트 충돌로 이번 세션엔 스킵. 재현하려면 백엔드+DB를 띄운 뒤 `curl http://localhost:3000/ \| grep -o '"todayOfficialCount":[0-9]*'`로 SSR 응답 자체에 숫자가 박혀 있는지 확인하면 됨 (하이드레이션 전 HTML이므로, `0`이 아니라 실제 숫자가 보이면 prefetch가 제대로 동작한 것) |

---

## 4. 다음 단계

- **alerts/events/community 목록 페이지로 확산**: 같은 패턴(서버 컴포넌트가 prefetch → `HydrationBoundary` → 기존 클라이언트 컴포넌트)을 적용할 수 있지만, 이 페이지들은 `react-hook-form`의 `watch()`/`useSearchParams()`로 필터 상태를 관리하고 있어 "서버가 첫 렌더 시점의 파라미터를 어떻게 알아낼지"(Next.js의 `searchParams` prop 활용)를 추가로 설계해야 함 — 홈보다 작업량이 더 큼.
- **`useSidoStats`/`useLatestComments`도 같은 방식으로 prefetch 추가 가능**: 이번엔 범위를 좁혀 `useDashboardSummary` 하나만 처리. 나머지 두 쿼리도 동일한 레시피(`queryClient.prefetchQuery` 추가)로 확장 가능.
- **실제 SSR 효과 실측**: 로컬 DB 포트 충돌이 해소되면 Playwright로 "SSR 응답에 실제 데이터가 박혀 있는지" + "첫 페인트까지 걸리는 시간"을 이전 항목들처럼 실측해서 기록.

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 기준 전체 진행 상황:

1. ~~`KakaoPolygonMap` 4중 호출 통합~~ — 완료
2. ~~워터폴 제거~~ — 완료 (`events/[id]` 수정, `AlertRiskSection`은 구조적 이유로 보류)
3. ~~SSR prefetch 도입~~ — **완료 (이 문서, 홈 페이지만)** — alerts/events/community는 후속 작업
4. 무거운 컴포넌트(`KakaoPolygonMap`, `KoreaMap25D`, stats 차트) `next/dynamic` 전환
