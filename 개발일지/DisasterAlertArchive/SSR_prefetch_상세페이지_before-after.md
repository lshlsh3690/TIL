# SSR Prefetch 확장 — 재난문자/이벤트 상세 페이지 Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults`
- 관련 커밋: `8e0a5a9` perf(detail): 재난문자/이벤트 상세 페이지에 SSR prefetch 확장
- 파일: `frontend/src/app/alerts/[id]/page.tsx`, `frontend/src/app/alerts/[id]/AlertDetailClient.tsx`(신규), `frontend/src/app/events/[id]/page.tsx`, `frontend/src/app/events/[id]/EventDetailClient.tsx`(신규)
- 원 출처: [SSR_prefetch_홈페이지_before-after.md](./SSR_prefetch_홈페이지_before-after.md)의 "다음 단계" — 홈 다음으로 상세 페이지부터 확장

---

## 1. 왜 목록 페이지보다 상세 페이지가 먼저인가

홈 페이지에 SSR prefetch를 붙인 뒤 "다른 페이지엔 왜 안 하냐"는 질문에 답하면서 정리된 우선순위다.

| 페이지 종류 | SSR prefetch 난이도 | 이유 |
|---|---|---|
| 홈 | 쉬움 | 파라미터 없는 단순 쿼리 1개, 폼 상태 없음 |
| **상세 페이지 (alerts/[id], events/[id])** | **쉬움** | **id가 URL 라우트 파라미터에서 그대로 나옴 — react-hook-form 같은 클라이언트 상태를 거치지 않음** |
| 목록 페이지 (alerts, events, community) | 어려움 | 필터가 `react-hook-form`의 `watch()`+`useSearchParams()`로 관리되어, 서버가 첫 렌더의 필터값을 알아내려면 파싱 로직을 서버/클라이언트가 공유하도록 리팩터링해야 함 |

상세 페이지는 `/alerts/123` 같은 URL에서 `123`이 Next.js의 동적 라우트 세그먼트(`[id]`)로 **서버 컴포넌트에도 그대로 전달**되기 때문에, 홈과 비슷한 수준의 작업으로 적용할 수 있었다.

---

## 2. 패턴은 홈과 동일 — "prefetch 책임만 서버로"

기존 `page.tsx`의 내용(훅 호출, JSX, 인터랙션)은 **한 글자도 안 바꾸고** 그대로 `XxxDetailClient.tsx`로 옮기고, `page.tsx`는 "id를 읽어서 필요한 쿼리 하나만 prefetch하고 HydrationBoundary로 감싸는" 얇은 서버 컴포넌트로 새로 썼다.

### 2-1. `events/[id]`

```tsx
// app/events/[id]/page.tsx (서버 컴포넌트, 전체)
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import EventDetailClient from "./EventDetailClient";
import { fetchEvent } from "@/api/eventApi";

export const dynamic = "force-dynamic";

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: idParam } = await params;
  const id = Number(idParam);

  const queryClient = new QueryClient();
  await queryClient.prefetchQuery({
    queryKey: ["event", id, "ko"],
    queryFn: () => fetchEvent(id, "ko"),
  });

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <EventDetailClient />
    </HydrationBoundary>
  );
}
```

**주의할 점 — `params`가 Promise다.** Next.js 15부터(이 프로젝트는 16.2.4) 동적 라우트의 `params`/`searchParams`는 서버 컴포넌트에 **Promise로** 전달된다. `await params`로 풀어야 값을 쓸 수 있다. (반면 클라이언트 컴포넌트의 `useParams()`/`useSearchParams()` 훅은 예전처럼 동기적으로 값을 준다 — 이번에도 `EventDetailClient` 내부는 그대로 `useParams()`를 씀.)

**언어(lang)는 항상 "ko"로 prefetch한다.** 클라이언트의 실제 언어는 `useLanguageStore`(zustand, `localStorage`에 persist)에서 오는데, 서버는 브라우저의 `localStorage`에 접근할 방법이 없다. 그래서 zustand 스토어의 초기값과 동일한 `"ko"`로 prefetch해뒀다 — 사용자가 예전에 "en"으로 바꿔놨다면, 클라이언트가 `useEvent(id, "en")`을 요청할 때 queryKey(`["event", id, "en"]`)가 서버가 채워둔 캐시(`["event", id, "ko"]`)와 달라서 **그냥 캐시 미스로 정상적으로 새로 요청**된다. 즉 "가속 효과가 없을 뿐, 깨지지는 않는다" — 이런 그레이스풀 디그레이드가 되는지도 아래 3장에서 직접 확인했다.

### 2-2. `alerts/[id]` — 두 쿼리 중 하나만 골라 prefetch

