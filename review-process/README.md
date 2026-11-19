# Codex 심층 검수 훅

## 설치

macOS, Linux 또는 WSL 환경에서 Bash, Git 2.31 이상, Python 3.8 이상과 로그인된 최신 Codex CLI가 필요해요.

```sh
cd review-process
./install.sh
```

설치 스크립트가 현재 컴퓨터의 실행 파일 경로를 찾아 `${CODEX_HOME:-$HOME/.codex}`에 스크립트와 훅 설정을 설치해요. 다른 훅은 보존하고, 기존 `hooks.json`을 변경할 때 `hooks.json.bak`에 직전 설정을 저장해요. 다른 컴퓨터에서도 그 컴퓨터의 환경에서 같은 명령을 실행하면 돼요.

Codex CLI를 열고 `/hooks`에서 `UserPromptSubmit`, `Stop`, `Interrupt`를 확인하고 승인해요. 훅이 비활성화된 환경에서는 Codex의 훅 설정도 확인해 주세요. [Codex 훅 문서](https://learn.chatgpt.com/docs/hooks)

## 사용

Git 프로젝트에서 프롬프트를 `[심층]`으로 시작해요.

```text
[심층] 장바구니의 합계 계산을 수정하고 테스트를 실행해줘.
```

- 프롬프트를 제출할 때 작업 트리를 기록해요. Codex가 응답을 마칠 때 읽기 전용 리뷰가 작업 전후의 Git 파일 변경분을 검수해요.
- **P0·P1**이 나오면 같은 작업을 이어서 해당 지적의 재현과 수정을 요청해요. 수정 후 자동으로 다시 검수해요. 최대 세 번 수정하며, 수정 뒤 변경분이 그대로면 남은 지적을 보고해요.
- **P2·P3**는 Codex 화면 메시지와 전체 검수 결과 파일에서 확인할 수 있어요.
- 변경이 없는 작업은 검수를 건너뛰어요. 검수 실패나 중단은 별도로 표시해요.
- 리뷰는 Codex의 모델 설정을 사용해요. 리뷰 모델을 따로 지정하려면 Codex를 실행할 환경에 `CODEX_DEEP_REVIEW_MODEL`을 설정해요.
- 실행 기록은 `$CODEX_HOME/deep-review-runs/` 아래에 남아요. 각 검수 회차의 `findings.json`에 전체 지적과 파일 위치가 있어요.

## 테스트

```sh
python3 -B -m unittest discover -s review-process -v
```

저장소 루트에서 실행하면 임시 Codex 설정과 모의 리뷰 결과로 설치, 재설치, 등급별 분기와 재검수를 검증해요.
