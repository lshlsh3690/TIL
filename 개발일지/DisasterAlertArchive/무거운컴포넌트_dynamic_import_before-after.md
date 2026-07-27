# 무거운 컴포넌트 next/dynamic 전환 — Before / After 기록

- 작성일: 2026-07-27
- 브랜치: `perf/frontend-query-cache-defaults`
- 관련 커밋: `505ab31` perf(map): KoreaMap25D/KakaoPolygonMap을 next/dynamic으로 지연 로드
- 파일: `frontend/src/app/HomeClient.tsx`, `frontend/src/app/alerts/page.tsx`
- 원 출처: [프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 4번 — 마지막 남은 항목

---

## 1. 문제 (Before)

`KoreaMap25D`(홈, `/`)와 `KakaoPolygonMap`(`/alerts`)이 둘 다 파일 최상단에서 정적으로 `import` 되고 있었다.

```ts
// HomeClient.tsx (before)
import KoreaMap25D from "@/components/map/KoreaMap25D";

// app/alerts/page.tsx (before)
import KakaoPolygonMap from "@/components/map/KakaoPolygonMap";
```

정적 import는 "이 컴포넌트의 코드를 이 페이지의 초기 JS 번들에 무조건 포함시켜라"는 뜻이다. 두 컴포넌트 다 지도 좌표 데이터, 폴리곤 렌더링 로직, (Kakao 쪽은) 외부 SDK 로더까지 포함하고 있어 가볍지 않다. 반면 이 프로젝트에는 이미 같은 문제를 겪은 뒤 고친 사례가 있었다 — `AlertRiskSection.tsx`의 `AlertRiskMap`:

```ts
// AlertRiskSection.tsx (기존, 참고 삼은 패턴)
const AlertRiskMap = dynamic(() => import("@/components/map/AlertRiskMap"), {
  ssr: false,
  loading: () => <div className="h-[472px] bg-gray-100 rounded-lg animate-pulse" />,
});
```

즉 이번 작업은 "새로운 기법 도입"이 아니라 **이미 검증된 사내 패턴을 나머지 두 곳에 똑같이 적용**한 것에 가깝다.

---

## 2. 해결 (After)

```ts
// HomeClient.tsx (after)
const KoreaMap25D = dynamic(() => import("@/components/map/KoreaMap25D"), {
  ssr: false,
  loading: () => (
    <div className="animate-pulse rounded-lg bg-gray-100" style={{ height: "clamp(650px, calc(100vh - 96px), 920px)" }} />
  ),
});

// app/alerts/page.tsx (after)
const KakaoPolygonMap = dynamic(() => import("@/components/map/KakaoPolygonMap"), {
  ssr: false,
  loading: () => <div className="h-[520px] animate-pulse rounded-lg bg-gray-100" />,
});
```

`next/dynamic(() => import(...), { ssr: false })`가 하는 일 두 가지:
1. **코드 스플리팅**: 이 컴포넌트의 코드를 페이지의 메인 청크가 아니라 별도의 JS 청크로 분리한다. 페이지가 처음 로드될 때 이 청크는 아예 요청되지 않고, React가 실제로 이 컴포넌트를 렌더링하려는 시점에야 다운로드된다.
2. **`ssr: false`**: 서버 렌더링(SSR) 대상에서 제외한다 — 서버는 이 컴포넌트를 렌더링하지 않고 `loading` 자리표시자만 HTML에 넣는다. 브라우저에서 hydrate된 뒤에야 실제 컴포넌트가 그려진다.

`loading`은 실제 컴포넌트가 차지할 공간과 비슷한 높이로 지정해 CLS(레이아웃 밀림)를 방지했다 — 이것도 `AlertRiskMap`의 기존 방식을 그대로 따른 것.

---

## 3. 검증 — SSR 응답으로 실제 분리를 직접 확인

"컴포넌트가 진짜로 서버 렌더링에서 빠졌는지"를 짐작이 아니라 실측하기 위해, 변경 전/후 SSR HTML을 직접 비교했다. (`git stash`로 두 파일만 잠깐 되돌려 dev 서버가 HMR로 반영하게 한 뒤 `curl`로 각각 받고, 다시 `git stash pop`으로 복원.)

### 3-1. 홈 페이지 (`/`)

| 확인 항목 | Before | After |
|---|---|---|
| `animate-pulse`(로딩 스켈레톤) 등장 횟수 | 0 | **1** |
| `<g` 태그(KoreaMap25D가 SVG 지역을 그룹핑하는 요소) 등장 횟수 | 1 | **0** |

Before는 서버가 실제로 `<g>`로 시작하는 지도 SVG 콘텐츠를 렌더링해서 응답에 포함시켰고, After는 그 자리에 스켈레톤 `div`만 있다 — **컴포넌트의 렌더 결과 자체가 서버 렌더링 경로에서 완전히 빠졌다**는 뜻이다.

### 3-2. 재난문자 목록 페이지 (`/alerts`)

| 확인 항목 | Before | After |
|---|---|---|
| `KakaoPolygonMap` 루트 래퍼 클래스(`bg-white rounded-xl shadow overflow-hidden`) 등장 횟수 | 1 | **0** |
| `animate-pulse` 등장 횟수 | 0 | **1** |

Before는 이 클래스 문자열이 SSR HTML에 그대로 있었다 — 즉 Kakao SDK가 로드되기 전이라도 **컴포넌트 자체는 서버에서 렌더링되어 응답에 포함**돼 있었다는 뜻(지도 타일은 어차피 클라이언트에서만 그려지지만, React 컴포넌트 트리 자체는 서버가 계산해서 내려보냄). After는 이 클래스가 완전히 사라지고 스켈레톤만 남았다.

이 결과는 "정적 import를 dynamic으로 바꾸면 번들이 작아진다"는 일반론을 이 프로젝트의 실제 두 라우트에서 직접 재현한 것이다. `next build`(Turbopack)로 프로덕션 빌드는 정상적으로 통과했지만, 이번 Next.js 버전(16.2.4, Turbopack build)의 출력 포맷에서는 라우트별 "First Load JS" 바이트 수 표가 예전 Webpack 빌드처럼 뜨지 않아 — 정확한 KB 절감치는 이번엔 못 뽑았고, 대신 위처럼 "서버가 실제로 이 컴포넌트를 그렸는지"를 직접 확인하는 방식으로 검증을 대체했다.

---

## 4. 왜 stats 페이지 차트(recharts)는 그대로 뒀나

최초 조사(`프론트엔드_데이터페칭_속도개선_before-after.md`)에서 `/stats` 페이지의 recharts 기반 위젯들(`_DistributionCharts.tsx`, `_TimeCharts.tsx`, `_WeatherCharts.tsx`)도 무거운 정적 import 후보로 지목됐었다. 하지만 다시 보니:

- Next.js App Router는 **라우트 단위로 이미 자동 코드 스플리팅**을 한다 — `/stats`를 방문하지 않는 사용자는애초에 recharts를 포함한 `/stats` 청크를 전혀 다운로드하지 않는다. `KoreaMap25D`/`KakaoPolygonMap`은 이 자동 분리의 혜택을 못 받았다(각각 `/`, `/alerts`라는, **트래픽이 제일 많은 라우트 자체**에 정적으로 박혀 있었으니까). recharts는 애초에 그 문제가 없었다는 뜻.
- `/stats` 안에서 위젯 종류별로 **추가로** 잘게 쪼개는 것(예: `DonutChart`만 쓰는 레이아웃인데 `WeatherByRegionChart` 코드까지 로드되는 것을 막는 것)은 이득이 있을 수 있지만, `_WidgetCard.tsx`의 `WidgetContent` 디스패처가 위젯 종류별로 이미 15개 안팎의 차트 컴포넌트를 다루고 있어 — 이걸 전부 개별 `dynamic()`으로 바꾸면 로딩 상태 처리가 15곳으로 늘어나는 작업량 대비, 이미 라우트 단위로 격리된 상태라 실이익은 상대적으로 작다.

그래서 이번엔 손대지 않고, **"이미 라우트 스플리팅으로 격리돼 있다"는 근거와 함께 범위에서 제외**했다 — 실제 번들 크기를 재서 문제가 확인되면 그때 후속 작업으로 진행하는 게 맞다고 판단.

---

## 5. 전체 진행 상황

[프론트엔드_데이터페칭_속도개선_before-after.md](./프론트엔드_데이터페칭_속도개선_before-after.md) 4장 기준, 4개 항목 모두 완료:

1. ~~`KakaoPolygonMap` 4중 호출 통합~~ — 완료
2. ~~워터폴 제거~~ — 완료
3. ~~SSR prefetch 도입~~ — 완료 (홈 + 상세 페이지; 목록 페이지는 별도 과제로 분리)
4. ~~무거운 컴포넌트 동적 import~~ — **완료 (이 문서)**

"프론트엔드 데이터 뜨는 속도 개선"으로 시작된 작업이 캐시 설정 → API 중복 호출 제거 → 워터폴 정리 → SSR prefetch → 번들 분리까지 5개 커밋으로 이어진 흐름 전체는 `프론트엔드_데이터페칭_속도개선_before-after.md`를 시작점으로 이 폴더의 문서들을 순서대로 따라가면 재구성할 수 있다.
