# Codex 외부 독립 리뷰 자동화 설계

## 1. 먼저 알아둘 기능과 명령

이 자동화는 구현을 마치려는 시점에 별도의 Codex 리뷰를 실행하고, 중요한 지적이 있으면 기존 구현 세션에 돌려보내는 구조다. Git은 검토할 변경분을 고정하고, Python은 각 단계의 실행 시점과 결과를 연결한다.

### Codex 리뷰와 비대화형 CLI

| 기능·명령 | 기본 의미와 이 프로젝트에서의 역할 |
|---|---|
| `/review`, `codex review` | Codex의 내장 코드 리뷰 기능이다. 지정한 Git 변경을 읽고 파일 위치와 우선순위가 붙은 지적을 돌려준다. `/review`는 대화 중에 입력하고, `codex review`는 터미널에서 비대화형으로 실행한다. |
| `codex exec`, `codex exec … review` | `exec`는 스크립트나 CI에서 Codex를 비대화형으로 실행하는 명령이다. 이 프로젝트는 `review` 하위 명령에 검토할 변경 범위와 원래 요청을 전달한다. 일반 작업을 시작하는 CLI 래퍼도 `exec`를 사용한다. |
| `-s read-only`, `--disable hooks` | 리뷰 실행의 샌드박스를 읽기 전용으로 선택하고, 그 실행에서 Hook을 끈다. 리뷰 턴이 종료될 때 같은 자동 리뷰가 다시 시작되는 일을 피한다. |
| `--json`, `-o <파일>`, `-` | 각각 실행 이벤트를 한 줄에 한 객체씩 출력하는 JSONL 형식, 마지막 메시지를 저장할 파일, 프롬프트를 표준 입력에서 읽겠다는 뜻이다. Python은 실행 이벤트와 리뷰 판정을 각기 읽는다. |
| `codex exec resume <세션 ID>` | 저장된 비대화형 세션을 이어 간다. CLI 래퍼는 구현 세션의 ID로 재개하고 리뷰 지적을 다음 요청으로 전달한다. |

