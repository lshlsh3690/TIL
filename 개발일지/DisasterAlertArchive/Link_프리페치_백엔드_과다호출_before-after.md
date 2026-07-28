# Next.js Link 자동 프리페치로 인한 백엔드 과다 호출 — Before / After 기록

- 작성일: 2026-07-28
- 브랜치: `develop`
- 계기: Caddy 접근 로그(`/var/log/caddy/access.log`)에서 `/api/v1/alerts/*`로 짧은 시간에 대량 요청이 몰리는 걸 발견
- 변경 파일: `frontend/src/components/layout/Header.tsx`, `frontend/src/app/HomeClient.tsx`, `frontend/src/app/LatestAlertsSection.tsx`, `frontend/src/app/alerts/AlertsClient.tsx`, `frontend/src/app/events/EventsClient.tsx`

---

## 1. 증상 — 로그에서 뭘 봤나

배포된 백엔드(Spring Boot, EC2 + Caddy 리버스 프록시) 앞단 Caddy의 접근 로그를 열어보니, `/api/v1/alerts/{id}` 같은 엔드포인트가 **아주 짧은 시간에, ID가 순서대로(89520, 89521, 89522 …) 대량 호출**되고 있었다. 요청을 보낸 쪽의 User-Agent(요청을 보낸 프로그램이 자기소개하는 문자열)를 보니 실제 크롬 브라우저가 아니라 `node`, `axios/1.11.0` 이었고, IP 대역은 AWS(50.16.x, 54.238.x …) 소속이었다.

정리하면 이렇다.

| 관찰된 트래픽 | 겉보기엔 | 실제로는 |
|---|---|---|
| `/alerts/{id}` 순차 대량 호출, UA=`node` | 크롤러가 긁는 것 같음 | Vercel(우리 프론트가 배포된 곳)의 서버 실행 환경도 AWS 위에서 도는 Node.js라, 우리 코드가 서버에서 `fetch`를 부르면 UA가 `node`로 찍힌다 |
| `/stats`, `/search/combined`, `/dashboard/summary` 가 정확히 2번씩, UA=`axios/1.11.0` | 프론트에서 같은 요청을 실수로 두 번 보내는 버그 같음 | 이것도 브라우저가 아니라 서버에서 두 번 실행된 흔적 (이유는 3장에서 설명) |

즉, **외부 크롤러도 아니고 클라이언트(브라우저)의 실수도 아니라, 우리 Next.js 서버 자신이 스스로 필요 이상으로 API를 호출하고 있었다.**

---

## 2. 배경 지식 — 알아야 하는 개념 3가지

### 2-1. `<Link>`의 "프리페치(prefetch)"란?

Next.js에서 페이지 이동은 보통 `<a href="...">` 대신 `<Link href="...">`를 쓴다. 일반 `<a>`와 다른 점은, **사용자가 클릭하기도 전에 그 페이지에 필요한 걸 미리 받아둔다**는 것이다. 이걸 프리페치라고 부른다.

예를 들어 목록 화면에 게시글 10개가 나열되어 있고 각 제목이 `<Link href="/posts/1">` 처럼 걸려있다면, 그 링크가 화면(뷰포트)에 보이는 순간 Next.js가 "이 사람이 곧 클릭할 수도 있으니 미리 준비해두자"며 백그라운드에서 해당 페이지를 조용히 미리 불러온다. 사용자가 실제로 클릭했을 때 이미 준비돼 있으니 페이지 전환이 즉각적으로 느껴진다 — **의도 자체는 사용자 경험을 좋게 하려는 최적화 기능**이다.

중요한 건 **이 동작이 기본값(default)** 이라는 점이다. `<Link href="/posts/1">`라고만 쓰면, 개발자가 아무것도 지정하지 않아도 "화면에 보이면 자동으로 프리페치"가 켜져 있다.

### 2-2. "서버 컴포넌트"와 "force-dynamic"이란?

Next.js의 App Router(이 프로젝트가 쓰는 최신 방식)에서는 페이지(`page.tsx`)가 기본적으로 **서버에서 실행되는 컴포넌트**다. 브라우저가 아니라 Vercel(또는 우리 서버)에서 그 페이지의 코드가 실행되고, 필요한 데이터를 그 안에서 직접 API로 가져온 다음, 완성된 결과만 브라우저로 보낸다.

