# 워터폴/중복 fetch 제거 — 이벤트 상세 시군구 번역 & AlertRiskSection 분석 기록

- 작성일: 2026-07-27
- 대상 파일: `frontend/src/app/events/[id]/page.tsx`, `frontend/src/components/alerts/AlertRiskSection.tsx`
- 원 출처: [프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 "다음 단계" 2번(워터폴 제거)

---

## 0. 결론 먼저

이번 항목은 "워터폴 2개를 찾아서 둘 다 없앴다"가 아니라, **조사해보니 성격이 다른 두 사례였다**는 게 핵심이다.

| 대상 | 워터폴 성격 | 조치 |
|---|---|---|
| `events/[id]` 시군구 번역 fetch | **가짜 워터폴** — `useEffect` + raw `fetch`로 짜여 있어 캐시가 안 타고, 매 방문마다 재요청됨. 의존 관계 자체(이벤트→지역)는 필연이지만, "요청이 몇 번 나가는가"는 고칠 수 있었음 | **수정함** — React Query 캐시 훅(`useSigungu`)으로 교체 |
| `AlertRiskSection` 위험도 2단계 조회 | **진짜 워터폴** — `topCode`(대표 지역)가 첫 응답의 데이터에서만 계산 가능해서, 두 번째 요청이 첫 번째 요청 없이는 애초에 무엇을 물어봐야 할지 알 수 없음. 프론트 코드를 아무리 잘 짜도 이 순서는 못 없앰 | **수정 안 함** — 이유는 2장에 상세 기록. 진짜로 없애려면 백엔드가 "대표 지역 판정" 로직까지 넘겨받아야 함 |

즉 "useEffect를 없애면 워터폴이 없어진다"는 말은 반은 맞고 반은 틀리다 — **이번 두 사례를 비교하면 그 경계가 정확히 어디인지 보인다.** 1장에서 그 경계를 가르는 기준(useEffect의 동작 원리)을 먼저 설명하고, 2~3장에서 각 사례에 적용한다.

---

## 1. useEffect 작동 원리 — 이 판단의 근거

### 1-1. React 렌더링 사이클: render → commit → paint → effect

React 컴포넌트가 리렌더링될 때 벌어지는 일은 순서대로 이렇다.

1. **Render phase**: 함수 컴포넌트 본문이 실행되며 새 JSX(가상 DOM)를 계산한다. 이 단계는 **순수해야 한다** — 네트워크 요청, `localStorage` 쓰기, DOM 직접 조작 같은 부수효과(side effect)를 여기서 하면 안 된다는 것이 React의 규칙이다. (같은 렌더가 StrictMode 등으로 두 번 실행될 수도 있고, Concurrent 렌더링에서 중간에 버려질 수도 있기 때문.)
2. **Commit phase**: 계산된 결과를 실제 DOM에 반영한다. `useLayoutEffect`는 이 시점 직후, **브라우저가 화면을 그리기 전에** 동기적으로 실행된다.
3. **Paint**: 브라우저가 실제로 화면을 그린다.
4. **Passive effect phase**: `useEffect`에 등록한 콜백이 **paint 이후, 별도의 매크로태스크로** 실행된다. "화면을 먼저 보여주고, 그 다음에 부수효과를 처리한다"는 것이 `useEffect`가 `useLayoutEffect`보다 느리게, 그리고 비동기적으로 도는 이유다.

즉 `useEffect(fn, deps)`는 "이 컴포넌트가 (처음이든 업데이트든) 화면에 반영되고 난 뒤, `deps` 배열의 값이 이전 렌더와 다르면 `fn`을 실행하라"는 뜻이다. **네트워크 요청처럼 렌더 중에는 할 수 없는 부수효과를, 정확히 이 사이클에 맞춰 안전하게 트리거하기 위한 도구**가 `useEffect`다.

### 1-2. 의존성 배열(`deps`)은 어떻게 비교되나

React는 매 렌더마다 `deps` 배열의 각 원소를 **이전 렌더의 값과 `Object.is`로 얕은 비교**한다. 하나라도 다르면 effect를 다시 실행한다.

- 원시값(문자열/숫자/불리언)은 값 자체가 같으면 "같다"로 취급된다.
- 객체/배열/함수는 **참조가 같아야 "같다"**로 취급된다. 렌더마다 새로 생성되는 객체(`{ ...params }` 같은 것)를 deps에 넣으면, 내용이 같아도 매 렌더 effect가 재실행된다 — `useMemo`/`useCallback`으로 참조를 고정하지 않으면 무한 재실행 함정에 빠지기 쉬운 이유.
- deps 배열을 아예 생략하면 매 렌더마다, `[]`면 마운트 시 1회만 실행된다.

### 1-3. cleanup 함수와 "취소" 패턴

`useEffect`가 함수를 반환하면 그게 cleanup이다. cleanup은 **다음 effect가 실행되기 직전**과 **컴포넌트가 언마운트될 때** 호출된다. `events/[id]`의 원래 코드가 쓰던 패턴이 정확히 이거다.

```ts
useEffect(() => {
  let cancelled = false;
  fetchSigungu(sido, lang).then((list) => {
    if (cancelled) return;   // ← 이 fetch가 "구식" 응답이면 무시
    setSigunguMap(...);
  });
  return () => { cancelled = true; };  // ← cleanup: 다음 effect 직전에 이전 요청을 무효화
}, [data, lang]);
```

왜 이게 필요하냐면: `lang`이 한국어→영어→중국어로 빠르게 바뀌면 `fetchSigungu("서울특별시", "en")`과 `fetchSigungu("서울특별시", "zh")`가 거의 동시에 나갈 수 있고, 네트워크 상황에 따라 **먼저 보낸 "en" 요청이 나중에 도착**할 수 있다(요청-응답 순서 역전). cleanup의 `cancelled` 플래그가 없으면, 사용자는 "zh"를 선택했는데 화면엔 늦게 도착한 "en" 결과가 덮어써지는 버그가 생긴다. 이건 원래 코드가 **정확하게 잘 짜여 있었던 부분**이다 — 문제는 이 자체가 아니라, 이 패턴을 매번 손으로 재구현하고 있었다는 점이다.

### 1-4. 그래서 useEffect+fetch는 왜 "느린" 패턴인가

핵심은 **"느리다"의 의미가 두 가지**라는 점이다.

1. **타이밍(hop) 관점**: `useEffect`는 paint 이후에 실행되므로, "부모 데이터가 준비된 시점"과 "자식 요청이 실제로 나가는 시점" 사이에 최소 한 번의 렌더+커밋+페인트 텀이 낀다. 하지만 이건 **React Query의 `useQuery`도 내부적으로 effect를 통해 fetch를 트리거하므로 동일하게 겪는 지연**이다 — React 렌더 규칙상 fetch는 어차피 render phase 밖에서 시작해야 하기 때문. 즉 "raw useEffect vs React Query" 비교에서 이 hop 자체는 승부처가 아니다.
2. **캐싱/중복 제거 관점**: 진짜 차이는 여기다. raw `useEffect` + `fetch`는 **아무 캐시도 없다** — 같은 `sido`+`lang` 조합이어도 컴포넌트가 마운트될 때마다(= 다른 이벤트 상세 페이지로 이동할 때마다) 네트워크를 다시 탄다. `useQuery`는 `queryKey`가 같으면 `staleTime` 안에서 캐시를 재사용하고, 이미 나가 있는 동일 키 요청과 자동으로 dedupe하며, 컴포넌트가 여러 개 동시에 같은 데이터를 구독해도 요청은 1번만 나간다. **여기서 이번 수정의 실익이 나온다** — hop을 줄인 게 아니라, "몇 번 요청하느냐"를 줄인 것.

### 1-5. 왜 AlertRiskSection의 워터폴은 못 없애나

`AlertRiskSection`에는 위 문제의 원인이 되는 `useEffect`가 애초에 **없다**. `topCode`는 `useAlertRisk`가 반환한 `data`로부터 `useMemo`로 **같은 렌더 안에서** 동기적으로 계산되고, `useRegionRisk(topCode)`/`useRegionRiskHistory(topCode, ...)`는 `topCode`가 정해지자마자 그 즉시(다음 렌더에서) `enabled: true`로 전환되어 요청이 나간다 — 인위적으로 hop을 하나 더 만드는 실수가 없다는 뜻이다.

문제는 **의존관계 자체가 데이터에 있다**는 것이다 — "이 알림이 영향을 준 지역 목록"을 모르면 "그중 대표 지역의 현재 위험도"를 물어볼 수조차 없다. 이 순서를 없애려면:

- 백엔드의 alert-risk 응답(`/api/v1/regions/alerts/{id}/risk`)이 대표 지역 코드까지 스스로 판정해서, 그 지역의 현재 위험도·추이까지 한 응답에 담아야 한다.
- 그런데 "대표 지역"을 고르는 로직(`aggregateBySigungu` + 정렬, `frontend/src/lib/riskScore.ts`)은 지금 **프론트엔드에만** 있다. 백엔드로 옮기면 두 곳에 같은 로직이 중복되거나(드리프트 위험), 프론트 로직을 통째로 백엔드로 이관해야 한다 — DTO 계약 변경 + 다른 화면(지도 등)에서의 재사용 여부까지 함께 설계해야 하는, 이번 범위보다 큰 작업이다.

그래서 이번엔 **손대지 않고 원인만 정확히 기록**하는 쪽을 택했다. "왜 안 고쳤는지"도 "무엇을 고쳤는지"만큼 근거가 있어야 한다는 게 이번 기록의 요지다.

---

## 2. 수정한 것: `events/[id]` 시군구 번역 fetch

### 2-1. Before

`frontend/src/app/events/[id]/page.tsx` (수정 전):

```ts
import { fetchSigungu } from "@/api/alertApi";
// ...

const [sigunguMap, setSigunguMap] = useState<Map<string, string> | null>(null);
useEffect(() => {
  setSigunguMap(null);
  if (lang === "ko" || !data?.primaryRegionName) return;
  const { sido, sigungu } = splitRegion(data.primaryRegionName);
  if (!sido || !sigungu) return;
  let cancelled = false;
  fetchSigungu(sido, lang)
    .then((list) => {
      if (cancelled) return;
      const map = new Map<string, string>();
      (list ?? []).forEach((s) => { if (s.translatedName) map.set(s.name, s.translatedName); });
      setSigunguMap(map);
    })
    .catch(() => { /* 번역 실패 시 원문 유지 */ });
  return () => { cancelled = true; };
}, [data, lang]);
```

- `useEvent(id, lang)`의 `data`가 준비된 뒤에야 이 effect가 도는 것 자체는 불가피(1-5 참고와 동일한 이유: 지역명을 이벤트 데이터에서 뽑아야 함).
- 하지만 **캐시가 전혀 없다.** 같은 시/도의 이벤트 상세를 A → B → A 순서로 방문하면 `fetchSigungu("서울특별시", "en")`이 **3번 다** 새로 나간다.
- `cancelled` 플래그, `null` 초기화, `catch` 처리를 매번 손으로 구현해야 한다 — 이미 프로젝트에 있는 `useSigungu` 훅이 이 모든 걸 캡슐화하고 있는데도 안 쓰고 있었다.

### 2-2. After

```ts
import { useSigungu } from "@/lib/queries/useAlerts";
// ...

const { sido: primarySido, sigungu: primarySigungu } = useMemo(
  () => splitRegion(data?.primaryRegionName),
  [data]
);
const { data: sigunguList } = useSigungu(
  lang !== "ko" && primarySido && primarySigungu ? primarySido : undefined,
  lang
);
const sigunguMap = useMemo(() => {
  const map = new Map<string, string>();
  (sigunguList ?? []).forEach((s) => { if (s.translatedName) map.set(s.name, s.translatedName); });
  return map;
}, [sigunguList]);
```

`useSigungu`는 이미 `frontend/src/lib/queries/useAlerts.ts`에 있던 훅이다 (`KakaoPolygonMap`, `alerts/page.tsx` 등에서 이미 쓰이고 있음):

```ts
export function useSigungu(sido: string | undefined, lang = "ko") {
  return useQuery({
    queryKey: ["sigungu", sido, lang],
    queryFn: () => fetchSigungu(sido!, lang),
    enabled: !!sido,
    staleTime: Infinity,   // ← 법정동 이름은 사실상 불변 데이터라 무기한 캐시
  });
}
```

### 2-3. 무엇이 실제로 바뀌었나

| 항목 | Before | After |
|---|---|---|
| 이벤트→지역 의존관계(hop 자체) | 있음 (필연) | 있음 (동일, 제거 불가) |
| 같은 시/도를 여러 이벤트에서 재방문 시 요청 횟수 | 매번 재요청 | **최초 1회만 요청, 이후 캐시 히트 (`staleTime: Infinity`)** |
| 동시에 여러 컴포넌트가 같은 sido+lang을 구독할 경우 | 각자 별도 요청 | React Query가 자동 dedupe |
| race condition 방지(cancelled 패턴) | 수동 구현 | React Query가 queryKey 변경 시 자동 처리 |
| 코드량/보일러플레이트 | `useState` + `useEffect` + 수동 cancel + catch | `useMemo` 2개, 표준 훅 재사용 |

이 표의 "요청 횟수 감소"는 [지도_시군구통계_API중복호출_통합_before-after.md](./지도_시군구통계_API중복호출_통합_before-after.md)에서 이미 같은 `staleTime: Infinity`/캐시 재사용 원리를 실측으로 검증한 것과 동일한 React Query 동작(라이브러리 문서상 결정론적 동작)에 근거한다 — 이번 세션에서 세 번째로 같은 원리를 확인하는 셈이라 별도 재측정은 하지 않았다.

---

## 3. 검증

- `npx tsc --noEmit` 통과 (타입 에러 없음).
- `npx eslint "src/app/events/[id]/page.tsx"` 통과 (경고/에러 없음, `useEffect` import 제거로 인한 미사용 import 없음 확인).
- `AlertRiskSection.tsx`는 코드 변경 없음 — 2장의 분석이 "왜 안 건드렸는지"에 대한 근거.

---

## 4. 남은 항목

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 기준:

1. ~~`KakaoPolygonMap` 4중 호출 통합~~ — 완료
2. ~~워터폴 제거~~ — **완료 (이 문서)**: `events/[id]`는 캐시 훅으로 전환, `AlertRiskSection`은 구조적으로 불가피함을 확인하고 보류(백엔드 "대표 지역 판정 로직 이관"이 선행돼야 하는 별도 과제로 분리)
3. SSR prefetch 도입 — 핵심 목록 페이지 최초 로딩 속도 개선
4. 무거운 컴포넌트(`KakaoPolygonMap`, `KoreaMap25D`, stats 차트) `next/dynamic` 전환