Codex의 내장 리뷰는 Git 변경을 읽어 결함 후보를 보고한다. 결함 재현과 코드 수정, 관련 테스트는 지적을 전달받은 구현 세션의 역할이다. [Codex 코드 리뷰](https://learn.chatgpt.com/docs/code-review) · [Codex CLI 명령과 옵션](https://learn.chatgpt.com/docs/cli/reference#codex-exec)

### Hook과 `Stop` 이벤트

Hook은 Codex가 지정된 이벤트를 만났을 때 설정된 외부 명령을 호출하는 기능이다. 명령형 Hook은 표준 입력으로 이벤트 JSON을 받고, 필요하면 표준 출력으로 응답한다. 이 프로젝트에서는 Python 스크립트가 다음 이벤트를 처리한다.

| 이벤트 | 언제 호출되는가 | 이 프로젝트에서 하는 일 |
|---|---|---|
| `UserPromptSubmit` | 사용자 요청을 처리하기 직전 | `[심층]` 요청인지 확인하고 작업 전 Git 스냅샷을 저장한다. |
| `Stop` | Codex가 현재 응답을 마치려는 시점 | 구현 결과를 리뷰한다. `decision: "block"`과 `reason`을 반환하면 Codex가 `reason`을 다음 요청으로 사용해 같은 작업을 이어 간다. |
| `Interrupt` | 사용자가 진행 중인 메인 턴을 중단할 때 | 작업을 중단 상태로 기록하고, 진행 중이던 리뷰 결과가 나중에 돌아와도 추가 수정으로 이어지지 않게 한다. |

Hook 응답의 `systemMessage`는 사용자에게 알릴 내용을 담는다. 리뷰 지적을 보여 주면서 `reason`에는 구현 세션에 전달할 수정 요청을 따로 담을 수 있다. [Codex Hook 이벤트와 응답](https://learn.chatgpt.com/docs/hooks#stop)

### Git 스냅샷과 변경분

Git **트리**는 한 시점의 파일 상태를 나타내는 객체다. **인덱스**는 다음 커밋에 담을 파일 상태를 준비하는 스테이징 영역이다. Python은 임시 인덱스에서 현재 파일 상태를 트리로 만들어 두 시점의 트리 ID를 비교한다. 리뷰용 저장소에서는 두 트리를 부모·자식 커밋으로 만들어, `git diff HEAD^ HEAD`로 작업 전 상태에서 제출본까지의 차이를 확인할 수 있게 한다. [Git 스냅샷과 리뷰용 커밋 구현](./review_gate.py#L26)

## 2. Python을 연결하는 지점

작업 시작에 비교 기준을 저장하고, 구현 종료에 제출본을 고정한다. 리뷰가 돌아오면 Python이 결과의 형식과 변경분의 일치 여부를 확인한 뒤 보고 또는 수정 요청을 결정한다. 구현 세션이 수정하면 직전 제출본과 비교해 변화 여부를 확인하고, 변화가 있을 때 작업 시작점부터 수정본까지의 전체 변경을 새로 검토한다.

작은 자동화에서는 이 역할을 한 스크립트에 둘 수 있다. 이 저장소는 Hook의 이벤트 처리와 공통 리뷰 로직을 모듈로 나눈다. CLI 래퍼는 이벤트 없이도 같은 리뷰 함수를 직접 호출한다. CI에 연결한다면 기준·제출 리비전을 실행 시점에 넘기고, 리뷰 지적과 테스트 결과를 파이프라인 결과로 남기는 방식으로 이 구조를 적용할 수 있다.

| 파일 | 맡는 일 | 주요 함수 |
|---|---|---|
| [deep_review_hook.py](./deep_review_hook.py) | 이벤트 수신, 턴 상태 저장, 사용자 알림과 수정 요청 반환 | `run()`, `start_review()`, `stop_review()` |
| [review_gate.py](./review_gate.py) | Git 스냅샷, 리뷰 호출, 지적 검증, CLI 구동과 최종 검증 | `snapshot()`, `independent_review()`, `review_result()`, `gate()` |
| [review_usage.py](./review_usage.py) | CLI 실행에 해당하는 리뷰 로그 찾기, 선택적인 사용량 집계 | `find_reviewer()`, `save_usage()` |

## 3. 개념별 코드 설명

### 3.1 이벤트를 받아 작업 상태를 연결한다

작업 시작과 종료, 중단은 각각 별도 프로세스에서 호출될 수 있으므로 상태를 파일에 저장한다. `main()`은 `json.load(sys.stdin)`으로 이벤트를 읽고 `run()`에 전달한다. `run()`은 사용자 프롬프트의 앞 공백과 탭을 제거한 뒤 `[심층]`으로 시작하는 일반 사용자 턴을 선택한다. `start_review()`는 계획 모드나 Git 작업 트리 외부의 요청을 건너뛰고, 참여하는 작업은 저장소 루트와 작업 직전 Git 트리를 기준으로 삼는다. [이벤트 분기](./deep_review_hook.py#L127) · [시작 상태](./deep_review_hook.py#L43)

`state_directory()`는 `session_id`와 `turn_id`를 함께 해시해 턴별 상태 디렉터리를 만든다. 최초 `status.json`의 `root`는 저장소 루트, `task`는 태그를 뺀 원래 요청, `base`는 시작 트리, `reviews`는 완료한 리뷰 수, `result`는 현재 상태다. 리뷰가 끝나면 `last_reviewed`를 추가하고, 최종 상태로 바뀌면 `status.json`에서 작업 본문을 제거한다. [상태 디렉터리](./deep_review_hook.py#L22) · [상태 종료](./deep_review_hook.py#L62) · [마지막 검토 트리](./deep_review_hook.py#L109)

동시 호출은 `status.lock`으로 상태 읽기와 쓰기를 조정한다. `save_state()`는 `pending.json`에 먼저 쓴 다음 `status.json`을 교체한다. 첫 `Stop`은 잠금 안에서 `pending`을 `reviewing`으로 바꾼다. 리뷰 진행 중에 들어온 `Stop`은 이 상태를 보고 바로 반환한다. 리뷰 중에는 잠금을 풀어 `Interrupt`가 `interrupted`를 기록할 수 있게 하고, 리뷰 반환 뒤 다시 잠금을 잡아 상태가 여전히 `reviewing`일 때만 결과를 저장한다. [잠금과 상태 파일 교체](./deep_review_hook.py#L30) · [종료와 중단의 상태 전이](./deep_review_hook.py#L140)

### 3.2 Git으로 작업 전후의 비교 대상을 고정한다

비교 기준을 작업 시작 시점에 잡으면 기존 미커밋 변경을 기준 상태에 포함할 수 있다. 작업 트리가 사용 중이면 원본 인덱스를 유지하면서 스냅샷을 만들어야 한다. `snapshot()`은 명시된 리비전이 있으면 그 트리 ID를 반환한다. 현재 상태를 찍을 때는 기존 인덱스가 있으면 복사하고, 아래 코드로 **임시 인덱스**에만 변경을 모아 Git 트리 ID를 만든다. [스냅샷 구현](./review_gate.py#L26)

```python
env = dict(os.environ, GIT_INDEX_FILE=str(index))
git(root, "-c", "core.splitIndex=false", "add", "-A", "--", ".", env=env)
return git(root, "write-tree", env=env)
```

이 스냅샷에는 추적 파일의 수정·삭제와 Git이 새로 추가할 수 있는 파일이 반영된다. 스테이징 내용과 작업 트리 내용이 다르면 캡처한 트리에는 현재 파일 내용이 들어가고, 실제 스테이징은 유지된다. 같은 작업 트리를 여러 작업이 공유하면 시작 이후의 다른 작업 변경도 함께 잡힐 수 있다. 작업별 Git 작업 트리를 사용하면 각 리뷰가 비교할 범위를 분리할 수 있다.

`review_checkout()`은 회차별 `repository/`에 원본과 같은 Git 객체 형식으로 새 저장소를 만들고, `objects/info/alternates`를 통해 원본의 Git 객체를 읽게 한다. `commit-tree`로 시작 트리 커밋을 먼저 만들고 제출 트리 커밋을 그 자식으로 만든다. 제출 커밋을 체크아웃하면 `HEAD^`가 시작점, `HEAD`가 제출본이다. 이 과정에서 생성하는 커밋은 리뷰용 저장소에만 있다. [리뷰용 체크아웃 구현](./review_gate.py#L39)

### 3.3 별도 리뷰를 실행하고 결과를 찾는다

`independent_review()`는 원본 저장소를 가리킬 수 있는 Git 환경 변수들을 자식 프로세스 환경에서 빼고, 고정 체크아웃에서 내장 리뷰를 실행한다. 원래 요청과 `HEAD^` 대비 `HEAD`의 변경을 검토하라는 지시를 넘긴다. 리뷰 호출은 다음 인자 구성에 회차별 출력 파일과 설정을 더해 실행한다. 실제 호출은 `-o`로 회차의 `final.txt`를 지정하고 마지막 `-`로 프롬프트를 받는다. 스크립트의 리뷰 프로세스 실행 제한은 회차당 540초다. [별도 리뷰 실행](./review_gate.py#L105)

```text
codex exec -s read-only review --disable hooks --json
```

`invoke()`는 프롬프트와 시작 시각을 남기고 표준 출력의 CLI 이벤트를 `events.jsonl`, 표준 오류를 `stderr.txt`에 기록한다. 종료 코드가 0이고 마지막 종료 이벤트가 `turn.completed`일 때 완료로 처리한다. 그 다음 `find_reviewer()`는 부모 실행의 `thread.started`에서 스레드 ID를 얻어, 시작 시각 이후 수정된 세션 로그 중 첫 이벤트의 리뷰 출처와 부모 ID가 맞는 로그를 찾는다. 일치하는 로그가 정확히 하나여야 한다. 부모의 실행 여부와 실제 리뷰어의 판정을 연결하는 절차다. [프로세스 완료 확인](./review_gate.py#L55) · [리뷰 로그 선택](./review_usage.py#L14)

`review_result()`는 자식 리뷰 로그의 마지막 `task_complete`에서 `last_agent_message`를 JSON으로 읽는다. 여기서 `findings` 목록과 `overall_correctness`를 얻는다. 각 지적은 제목과 설명이 문자열이고, 절대 경로를 리뷰 저장소 기준 상대 경로로 변환할 수 있으며, 줄 범위가 `1 ≤ start ≤ end`인 정수여야 한다. 현재 코드가 검사하는 위치 조건은 경로의 범위와 줄 번호의 형식이다. [결과 형식 검사](./review_gate.py#L80)

`finding_priority()`는 정수 우선순위 0에서 3을 받는다. 숫자가 빠져도 제목의 `[P1]`처럼 유효한 접두사가 있으면 그 값으로 채운다. 숫자와 접두사가 어긋나거나 유효한 우선순위를 얻을 수 없으면 결과를 오류로 처리한다. 검증 후에는 `findings.json`을 저장하고, 리뷰용 체크아웃이 깨끗하며 `HEAD`의 트리가 원래 제출 트리와 일치하는지 확인한다. `stop_review()`와 `gate()`는 이어서 **원래 작업 트리**도 다시 찍어, 검토한 제출본과 같을 때 결과를 사용한다. [우선순위 처리](./review_gate.py#L69) · [리뷰 체크아웃 재확인](./review_gate.py#L122) · [원래 작업 트리 재확인](./deep_review_hook.py#L102)

### 3.4 지적을 보고하거나 기존 세션에 수정을 요청한다

자동 수정 여부를 정하는 기준은 리뷰어가 보고한 우선순위다. 두 진입점 모두 P0·P1만 수정 요청에 넣고 P2·P3은 사용자에게 보고한다. `repair_prompt()`는 지적의 상대 경로, 시작 줄, 제목과 본문을 묶어 전달한다. 구현 세션에는 지적을 재현하고 확인된 문제를 원래 작업 범위 안에서 수정한 뒤 관련 테스트를 실행하라고 요청한다. [수정 프롬프트](./review_gate.py#L131)

| 흐름 | 수정 요청과 최종 검증이 이루어지는 방식 |
|---|---|
| Hook의 `stop_review()` | `decision: "block"`과 수정 프롬프트를 담은 `reason`을 반환한다. 같은 회차의 P2·P3은 `systemMessage`에 담는다. 관련 테스트는 구현 세션에 요청한다. 기본 한도는 수정 요청 3회, 리뷰 4회다. |
| CLI의 `gate()` | `codex exec resume`으로 원래 구현 세션을 이어 가고, 반환된 세션 ID가 같은지 확인한다. `--acceptance-file`을 받으면 리뷰와 수정 양쪽에 전달한다. `--check`를 지정하면 P0·P1이 남아 있지 않은 회차에서 별도 검증 명령을 실행한다. |

예를 들어 한 회차에서 P1과 P2가 함께 나오면 Hook 응답은 다음 형태가 된다.

```json
{
  "decision": "block",
  "reason": "P1 지적의 위치와 근거를 바탕으로 재현하고 수정한다.",
  "systemMessage": "[심층] P2 지적 요약과 전체 리뷰 결과 경로"
}
```

CLI의 `check_worktree()`는 검토한 바이너리 포함 패치를 `candidate.patch`로 저장해 검증 명령에 경로를 넘긴다. 검증 출력과 종료 코드를 기록하며, 명령 전후 트리가 일치하는지 확인한다. 이로써 리뷰한 파일과 검증한 파일이 달라졌을 때 검증 결과를 그대로 수용하는 일을 피한다. [Hook의 우선순위 분기](./deep_review_hook.py#L109) · [CLI 반복 검수와 같은 세션 재개](./review_gate.py#L162) · [별도 검증 명령](./review_gate.py#L147)

| 종료 조건 | 코드의 처리 |
|---|---|
| 처음부터 변경분이 없음 | 두 흐름 모두 `no-changes`로 끝난다. |
| 수정했지만 직전 검토본과 같음 | `no-progress`를 기록한다. 같은 패치를 재검토하지 않는다. |
| 수정으로 작업 전 상태까지 돌아옴 | Hook은 `no-changes`, CLI는 `changes-reverted`를 기록한다. |
| 지적 없음 또는 P2·P3만 남음 | 각각 `review-clear`, `report-only`를 기록한다. CLI에서 선택한 별도 검증 명령이 실패하면 `check-failed`를 기록한다. |
| 수정 한도에 도달함 | `repair-limit`와 마지막 회차의 지적을 남긴다. |
| 리뷰 중 원래 작업 트리가 달라짐 | Hook은 `worktree-changed`, CLI는 `error`로 종료한다. |
| 중단 또는 리뷰 처리 실패 | Hook은 사용자 중단을 `interrupted`로 기록한다. 프로세스나 파싱의 오류는 실패로 기록하고 수정 요청을 이어 가지 않는다. |

### 3.5 실행 기록을 남기고 필요하면 사용량을 집계한다

작업별 `status.json`에는 종료 상태가, 회차별 디렉터리에는 고정 체크아웃과 실행 근거가 남는다. 결과를 확인할 때는 `findings.json`에서 검증을 거친 판정과 지적을, `reviewer-rollout.jsonl`에서 원본 리뷰 이벤트를, `events.jsonl`과 `stderr.txt`에서 CLI 실행 여부와 오류를 본다. CLI의 별도 검증을 실행한 경우 `verification/`에서 패치와 검증 출력을 확인한다. [리뷰 결과와 원본 로그 저장](./review_gate.py#L122) · [검증 기록](./review_gate.py#L147)

`review_usage.py`의 `find_reviewer()`는 필수 리뷰 경로에서 공통으로 사용한다. 별도 진입점인 `save_usage()`는 같은 로그에서 모델과 추론 강도, 누적 토큰, 단일 요청의 최대 입력을 읽어 `usage.json`을 만든다. 비용은 코드에 정의된 모델 및 토큰 조건에 맞을 때만 추정하며, 그 밖의 경우 `estimated_usd`는 `null`이다. Hook과 CLI에서 리뷰할 때 이 비용 집계는 자동 실행되지 않는다. [사용량 집계 구현](./review_usage.py#L38)