`export const dynamic = "force-dynamic";` 이라고 페이지에 써두면 "이 페이지는 절대 미리 만들어두지 말고, 요청이 올 때마다 매번 서버에서 새로 실행해라"라는 뜻이다. 이 프로젝트는 홈(`/`)이 "오늘 발생한 재난문자 건수" 같은 실시간 값을 보여줘야 하고, `/alerts` 목록은 사용자가 고른 필터(지역/기간/유형)에 따라 매번 다른 결과를 보여줘야 해서 이 옵션을 켜두었다 — 여기까진 정상적이고 의도된 설계다.

### 2-3. `loading.tsx`가 없으면 프리페치가 어떻게 되는가

여기서부터가 이번 사고의 핵심이다. Next.js 공식 문서에 이렇게 나와 있다.

> 정적(static) 페이지는 전체가 프리페치된다. 동적(dynamic) 페이지는, `loading.tsx`(로딩 화면 파일)가 있으면 **그 로딩 화면까지만** 미리 받아오고, 실제 데이터는 클릭 후에 받아온다.

문제는 `loading.tsx`가 없는 동적 페이지다. "로딩 화면까지만 미리 받으라"고 했는데 그 경계(loading.tsx)가 없으니, Next.js가 멈출 지점을 못 찾고 **페이지 전체 — 즉 서버 컴포넌트 안에서 실행되는 API 호출까지 포함해서 — 통째로 미리 실행**해버린다. 사용자가 클릭도 안 했는데 말이다.

이 프로젝트의 `alerts/`, `events/` 라우트 아래에는 `loading.tsx`가 하나도 없었다(직접 확인). 그래서 이 함정에 정확히 걸렸다.

---

## 3. 원인 — 이 세 가지가 겹쳐서 사고가 났다

### 3-1. 어디서나 보이는 Header 네비게이션

`frontend/src/app/layout.tsx`(모든 페이지를 감싸는 최상위 레이아웃)에 `<Header />`가 박혀 있고, Header 안에는 `/`, `/alerts`, `/stats` 등으로 가는 `<Link>`들이 있다. 즉 **사이트 어느 페이지를 보고 있든 이 링크들은 항상 화면에 떠 있다.**

```tsx
// Header.tsx (수정 전)
{menu.map(({ name, href }) => (
  <Link key={href} href={href} className="...">
    {name}
  </Link>
))}
```

`/`(홈)와 `/alerts`(목록)는 둘 다 `force-dynamic` + 서버 컴포넌트에서 API를 여러 개 미리 호출(prefetch)하는 페이지다.

```tsx
// app/page.tsx (홈, 수정 전부터 있던 코드 — 이 자체는 정상)
export const dynamic = "force-dynamic";
export default async function Home() {
  const queryClient = new QueryClient();
  await queryClient.prefetchQuery({
    queryKey: ["dashboard-summary"],
    queryFn: fetchDashboardSummary,   // → GET /api/v1/alerts/dashboard/summary
  });
  ...
}
```

```tsx
// app/alerts/page.tsx (목록, 수정 전부터 있던 코드 — 이 자체도 정상)
export const dynamic = "force-dynamic";
export default async function AlertsPage(...) {
  const queryClient = new QueryClient();
  await Promise.all([
    queryClient.prefetchQuery({ queryFn: () => searchCombinedAlerts(...) }), // /search/combined
    queryClient.prefetchQuery({ queryFn: () => fetchLatestAlertsBySido(...) }), // /stats/sido
    queryClient.prefetchQuery({ queryFn: () => fetchStats(...) }),           // /stats
  ]);
  ...
}
```

2-3에서 설명한 대로 `loading.tsx`가 없으니, Header의 `/`·`/alerts` 링크가 화면에 보이는 순간 — **즉 로그인 페이지든 커뮤니티 페이지든, 아무 페이지에 들어가든** — 이 4개의 API 호출(dashboard-summary, search/combined, stats/sido, stats)이 백그라운드에서 조용히 실행된다. 사용자가 실제로 `/`나 `/alerts`를 방문하면 그때 또 한 번 진짜로 실행된다. **한 번은 "혹시 몰라서 미리", 한 번은 "진짜 방문해서" — 그래서 정확히 2번씩 찍힌 것이다.**

