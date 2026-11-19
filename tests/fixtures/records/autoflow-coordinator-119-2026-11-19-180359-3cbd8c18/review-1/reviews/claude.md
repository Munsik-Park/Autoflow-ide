# Review Summary

PR #120에는 blocking finding이 없습니다. non-blocking finding은 하나(Low)입니다. 변경 범위는 다음과 같습니다.

- 새 `modules` default(`~/work/autoflow-module`)가 `tools/config.py`, `SETTINGS.md`, README 세 곳에서 일치합니다.
- 새 `overlaps`/`base_overlap` check는 file identity로 비교합니다. 각 경로의 realpath에서 시작해 ancestor를 하나씩 올라가며 반대쪽 경로와 `os.path.samefile`로 대조하고, 양방향 모두 검사합니다.
- `tools/prepare.py`에서는 이 check가 `read_repo`/`read_token`, `os.mkdir(run_dir)` 이전에 실행되므로, issue가 요구한 "stops before creating the run"이 충족됩니다.

다음 경우를 trace해서 확인했습니다.

- **Sibling prefix**: `base=~/work/coordinator`, `modules=~/work/coordinator-modules`는 overlap이 아닙니다. 비교가 문자열 prefix가 아니므로 오탐이 없습니다.
- **Case variant / symlink**: case-insensitive volume에서 대소문자만 다른 경로와 symlink 경로를 모두 잡아냅니다.
- **존재하지 않는 경로**: `overlaps`는 False를 반환합니다. 이 경우 `prepare.py`는 뒤의 `modules_clone`에서 run을 만들기 전에 멈춥니다.
- **base가 modules 안에 있는 경우**: 정상적으로 감지합니다.
- **Import shadowing 없음**: `import modules as module_tool`로 alias를 주었기 때문에, 이후 `modules, modules_commit = modules_clone(...)`이 `tools/modules.py` module을 가리지 않습니다.
- **Import side effect 없음**: `tools/modules.py`의 top level에는 선언만 있고 실행 코드는 `__main__` guard 안에 있습니다.

`tools/invocation.py`에 check가 없는 것도 타당합니다. 이 파일은 `MODULES`를 `run.env`에서만 읽고(`tools/invocation.py:150`), 그 값은 `prepare.py`가 이미 check한 값입니다.

남은 문제는 coordination session이 따르는 tool reference에 새 stop 조건이 반영되지 않았다는 점 하나입니다.

# Findings

### 1. `coordinator-role.md`의 tool reference에 새 base-overlap stop이 빠져 있음

- **Severity:** Low
- **위치:** `coordinator-role.md:144-145` (PR이 수정하지 않은 파일)

**What breaks**
coordination session이 tool의 동작을 읽는 문서가 바뀐 동작과 어긋납니다.

- 144행은 `tools/modules.py`를 멈추게 하는 조건을 모두 나열합니다: default location이 없을 때, user location을 읽을 수 없을 때, `modules`와 `<home>/modules`가 같은 folder로 resolve될 때.
- 145행은 `tools/prepare.py`가 "stops without creating it when the `modules` clone is not a git clone or is the same folder as `<home>/modules`, or on the workspace's `gh-token` …"라고 씁니다.
- 두 행 모두 modules clone과 base가 같은 folder이거나 한쪽이 다른 쪽 안에 있을 때의 stop을 언급하지 않습니다. 이 PR이 `tools/modules.py:199-201`과 `tools/prepare.py:203-206`에 추가한 바로 그 stop입니다.

**Why it breaks**
- 이 문서는 behavior-linked입니다. coordination session이 tool을 호출하고 그 결과를 해석할 때 근거로 삼는 reference이기 때문입니다.
- PR body에 따르면 새 규칙은 `SETTINGS.md` > Module locations와 각 tool의 docstring에만 적혀 있습니다.
- 따라서 `coordinator-role.md`의 stop 목록은 불완전해졌습니다. 특히 144행은 이전까지 빠짐없는 목록이었습니다.

**Evidence for each failure precondition**
- 위 두 행은 현재 tree에 있습니다.
- 새 stop은 `SETTINGS.md`의 이전 기본 위치(`~/work/coordinator/autoflow-module`)처럼 base 아래에 `modules`를 둔 설정에서 실제로 발생합니다.
- 실제 피해는 크지 않습니다. stop message가 두 경로를 모두 명시하고 해결 방법도 안내하기 때문에 Low로 판정했습니다.

**Suggested fix direction**
144행과 145행의 stop 조건 목록에 base overlap을 추가하고, 해당 항목을 `SETTINGS.md` > Module locations에 연결합니다.

# Inline Comment Candidates

None. 이 finding은 PR이 수정하지 않은 파일의 행이므로 inline comment로 달 수 없습니다.

# Missing Tests

None. 이 repository에는 test suite가 없습니다(`tests/`나 `test/` 디렉터리 없음). PR body는 issue의 Done-when 항목마다 직접 실행한 결과를 기록합니다.

- stop하는 case 여섯 가지: `<base>/am`, `<base>/ws/am`, modules와 base가 같은 경우, `<modules>/b`, case variant 두 가지
- 통과하는 case: base 밖의 modules
- base가 존재하지 않는 case
- config.py의 default 출력

다만 `prepare.py`가 "prepares as before" 하는지는 끝까지 확인되지 않았습니다. 실행이 `read_token`에서 멈췄는데, scratch workspace가 git worktree 안에 있었기 때문입니다. 그래도 새 check 다음의 코드는 바뀌지 않았으므로 이 공백이 실질적인 위험은 아닙니다.

# Low Confidence

None.

---

이 review record는 review rule에 따라 한국어로 작성했습니다. aggregation 대상 record가 영어로 필요하면 말씀해 주십시오.

README의 autoflow-module 행은 issue AC가 요구한 대로 module interface의 표현을 쓰고 있습니다. 하지만 그 표현을 원문과 대조하지는 못했습니다. `gh api`로 `Munsik-Park/autoflow-module`의 `docs/module-interface.md`를 읽으려 했는데 권한 모드에서 거부되었고, 로컬 clone도 찾지 못했습니다.
