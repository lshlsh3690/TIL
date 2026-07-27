# SSR Prefetch 확장 — 이벤트 목록 페이지 Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults`
- 관련 커밋: `72fd68a` perf(events): 이벤트 목록 페이지에 SSR prefetch 도입
- 파일: `frontend/src/lib/eventsSearchParams.ts`(신규), `frontend/src/app/events/page.tsx`, `frontend/src/app/events/EventsClient.tsx`(신규)

---

## 1. alerts와 거의 동일한 패턴 — 그대로 복제

[SSR_prefetch_목록페이지_before-after.md](./SSR_prefetch_목록페이지_before-after.md)에서 "범위를 과대평가했다"고 인정한 뒤, 실제로 `alerts` 목록에 적용한 지 얼마 안 돼 `events` 목록도 같은 구조라는 게 확인됐다. 필터가 `react-hook-form` + `useSearchParams()`로 관리되는 것도 같고, "서버가 prefetch할 params와 클라이언트 첫 렌더 params를 맞춘다"는 과제도 동일했다.

`alertsSearchParams.ts`와 거의 대칭되는 `eventsSearchParams.ts`를 만들어 그대로 복제:

```ts
export function parseEventsSearchForm(sp): EventsSearchForm { ... }  // URL → 폼 상태 (빈 문자열 기반)
export function parseEventsTab(sp): EventsActiveTab { ... }          // active=true/false/없음 → 탭
export function parseEventsPage(sp): number { ... }                  // page 파라미터
export function buildEventsListParams(f, tab, page, size, lang) { ... }  // → useSearchEvents에 넘길 params
```

서버(`page.tsx`)와 클라이언트(`EventsClient.tsx`)가 이 4개 함수만 공유하면 되는 구조라, 코드량 자체는 alerts 때보다 오히려 적게 들었다 (이미 검증된 레시피를 그대로 따라가기만 하면 됐음).

---

## 2. alerts와 달랐던 점 — 이 페이지엔 "빈 필터 이중 요청" 버그가 없었다

alerts 목록에서는 `formState`가 `useState({})`로 시작해서 URL에 필터가 있어도 첫 렌더가 항상 "필터 없음"으로 한 번 요청되는 기존 버그를 발견하고 같이 고쳤다. `events/page.tsx`는 원래부터:

```ts
const [tab, setTab] = useState<ActiveTab>(() => { /* searchParams 파싱 */ });
const [page, setPage] = useState(() => { /* searchParams 파싱 */ });
const [formState, setFormState] = useState<SearchForm>(() => readFormFromParams(searchParams));
```

**이미 지연 초기화(lazy init)로 짜여 있었다** — 커밋 로그를 보진 않았지만 아마 alerts보다 나중에 작성됐거나, 다른 사람이 이 부분만 먼저 신경 써서 짰을 가능성이 있다. 덕분에 이번엔 "덤으로 버그 발견"은 없었고, SSR prefetch만 순수하게 얹는 작업이었다.

---

## 3. 검증

실제 백엔드로 두 가지 시나리오를 확인:

```bash
curl "http://localhost:3000/events" | grep -oE 'href="/events/[0-9]+' | sort -u
# → 10개 실제 eventId 링크, size=10과 일치

curl "http://localhost:3000/events?active=true" | grep -o '\"active\":[a-z]*'
# → "active":true  (dehydrate된 쿼리 파라미터가 URL 필터를 정확히 반영)
```

필터 없음 / `active=true` 필터 둘 다 SSR HTML에 실제 이벤트 목록이 이미 포함돼 있고, dehydrate된 캐시의 `queryKey`가 클라이언트가 쓸 키와 정확히 일치함을 확인했다.

---

## 4. 남은 SSR 후보 (전수 조사 결과, 참고용)

사용자 요청으로 전체 페이지를 훑어 정리한 결과:

| 페이지 | 상태 |
|---|---|
| `/`, `/alerts`, `/alerts/[id]`, `/events/[id]`, `/events` | ✅ 완료 |
| `/community` | 대상이지만 이번엔 제외 (사용자가 명시적으로 보류 요청) — tab이 URL에도 없이 `useState`로 고정 시작이라 홈보다도 쉬운 케이스 |
| `/notifications`, `/user/me`, `/user/settings/regions` | 로그인 사용자 전용 데이터 — "공개 데이터 prefetch"와는 다른, 쿠키를 서버 컴포넌트까지 넘기는 설계가 필요한 별개 작업 |
| `/stats` | 위젯 레이아웃이 `localStorage` 프리셋 기반이라 서버가 "무엇을 prefetch해야 할지" 알 수 없는 구조 — SSR과 근본적으로 안 맞음 |
| `/alerts/[id]/edit`, `/alerts/new`, `/login`, `/signup` | 폼 전용, prefetch할 조회 데이터 없음 |
| `/missing`, `/user/[id]`, `/user/delete`, `/alerts/map`, `/community/[id]`, `/test` | 플레이스홀더 스텁, 실제 데이터 연동 전 |

즉 "공개 데이터 + URL 파라미터로만 결정되는" 페이지는 이번 세션에서 사실상 다 처리됐고, 남은 건 성격이 다른 인증 기반 페이지들이다.