### 3-2. 목록/홈에 쭉 나열된 개별 링크들

`/alerts` 목록 페이지는 한 페이지에 10건, 홈 화면의 "최근 재난문자" 위젯은 5건을 보여주는데, 각 항목이 전부 `<Link href="/alerts/{id}">`였다.

```tsx
// AlertsClient.tsx (수정 전) — 목록의 각 줄
<Link href={href} className="...">
  ...
</Link>
```

```tsx
// LatestAlertsSection.tsx (수정 전) — 홈 화면 "최근 재난문자" 5건
<Link href={`/alerts/${a.id}`} className="feed-item">
  ...
</Link>
```

상세 페이지(`/alerts/{id}`)도 `force-dynamic` + 서버에서 API 호출을 하는 페이지라, **목록 10건이 화면에 뜨는 순간 상세 페이지 10개 분량의 서버 API 호출이, 홈에 5건이 뜨는 순간 5개 분량이 백그라운드로 동시에 실행**됐다. 로그에 찍힌 "ID가 순서대로 대량 호출"이 바로 이거다 — 사용자가 그 10개를 다 클릭한 게 아니라, **목록이 화면에 나타나기만 해도** 10개 전부가 프리페치된 것이다.

### 3-3. UA로 "서버에서 실행됐다"는 걸 확신할 수 있었던 이유

axios(HTTP 요청 보낼 때 쓰는 라이브러리)가 Node.js 환경(서버)에서 실행되면 요청 헤더에 `User-Agent: axios/1.11.0`을 스스로 붙인다. 그런데 **같은 axios 코드가 브라우저에서 실행되면 이 헤더를 못 붙인다** — 브라우저는 보안상 자바스크립트가 `User-Agent` 헤더를 마음대로 바꾸는 걸 막아놓았기 때문이다(브라우저는 항상 자기 자신의 진짜 UA, 예: `Mozilla/5.0 ... Chrome/...`를 보낸다).

그래서 `axios/1.11.0`이라는 UA가 로그에 찍혔다는 것 자체가 **"이 요청은 브라우저가 아니라 서버(Vercel의 Node.js 실행 환경)에서 나갔다"는 확실한 증거**였다. 이 덕분에 "클라이언트 쪽에서 React Query가 두 번 요청하는 버그인가?"라는 처음 가설을 접고, "서버 쪽에서 같은 페이지가 두 번 실행되고 있다"는 정확한 방향으로 원인을 좁힐 수 있었다.

(추가 근거: `useAlertStats`, `useDashboardSummary` 훅에는 `staleTime`이 각각 60초/30초로 설정되어 있어서, 만약 클라이언트에서 재요청하는 버그였다면 이 시간 안에는 재요청이 안 나가야 정상이다. 그런데도 2번씩 찍혔다는 건 애초에 클라이언트 재요청이 아니라는 뜻이다.)

---

## 4. 해결 — `prefetch={false}`

원인이 "Link의 자동 프리페치가 무거운 서버 페이지를 미리 통째로 실행시킨다"는 거였으니, 가장 직접적인 해결책은 **그 프리페치를 꺼버리는 것**이다. `<Link>`에 `prefetch={false}`를 추가하면 된다 — 자동으로 미리 받아오지 않고, 사용자가 실제로 클릭했을 때만 그 페이지를 실행한다(원래 `<a>` 태그와 비슷한 동작이 되지만, 클릭 시 페이지 전체를 새로고침하지 않고 필요한 부분만 바꾸는 Next.js의 장점은 그대로 유지된다).

```tsx
// Header.tsx (수정 후)
<Link key={href} href={href} prefetch={false} className="...">
  {name}
</Link>
```

```tsx
// AlertsClient.tsx (수정 후)
<Link href={href} prefetch={false} className="...">
  ...
</Link>
```

```tsx
// LatestAlertsSection.tsx (수정 후)
<Link href={`/alerts/${a.id}`} prefetch={false} className="feed-item">
  ...
</Link>
```

