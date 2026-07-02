# Claude Code Skill

skill은 클로드가 대화의 맥락을 분석해서 스스로 필요하다고 판단하면 참조하거나 @멘션으로 즉시 로딩한다.

## SKILL.md의 파일 구조

SKILL.md 파일은 상단의 메타데이터 블록과 그 아래의 본문 지침으로 구성된다.

메타데이터 블록은 `---`으로 감싼 YAML 형식이며 세 가지 핵심 필드를 포함한다.

| 필드 | 설명 |
| --- | --- |
| `name` | 소문자와 하이픈으로 작성하며, `/name` 형태의 슬래시 커맨드로 호출되는 식별자다. |
| `description` | 이 Skill이 무엇을 하고 언제 사용하는지를 명확히 기술한다. 클로드가 자동 활성화 여부를 판단할 때 이 필드를 참조하므로, 구체적으로 작성할수록 정확하게 호출된다. |
| `allowed-tools` | Skill이 사용할 수 있는 도구를 선언한다. 파일 읽기 쓰거나 Bash 명령이 필요하다면 반드시 명시해야한다. |

본문지침은 클로드가 실제로 따르는 지침으로, 무엇을 하는가와 언제 사용하는가를 명확히 포함해야 한다.

## SKILL.md 형식

```markdown
---
name: <소문자와 하이픈으로 구성된 식별자, 예: pdf-summary, commit-message>
description: <이 Skill이 무엇을 하고 언제 사용하는지를 한 문단으로 명확히 기술. 클로드가 이 필드를 보고 자동 활성화 여부를 판단하므로 트리거 상황을 구체적으로 적을수록 정확히 호출된다>
allowed-tools: <Skill이 사용할 도구 목록, 예: Bash, Read, Edit / 생략하면 기본 도구만 사용 가능>
---

# <Skill 제목>

## 언제 사용하는가
- <이 Skill이 활성화되어야 하는 상황을 조건 형태로 나열>

## 무엇을 하는가
1. <클로드가 실제로 수행할 절차를 순서대로 기술>
2. ...

## 주의사항
- <하지 말아야 할 것, 예외 상황, 사용자 확인이 필요한 지점 등>
```

- `---` 위쪽: YAML 메타데이터 블록 (name, description, allowed-tools)
- `---` 아래쪽: 클로드가 실제로 따르는 본문 지침. "언제 사용하는가"와 "무엇을 하는가"를 반드시 포함해야 하며, 필요하면 "주의사항" 같은 절을 추가한다.

이 템플릿의 각 자리에 실제 값을 채워 넣으면 아래 "예제: SKILL.md"와 같은 결과물이 만들어진다. 예를 들어 "커밋 메시지 작성을 자동화하는 Skill"을 만든다면 `name`에는 `commit-message`, `description`에는 언제 어떤 요청에서 활성화되어야 하는지, `allowed-tools`에는 `git diff` 실행에 필요한 `Bash`와 파일 확인용 `Read`를 채우는 식이다.

## 예제: SKILL.md

```markdown
---
name: commit-message
description: 스테이징된 변경 사항을 분석해 커밋 메시지 초안을 작성한다. 사용자가 "커밋 메시지 만들어줘", "커밋 정리해줘"라고 요청할 때 사용한다.
allowed-tools: Bash, Read
---

# Commit Message 작성 지침

## 언제 사용하는가
- 사용자가 커밋 메시지 작성을 요청했을 때
- git add로 스테이징된 변경 사항이 있을 때

## 무엇을 하는가
1. `git diff --staged`로 변경 내용을 확인한다.
2. 변경의 목적(버그 수정, 기능 추가, 리팩터링 등)을 파악한다.
3. 1~2문장으로 "왜" 변경했는지를 중심으로 커밋 메시지를 작성한다.
4. 사용자에게 초안을 제시하고, 확정되면 `git commit -m`으로 커밋한다.

## 주의사항
- 커밋은 사용자가 명시적으로 요청했을 때만 생성한다.
- `.env`, `credentials.json` 등 민감한 파일은 커밋 대상에서 제외한다.
```

## 실제 사용 예시

**상황**: 사용자가 `git add`로 파일을 스테이징해 두고 커밋 메시지 작성을 요청함

```
user: 방금 수정한 내용 커밋 메시지 만들어줘

claude: (commit-message 스킬이 description을 보고 자동 활성화됨)
1. git diff --staged 실행 → 로그인 실패 시 에러 메시지가 표시되지 않던 버그를
   수정한 변경 사항 확인
2. 변경 목적 파악 → 버그 수정(fix)
3. 커밋 메시지 초안 제시:
   "fix: 로그인 실패 시 에러 메시지가 표시되지 않던 문제 수정"
4. 사용자가 확정하면 git commit -m "..." 실행
```

이처럼 사용자가 명시적으로 `/commit-message`를 호출하지 않아도, 대화 맥락("커밋 메시지 만들어줘")이 SKILL.md의 `description`과 일치하면 클로드가 스스로 해당 스킬을 찾아 로딩하고 지침을 따른다.

## 클로드 코드 스킬 저장소

클로드 코드 Skill을 필요할 때마다 일일이 다 만들어서 쓰기는 부담스럽기 때문에 아래 저장소에서 가져다 쓰면 된다고 한다.

| 제공자 | 링크 |
| --- | --- |
| 앤트로픽이 운영하는 스킬 저장소 | [github.com/anthropics/skills](https://github.com/anthropics/skills) |
| Vercel | [skills.sh](https://skills.sh) |
| Composio | [github.com/ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) |
| SkillsMP | [skillsmp.com](https://skillsmp.com) |

## 스킬 활성화 방법

- **자동 활성화**: 위의 클로드 스킬 예제처럼 커밋 메세지를 요청할때 Skill을 자동으로 로드한다.
- **수동 활성화**: `/skill-name`과 같이 슬래시 명령어로 활성화한다.

## 스킬 비활성화 방법

1. `mv SKILL.md SKILL.md.disabled`와 같이 뒤에 disabled를 추가한다.
2. skills 폴더 바깥으로 이동한다.
3. 스킬을 삭제한다.