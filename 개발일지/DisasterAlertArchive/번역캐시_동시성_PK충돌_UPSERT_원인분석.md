# 번역 캐시 동시 저장 시 HTTP 500 — 원인 분석과 UPSERT 해결법

- 작성일: 2026-08-10
- 코드 스냅샷 확인일: 2026-08-12 (`TranslationService.java`, `DisasterAlertService.java` 실제 소스와 대조 후 코드블록 갱신)
- 관련 명세: `specs/005-translation-pipeline/spec.md` (예외 상황 절), `plan.md` (미해결 항목 #1)
- 상태: **아직 안 고침**. 원인만 확정된 상태이고, 이 문서는 왜 생기는지와 어떻게 고칠지를 정리한 것이다.
- 대상 독자: JPA/트랜잭션을 막 배우기 시작한 사람. 용어는 나올 때마다 풀어서 설명한다.

---

## 0. 한 줄 요약

**두 사람이 동시에 같은 재난문자를 같은 외국어로 열면, 번역 저장이 충돌해서 페이지가 500 에러로 죽는다.** 분명히 `try/catch`로 감쌌는데도 안 잡힌다. 이유는 "예외가 `try` 블록을 빠져나온 **뒤에** 터지기 때문"이다.

---

## 1. 배경 — 번역 캐시가 뭔가

우리 서비스는 재난문자를 5개 언어(영어/일본어/중국어/베트남어/태국어)로 번역해서 보여준다. 그런데 번역은 OpenAI API를 호출해야 해서 **느리고 돈이 든다**. 그래서 한 번 번역하면 DB에 저장해 두고, 다음부터는 저장된 걸 꺼내 쓴다. 이걸 **캐시(cache)** 라고 한다.

저장 테이블은 이렇게 생겼다.

```sql
CREATE TABLE disaster_alert_translation (
    disaster_alert_id  BIGINT      NOT NULL,   -- 어떤 재난문자인지
    language_code      VARCHAR(10) NOT NULL,   -- 어떤 언어인지 ('JA', 'ZH' ...)
    translated_message TEXT        NOT NULL,   -- 번역된 본문
    ...
    PRIMARY KEY (disaster_alert_id, language_code)   -- ★ 이 조합은 딱 하나만 존재 가능
);
```

마지막 줄이 핵심이다. **PRIMARY KEY(기본키)** 는 "이 조합은 테이블에 딱 한 줄만 있어야 한다"는 DB의 약속이다. 여기서는 `(재난문자 ID, 언어 코드)` 조합이 기본키다.

> 예: `(102596, 'JA')` 라는 조합의 행은 **최대 1개**. 두 번 넣으려고 하면 DB가 거부한다.

이 약속 덕분에 같은 번역이 중복 저장되지 않는다. 그런데 바로 이 약속이 오늘의 사고 원인이다.

---

## 2. 문제의 코드 — "확인하고 저장한다"

번역을 꺼내는 실제 코드다. (`TranslationService.java:81-95`, 오늘 기준 그대로)

```java
@Transactional
public void ensureTranslated(Long alertId, SupportedLanguage language) {
    if (!properties.isEnabled()) {
        return;
    }
    if (translationRepository.findByIdAlertIdAndIdLanguageCode(alertId, language.getDbCode()).isPresent()) {
        return;
    }
    try {
        translateAndSaveInternal(alertId, language);
    } catch (Exception e) {
        log.warn("상세 조회 lazy 번역 실패(원문 폴백): alertId={}, lang={}, reason={}",
                alertId, language.getDbCode(), e.getMessage());
    }
}
```

읽어보면 멀쩡해 보인다. "있으면 넘어가고, 없으면 만들고, 실패하면 원문 보여준다." 실제로 **혼자 쓸 때는 아무 문제 없다.**

문제는 이 패턴의 이름에 있다. 이런 걸 **check-then-act(확인하고 행동하기)** 라고 부르는데, 동시성 버그의 대표적인 원인이다.

---

## 3. 동시에 두 요청이 들어오면 — 타임라인으로 보기

사용자 A와 B가 **거의 동시에** 같은 재난문자를 일본어로 열었다고 하자.

```
시간 ──────────────────────────────────────────────────────────▶

A:  ①확인 "번역 없네"
B:      ①확인 "번역 없네"          ← A가 아직 저장을 안 했으므로 B도 "없다"고 본다
A:          ②OpenAI 번역 (2초)
B:          ②OpenAI 번역 (2초)
A:                      저장 시도 → 성공
B:                      저장 시도 → 💥 충돌! (102596, 'JA')는 이미 있음
```

핵심은 **①확인과 ②저장 사이에 시간 간격이 있다**는 것이다. 그 사이에 다른 요청이 끼어들 수 있다. 번역은 OpenAI를 호출하느라 1~2초가 걸리므로, 이 틈은 생각보다 아주 넓다.

> **비유**: 화장실 문에 자물쇠가 없고 "비어 있나?" 하고 열어보기만 하는 상황. 두 사람이 동시에 열어보면 둘 다 "비었네" 하고 들어간다.

---

## 4. "근데 try/catch 있잖아요?" — 여기가 진짜 함정

가장 이해하기 어려운 부분이다. **`save()`를 `try` 안에서 불렀는데 왜 `catch`가 못 잡을까?**

답은 **`save()`를 부르는 순간에는 INSERT SQL이 DB로 나가지 않기 때문**이다. 왜 안 나가는지를 제대로 알아야 이 버그가 이해된다.

### 4-1. 영속성 컨텍스트 — JPA의 "작업 책상"

JPA에는 **영속성 컨텍스트(Persistence Context)** 라는 게 있다. 쉽게 말해 **트랜잭션 하나마다 하나씩 주어지는 작업 책상**이다. 엔티티를 다룰 때 DB와 직접 대화하는 게 아니라, 일단 이 책상 위에 올려놓고 작업한다.

`save()`를 부르면 실제로는 이런 일이 일어난다.

```
repository.save(entity)
   │
   ├─ ① 엔티티를 영속성 컨텍스트(책상)에 올린다
   └─ ② "이 엔티티를 INSERT 해야 함" 이라는 SQL을 쓰기 지연 SQL 저장소에 예약한다
        (= 할 일 목록에 적어두기. 실제 전송은 아직)
```

이 예약 목록을 **쓰기 지연 SQL 저장소(ActionQueue)** 라고 부른다. 여기 쌓인 SQL이 실제로 DB로 날아가는 동작을 **flush(플러시)** 라고 한다.

> **비유**: 장을 볼 때 물건을 볼 때마다 계산대에 가지 않는다. 일단 카트에 담아두고(예약), 나갈 때 한 번에 계산한다(flush). `save()`는 "카트에 담기"고, INSERT는 "계산"이다.

### 4-2. 왜 굳이 미루나 — 이유 3가지

성능 때문이라고만 하면 설명이 부족하다. 구체적으로 세 가지 이득이 있다.

**① 네트워크 왕복을 줄인다**
DB는 보통 다른 서버에 있다. SQL 한 문장마다 왕복하면 100건 저장에 100번 왕복이다. 모아서 한 번에 보내면(JDBC batch) 왕복이 확 준다.

**② 중간 변경을 합칠 수 있다**
```java
member.setName("A");   // UPDATE 예약
member.setName("B");   // 또 UPDATE?
member.setName("C");   // 또?
```
바로바로 보냈다면 UPDATE가 3번 나간다. 미뤄두면 마지막 상태만 보고 **UPDATE 1번**으로 끝난다.

**③ DB 락을 잡고 있는 시간을 최소화한다 — 이게 제일 중요하다**
DB는 INSERT/UPDATE가 실행되는 순간부터 그 행에 **락(lock)** 을 건다. 락은 트랜잭션이 끝날 때까지 유지되고, 그동안 다른 요청은 그 행을 못 건드린다.

```
[바로 보내는 경우]
트랜잭션 시작 ── INSERT(락 획득!) ── OpenAI 번역 2초... ── 커밋(락 해제)
                     └──────────── 2초 넘게 락 보유 ────────────┘

[미뤄서 보내는 경우]
트랜잭션 시작 ── (예약만) ── OpenAI 번역 2초... ── flush(락 획득) ─ 커밋(락 해제)
                                                      └── 아주 짧게 ──┘
```

락을 오래 잡으면 다른 요청들이 줄줄이 대기한다. 그래서 JPA는 **락이 필요한 순간을 최대한 뒤로 미룬다.** 이건 성능 최적화라기보다 동시성을 지키기 위한 설계다.

### 4-3. 그럼 flush는 언제 일어나나 — 3가지 시점

여기가 이 버그의 핵심이다. flush는 **커밋 때만 일어나는 게 아니다.**

| # | 시점 | 이유 |
|---|------|------|
| ① | **트랜잭션 커밋 직전** | 카트를 계산해야 집에 갈 수 있으니까 |
| ② | **JPQL·네이티브 쿼리를 실행하기 직전** | ★ 아래 설명 |
| ③ | `entityManager.flush()` 직접 호출 | 개발자가 "지금 보내" 라고 명령 |

②가 왜 있냐면, 이런 상황을 막기 위해서다.

```java
repository.save(newAlert);                 // 카트에만 담김. DB엔 아직 없음
List<Alert> list = repository.findAll();   // DB에 물어보면? 방금 저장한 게 안 나온다!
```

방금 저장한 데이터가 조회에서 빠지면 말이 안 된다. 그래서 JPA는 **쿼리를 보내기 전에 카트를 먼저 비운다(flush)**. 이 기본 동작을 `FlushModeType.AUTO`라고 한다.

### 4-4. 우리 코드에서는 정확히 어디서 터지나

`getAlertDetail`을 다시 보자. (`DisasterAlertService.java:551-558`)

```java
SupportedLanguage.fromRequestParam(lang).ifPresent(language -> {
    // 1) 메시지/유형 번역 (없으면 lazy 번역 호출)
    translationService.ensureTranslated(id, language);          // ← try/catch는 이 안에 있다
    translationRepository.findByIdAlertIdAndIdLanguageCode(id, language.getDbCode())
            .ifPresent(t -> {                                    // ← 조회 쿼리!
                dto.setTranslatedMessage(t.getTranslatedMessage());
                dto.setTranslatedDisasterType(t.getTranslatedDisasterType());
            });
    ...
});
```

`ensureTranslated` 안에서 `save()`로 INSERT를 **예약**하고 `try/catch`를 빠져나온다. 그리고 **바로 다음 줄**에서 번역을 다시 꺼내려고 조회 쿼리를 날린다. 이건 위 표의 **②번 상황**이다 — 조회 직전에 flush가 일어나면서 예약해둔 INSERT가 그제야 DB로 날아간다.

```java
try {
    translateAndSaveInternal(...);   // save() → 카트에 담기만 함. 예외 없음.
} catch (Exception e) {
    log.warn(...);                   // ← 여기 안 들어옴!
}
// ── ensureTranslated 메서드 종료, try/catch 범위 벗어남 ──

findByIdAlertIdAndIdLanguageCode(...)   // 💥 조회 직전 flush → INSERT 발사 → PK 충돌!
```

**충돌은 `try/catch`를 이미 빠져나온 뒤에 터진다.** 그래서 안 잡힌다.

(만약 이 조회가 없었다면 커밋 시점까지 미뤄졌을 것이다. 어느 쪽이든 `try/catch` 바깥이라는 결론은 같다.)

> **비유**: 편지를 우체통에 넣는 순간이 아니라, 집배원이 배달하러 갔을 때 "그 주소엔 이미 사람이 산다"는 사실이 드러나는 것. 우체통 앞에서 아무리 기다려도 그 오류를 못 본다.

### 4-5. 참고: 쓰기 지연이 항상 되는 건 아니다

기본키를 DB가 자동으로 매기게 하면(`@GeneratedValue(strategy = IDENTITY)`) 이야기가 달라진다. 이 방식은 **INSERT를 실제로 실행해야만 ID를 알 수 있어서**, `persist()` 시점에 INSERT가 즉시 나간다. 즉 이 경우엔 `try/catch`가 잡는다.

우리 번역 테이블은 `(alertId, languageCode)`를 **직접 지정하는 복합키**(`@EmbeddedId`)라 DB에 물어볼 필요가 없다. 그래서 지연이 걸리고, 이 버그가 생긴다.

한 가지 더. Spring Data의 `save()`는 ID가 이미 채워져 있으면 `persist()`가 아니라 `merge()`를 호출한다. `merge()`는 "이미 있는 행인가?" 확인하려고 **SELECT는 즉시** 보내지만, 없으면 INSERT를 예약할 뿐이다. 그래서 결과적으로 INSERT는 여전히 지연된다.

---

## 5. rollback-only — 왜 500까지 가나

여기서 한 단계 더 나쁜 일이 일어난다.

**트랜잭션(transaction)** 은 "여러 DB 작업을 하나로 묶어서, 전부 성공하거나 전부 취소되게 하는 단위"다. 은행 송금에서 "A 계좌에서 빼기"와 "B 계좌에 넣기"가 반드시 같이 성공하거나 같이 실패해야 하는 것과 같다.

Spring에서는 `@Transactional`을 붙여서 이 범위를 지정한다.

그런데 트랜잭션 안에서 예외가 터지면, Spring은 그 트랜잭션에 **"rollback-only(이건 무조건 취소)"** 표시를 붙인다. 한 번 붙으면 되돌릴 수 없다. 나중에 커밋하려고 해도 Spring이 이렇게 말한다.

```
UnexpectedRollbackException: Transaction rolled back because it has been marked as rollback-only
```

이게 처리되지 않은 예외라서 결국 **HTTP 500**이 된다.

### 우리 코드에서 특히 나쁜 이유

`ensureTranslated`를 부르는 쪽을 보자. (`DisasterAlertService.java:542-543`)

```java
@Transactional                                    // ← ★ 여기에도 트랜잭션이 있다
public DisasterAlertDetailDto getAlertDetail(Long id, String lang) {
    DisasterAlert alert = disasterAlertRepository.findById(id)
            .orElseThrow(() -> new CustomException(ErrorCode.DISASTER_ALERT_NOT_FOUND, "id=" + id));
    ...
    translationService.ensureTranslated(id, language);   // 번역 캐시 채우기
    ...
    return dto;                                   // ← 여기서 트랜잭션 커밋
}
```

`getAlertDetail`에도 `@Transactional`이 붙어 있다. Spring의 기본 규칙(`REQUIRED`)에서는 **이미 트랜잭션이 열려 있으면 새로 만들지 않고 그 트랜잭션에 합류한다.**

즉 `ensureTranslated`는 자기만의 트랜잭션이 아니라 **`getAlertDetail`의 트랜잭션 안에서 돈다.** 그래서:

```
getAlertDetail 트랜잭션 시작
 └─ ensureTranslated: 번역 save() → INSERT 예약(카트에 담기)
 └─ ensureTranslated의 try/catch 통과 (예약만 했으니 아무 일 없음)
 └─ 번역 다시 조회(findBy...) → 조회 직전 flush → 💥 PK 충돌
      ├─ 이 예외를 감싸는 try/catch가 여기엔 없다 → 그대로 위로 전파
      └─ 동시에 트랜잭션이 rollback-only로 마킹됨
 └─ (설령 여기서 예외를 잡았더라도) 커밋 시점에 UnexpectedRollbackException
                                              → 결국 HTTP 500
```

4장에서 본 대로 우리 코드는 **커밋보다 먼저, 바로 다음 줄의 조회에서** 터진다. 하지만 결말은 같다 — 예외가 그대로 올라가도 500이고, 어떻게든 잡아냈더라도 이미 rollback-only가 찍혀서 커밋 때 `UnexpectedRollbackException`으로 다시 500이 된다. **rollback-only는 한 번 찍히면 취소할 수 없기 때문에, "예외만 잡으면 되겠지"는 통하지 않는다.**

**번역이라는 부가 기능 하나가 실패했을 뿐인데, 페이지 전체가 죽는다.** 원래 의도했던 "번역 실패하면 한국어 원문이라도 보여주자"가 완전히 무력화된다.

---

## 6. 해결책 3가지 비교

### 방법 A. 저장 직전에 한 번 더 확인하기 ❌

```java
if (repository.findBy(alertId, lang).isEmpty()) {   // 한 번 더 확인
    repository.save(...);
}
```

**안 된다.** 확인과 저장 사이의 틈이 좁아질 뿐 없어지지 않는다. 여전히 두 요청이 그 틈에 동시에 들어올 수 있다. 재현 확률만 낮추는 미봉책이고, 오히려 "고쳤다"고 착각하게 만들어 더 위험하다.

### 방법 B. 저장만 별도 트랜잭션으로 분리 (`REQUIRES_NEW`) 🔺

```java
@Transactional(propagation = Propagation.REQUIRES_NEW)
public void saveTranslation(...) { ... }
```

`REQUIRES_NEW`는 "바깥에 트랜잭션이 있어도 **새로** 하나 만들어라"는 뜻이다. 이러면 저장이 실패해도 그 실패는 **안쪽 트랜잭션에서 끝나고**, 바깥(`getAlertDetail`)은 rollback-only가 되지 않는다. 500은 막힌다.

다만 단점이 있다:
- DB 커넥션을 하나 더 쓴다 (동시 접속이 많으면 부담)
- Spring AOP 특성상 **같은 클래스 안에서 자기 메서드를 부르면 적용이 안 된다.** 별도 빈으로 분리해야 해서 구조가 복잡해진다
- 충돌 자체는 여전히 일어난다. 예외를 잡아 무시할 뿐이다

### 방법 C. UPSERT ✅ (권장)

**충돌을 잡는 게 아니라, 애초에 충돌이 예외가 되지 않게 만드는 방법.**

---

## 7. UPSERT가 뭔가

**UPSERT = UPDATE + INSERT.** "없으면 넣고, 있으면 무시하거나 갱신해라"를 **DB에게 한 번에 시키는 것**이다.

PostgreSQL 문법은 이렇다.

```sql
INSERT INTO disaster_alert_translation (disaster_alert_id, language_code, translated_message, translated_at)
VALUES (102596, 'JA', '...번역문...', now())
ON CONFLICT (disaster_alert_id, language_code) DO NOTHING;
--         └─ 이 기본키가 충돌하면      └─ 아무것도 하지 마 (에러 내지 말고 그냥 넘어가)
```

`ON CONFLICT ... DO NOTHING`을 붙이면, 이미 같은 키의 행이 있어도 **에러가 아니라 "0건 삽입"으로 조용히 끝난다.**

### 왜 이게 안전한가

핵심은 **"확인 → 저장"이 두 단계가 아니라 한 문장(single statement)이 된다는 것**이다. DB는 하나의 SQL 문장을 실행하는 동안 그 행에 락(lock)을 걸고 처리하므로, 두 요청이 동시에 들어와도 하나는 INSERT되고 다른 하나는 조용히 무시된다. **틈이 없다.**

> **비유**: 화장실 문을 열어보고 들어가는 게 아니라, 자물쇠가 달린 문에 카드키를 대는 것. 열리면 들어가고, 안 열리면 "아 누가 있구나" 하고 돌아선다. 확인과 진입이 한 동작이다.

`DO NOTHING`과 `DO UPDATE` 중에서는 **`DO NOTHING`이 우리 상황에 맞다.** 두 요청이 만든 번역은 어차피 같은 내용이므로, 먼저 저장된 걸 덮어쓸 이유가 없다.

---

## 8. 실제로 어떻게 고치나

### 8-1. 리포지토리에 네이티브 UPSERT 추가

JPA의 `save()`는 `ON CONFLICT`를 지원하지 않으므로, SQL을 직접 쓴다.

```java
public interface DisasterAlertTranslationRepository
        extends JpaRepository<DisasterAlertTranslation, DisasterAlertTranslationId> {

    /**
     * 번역 캐시 저장. 동시 요청이 같은 (alertId, languageCode)를 저장해도
     * PK 충돌을 예외로 만들지 않고 조용히 무시한다(먼저 저장된 쪽을 유지).
     * JPA save()는 ON CONFLICT를 지원하지 않아 네이티브 쿼리로 작성한다.
     */
    @Modifying
    @Query(value = """
            INSERT INTO disaster_alert_translation
                (disaster_alert_id, language_code, translated_message,
                 translated_disaster_type, translated_region_names, translated_at)
            VALUES (:alertId, :languageCode, :message, :disasterType, NULL, now())
            ON CONFLICT (disaster_alert_id, language_code) DO NOTHING
            """, nativeQuery = true)
    void upsert(@Param("alertId") Long alertId,
                @Param("languageCode") String languageCode,
                @Param("message") String message,
                @Param("disasterType") String disasterType);
}
```

- `@Modifying` — "이 쿼리는 조회가 아니라 데이터를 바꾼다"고 Spring Data에 알리는 표시. 없으면 실행이 거부된다.
- `nativeQuery = true` — JPQL이 아니라 **진짜 SQL**을 쓰겠다는 뜻. `ON CONFLICT`는 PostgreSQL 전용 문법이라 JPQL로는 표현할 수 없다.

### 8-2. 저장부를 교체

```java
// Before
translationRepository.save(
        DisasterAlertTranslation.of(alertId, targetLang, translatedMessage, translatedType, null)
);

// After
translationRepository.upsert(alertId, targetLang, translatedMessage, translatedType);
```

### 8-3. 이벤트 제목 쪽도 같이

`disaster_event_translation`도 똑같은 구조(복합 PK + 같은 저장 패턴)라 같은 문제가 있다. `DisasterEventTranslationRepository`에도 동일하게 적용해야 한다.

### 8-4. 주의: 네이티브 쿼리는 즉시 실행된다

`@Modifying` 네이티브 쿼리는 `save()`와 달리 **호출하는 순간 SQL이 나간다.** 즉 예외가 생기면 `try` 블록 안에서 터진다. 이것 자체가 이 해결책의 장점이다 — 4장에서 본 "나중에 터져서 못 잡는" 문제가 사라진다.

다만 JPA 영속성 컨텍스트(1차 캐시)를 우회하므로, 같은 트랜잭션 안에서 방금 넣은 행을 JPA로 다시 조회하면 안 보일 수 있다. 우리 코드는 저장 후 별도 조회로 값을 읽으므로(`findByIdAlertIdAndIdLanguageCode`) 이 부분을 함께 확인해야 한다.

---

## 9. 제대로 고쳐졌는지 확인하는 법

### 재현 테스트 (고치기 전에 먼저 작성 — Red)

동시 요청을 흉내 내는 통합 테스트를 쓴다.

```java
@Test
void 같은_알림을_같은_언어로_동시_조회해도_500이_나지_않는다() throws Exception {
    ExecutorService pool = Executors.newFixedThreadPool(2);
    CountDownLatch start = new CountDownLatch(1);   // 두 스레드를 같은 순간에 출발시키는 장치

    Callable<Void> task = () -> {
        start.await();                              // 신호 올 때까지 대기
        disasterAlertService.getAlertDetail(alertId, "ja");
        return null;
    };

    Future<Void> f1 = pool.submit(task);
    Future<Void> f2 = pool.submit(task);
    start.countDown();                              // 동시 출발!

    f1.get();   // 예외가 나면 여기서 터진다
    f2.get();
}
```

`CountDownLatch`는 "여러 스레드를 같은 순간에 출발시키는 신호총" 같은 도구다. 이게 없으면 하나가 먼저 끝나버려서 충돌이 재현되지 않는다.

**고치기 전에는 이 테스트가 실패해야 한다.** 실패하는 걸 확인하고 나서 고쳐야 "진짜 그 원인이었다"는 게 증명된다. (이 저장소의 Red-Green 규칙)

### 운영에서 확인

고친 뒤 서버 로그에 이 예외가 더는 안 찍히는지 본다.

```bash
docker logs --tail 500 disaster-backend | grep -i "UnexpectedRollbackException"
```

---

## 10. 정리

| 질문 | 답 |
|------|-----|
| 무엇이 문제인가 | "확인하고 저장하기(check-then-act)" 사이의 틈에 다른 요청이 끼어들어 기본키가 충돌한다 |
| 왜 `try/catch`가 못 잡나 | `save()`는 INSERT를 **예약만** 한다(쓰기 지연). 실제 발사는 flush 때인데, 우리 코드에서는 바로 다음 줄의 조회 쿼리가 flush를 유발한다 — 이미 `try`를 빠져나온 뒤다 |
| 왜 페이지 전체가 죽나 | 호출한 쪽(`getAlertDetail`)도 `@Transactional`이라 같은 트랜잭션을 공유한다. 예외가 그대로 올라가 500이 되고, 설령 잡더라도 rollback-only가 찍혀 커밋 때 `UnexpectedRollbackException`으로 다시 500 |
| 어떻게 고치나 | `INSERT ... ON CONFLICT DO NOTHING` (UPSERT). 확인과 저장을 DB의 한 문장으로 합쳐 틈을 없앤다 |
| 왜 재확인이나 락은 아닌가 | 재확인은 틈을 좁힐 뿐 없애지 못한다. UPSERT는 DB가 원자적으로 처리해 준다 |

### 오늘 배운 개념

- **check-then-act** — 확인과 행동이 분리되면 그 사이에 끼어들 수 있다. 동시성 버그의 대표 패턴
- **영속성 컨텍스트 / 쓰기 지연** — `save()`는 "작업 책상"에 올리고 SQL을 예약할 뿐이다. 미루는 이유는 ①네트워크 왕복 절감 ②중간 변경 병합 ③**DB 락 보유 시간 최소화**(가장 중요)
- **flush 시점 3가지** — ①커밋 직전 ②JPQL·네이티브 쿼리 실행 직전(방금 저장한 걸 조회에서 빠뜨리지 않으려고) ③`em.flush()` 직접 호출. **커밋 때만 나가는 게 아니라는 것**이 이 버그 이해의 핵심
- **ID 생성 전략에 따라 지연 여부가 다르다** — `@GeneratedValue(IDENTITY)`는 ID를 받아야 해서 즉시 INSERT(그래서 `try/catch`가 잡힘). 직접 지정하는 `@EmbeddedId`는 지연된다
- **트랜잭션 전파(propagation)** — `@Transactional`은 기본적으로 기존 트랜잭션에 합류한다(`REQUIRED`). 안쪽 실패가 바깥까지 번지는 이유
- **rollback-only** — 한 번 표시되면 되돌릴 수 없다. 예외를 잡아도 커밋 때 터진다
- **UPSERT** — `INSERT ... ON CONFLICT`. 경쟁 상황을 애플리케이션이 아니라 DB에게 맡기는 방법

---

## 11. 참고

- 명세: `specs/005-translation-pipeline/spec.md` — "예외 상황" 절 첫 항목
- 계획: `specs/005-translation-pipeline/plan.md` — "미해결 항목" #1, "트리거 경로와 트랜잭션 경계" 절
- 해당 코드: `backend/src/main/java/com/disaster/alert/alertapi/global/translation/TranslationService.java`, `backend/src/main/java/com/disaster/alert/alertapi/domain/event/service/EventTranslationService.java`
- 스키마: `backend/src/main/resources/db/migration/V3__create_disaster_alert_translation.sql`, `V40__create_disaster_event_translation.sql`