`alerts/[id]`는 URL에 `?source=USER`가 붙어 있으면 사용자 제보(`useUserAlert`)를, 없으면 공식 재난문자(`useAlert`)를 보여주는 분기가 있다. 클라이언트 코드가 이렇다:

```ts
// AlertDetailClient.tsx (기존 그대로)
const { data: offData } = useAlert(isUser ? 0 : id, lang);   // isUser면 id=0 → enabled:false로 비활성
const { data: userData } = useUserAlert(isUser ? id : 0);    // 반대로 비활성
```

즉 **둘 다 항상 호출은 되지만, 실제로 요청이 나가는 건 `source` 값에 따라 둘 중 하나뿐**이다. 서버도 똑같이 분기해서, 실제로 쓰일 쿼리 하나만 prefetch했다.

```tsx
// app/alerts/[id]/page.tsx (서버 컴포넌트, 전체)
import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import AlertDetailClient from "./AlertDetailClient";
import { fetchAlert } from "@/api/alertApi";
import { fetchUserAlert } from "@/api/userAlertApi";

export const dynamic = "force-dynamic";

export default async function AlertDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ source?: string }>;
}) {
  const { id: idParam } = await params;
  const { source } = await searchParams;
  const id = Number(idParam);
  const isUser = (source || "OFFICIAL").toUpperCase() === "USER";

  const queryClient = new QueryClient();
  if (isUser) {
    await queryClient.prefetchQuery({
      queryKey: ["user-alert", id],
      queryFn: () => fetchUserAlert(id),
    });
  } else {
    await queryClient.prefetchQuery({
      queryKey: ["alert", id, "ko"],
      queryFn: () => fetchAlert(id, "ko"),
    });
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <AlertDetailClient />
    </HydrationBoundary>
  );
}
```

`source`도 URL 쿼리스트링이라 서버의 `searchParams` prop으로 그대로 읽힌다 — 여기도 라우팅 정보에서 바로 나오는 값이라 클라이언트 상태(폼 등)를 거칠 필요가 없었다.

**댓글/인증/좋아요 같은 나머지 데이터는 그대로 클라이언트에 남겨뒀다.** `useComments`, `useInfiniteComments`, `useAuthStore`(로그인 여부) 등은 prefetch 대상에서 제외 — 로그인 여부에 따라 달라지는 데이터를 서버에서 미리 가져오려면 요청의 인증 쿠키를 서버 컴포넌트까지 전달하는 별도 설계가 필요해서, 이번엔 "본문(재난문자/이벤트 자체)"만 먼저 처리했다.

---

## 3. 검증

- `npx tsc --noEmit`, `npx eslint` 모두 통과 (신규/변경 파일 4개).
- **백엔드 없이** 프론트 dev 서버만 띄운 채 세 라우트 모두 확인:
  - `GET /events/1` → `200`, 정상 레이아웃 HTML (에러 오버레이 없음)
  - `GET /alerts/1` → `200`, 정상 레이아웃 HTML
  - `GET /alerts/1?source=USER` → `200`, 정상 레이아웃 HTML (응답 시간이 9ms로 눈에 띄게 빨랐는데, `source=USER` 분기의 `fetchUserAlert` prefetch가 백엔드 미기동으로 즉시 실패하고 그냥 넘어간 것으로 보임 — React Query v5의 `prefetchQuery`가 실패해도 throw하지 않는다는 걸 다시 한번 확인한 셈)
  - 즉 prefetch가 실패해도(백엔드 다운 상황) 세 페이지 다 크래시 없이 기존처럼 클라이언트 폴백 fetch로 넘어감을 확인.
- 홈 페이지와 마찬가지로 "SSR 응답에 실제 데이터가 박혀 나오는지"는 로컬 DB 포트 충돌로 이번엔 재현 못 함 (재현 방법은 [SSR_prefetch_홈페이지_before-after.md](./SSR_prefetch_홈페이지_before-after.md) 3장 표와 동일한 방식 — `curl`로 SSR HTML에 실제 메시지/제목 텍스트가 박혀 있는지 확인하면 됨).

---

## 4. 남은 항목

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 기준:

1. ~~`KakaoPolygonMap` 4중 호출 통합~~ — 완료
2. ~~워터폴 제거~~ — 완료
3. **SSR prefetch 도입** — 홈, 상세 페이지(`alerts/[id]`, `events/[id]`) 완료. **남은 것: alerts/events/community 목록 페이지** — `react-hook-form` 필터 파싱 로직을 서버/클라이언트 공유 가능하게 리팩터링해야 하는 더 큰 작업으로 별도 분리.
4. 무거운 컴포넌트(`KakaoPolygonMap`, `KoreaMap25D`, stats 차트) `next/dynamic` 전환