같은 이유로 `EventsClient.tsx`(이벤트 목록 → 상세)와 `HomeClient.tsx`(홈의 "전체보기 → /alerts" 링크)에도 동일하게 적용했다. `force-dynamic` 자체는 건드리지 않았다 — 홈의 실시간 통계, 목록의 필터 결과가 항상 최신이어야 한다는 원래 요구사항은 그대로 유효하기 때문이다. **"미리 준비하지 말고, 실제로 필요할 때만 하자"** 는 부분만 고쳤다.

---

## 5. 왜 다른 방법 대신 이 방법을 골랐나

과다 호출을 없애는 방법은 이론적으로 여러 개가 있었다.

| 대안 | 장점 | 왜 이번엔 안 골랐나 |
|---|---|---|
| **`prefetch={false}` (채택)** | 코드 몇 줄이면 끝, 위험 낮음, 원래 페이지 동작은 그대로 | — |
| 각 라우트에 `loading.tsx` 추가 | Next.js가 권장하는 "정석" 대응 | 프리페치가 로딩 화면까지만 받아오게 될 뿐, 그래도 뷰포트에 있는 모든 링크가 최소한 그 정도는 계속 프리페치됨 — 이번 문제(개별 API 과다 호출)의 근본 해결책은 아님 |
| 개별 상세 호출을 배치 API(`GET /alerts?ids=...`)로 묶기 | N+1 자체를 구조적으로 제거 | 백엔드 API를 새로 만들어야 하는 더 큰 변경. 프리페치 버그가 사라지면 애초에 "여러 개를 한꺼번에 미리 부르는" 상황 자체가 없어지므로 지금 당장은 필요 없다고 판단, 필요해지면 별도 작업으로 진행 |
| 서버에서 쓰는 axios 호출을 Next의 캐싱 `fetch()`로 교체 | 실제 방문 시에도 짧은 시간 내 캐시로 응답, 백엔드 부하 추가 절감 | 응답 신선도(실시간 오늘 통계 등)와 트레이드오프가 있어 별도 논의 후 진행하기로 보류 |

이번 문제의 본질은 "사용자가 원하지도 않은 페이지를 미리 실행하고 있었다"는 것이었기 때문에, 그 원인(자동 프리페치)을 직접 끄는 게 가장 적은 위험으로 가장 정확하게 문제를 없애는 방법이었다.

---

## 6. 검증

- `npx tsc --noEmit` 통과 (타입 오류 없음).
- `next lint`는 이 환경에서 별개 이유로 실행 자체가 깨져 있어(사전 문제, 이번 변경과 무관) 스킵.
- 아직 `npm run dev`로 홈/목록/상세를 직접 클릭해보며 "링크는 여전히 잘 눌리는지, 페이지 전환이 부자연스러워지지 않았는지" 눈으로 확인은 안 함 — `prefetch={false}`는 Next.js 공식 API라 동작 자체의 리스크는 낮지만, 실제 배포 전에 한 번은 눈으로 확인이 필요하다.

## 7. 예상 효과 (실측 전, 추정)

- 홈/목록에 나열된 링크가 화면에 뜨기만 해도 발생하던 상세 페이지(개별 `/alerts/{id}`, `/events/{id}`) 프리페치가 전부 사라지므로, 실제 클릭 대비 압도적으로 많던 이 트래픽은 **체감상 대부분(80~90% 이상)** 사라질 것으로 예상.
- Header가 모든 페이지에 떠 있어서 "어느 페이지를 보든 `/`·`/alerts`가 백그라운드로 다시 실행"되던 부분이 없어지므로, `/stats`·`/stats/sido`·`/search/combined`·`/dashboard/summary` 호출은 "실제로 그 페이지를 본 만큼"으로 줄어들 것으로 예상.
- 정확한 수치는 배포 후 Caddy 로그를 다시 받아 Before(이 문서 작성 시점 로그)와 비교해서 확인해야 한다 — 다음 개발일지에서 실측치로 업데이트 예정.

## 8. 남은 항목

1. `npm run dev`로 실제 클릭 동작(골든 패스) 확인 후 커밋.
2. 배포 후 며칠간 Caddy 로그를 다시 받아 Before/After 요청 수 비교.
3. (선택) 서버 prefetchQuery에서 쓰는 axios 호출을 `fetch() + next: { revalidate }` 캐싱 방식으로 바꿔 실방문 트래픽 자체도 줄이는 건 — 데이터 신선도 요구사항과 맞춰서 별도로 논의.