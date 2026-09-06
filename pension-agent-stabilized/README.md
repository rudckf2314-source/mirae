# Mirae Pension Agent — 안전성 수정본

기존 HyperCLOVA X, LangGraph, 상품·문서·법령 데이터, UI와
POST /api/search를 유지한 별도 작업본입니다.
STABILIZATION_REPORT.md를 먼저 읽어 주세요. 최종 제출 승인본은 아닙니다.

## 시작 전: 인증정보

원본 설정 예시에 실제 인증정보로 의심되는 값이 포함돼 있었습니다.
이 배포본에는 해당 값을 넣지 않았습니다. 공유된 키는 노출된 것으로 보고
소유자가 발급처에서 폐기·재발급 여부를 확인해야 합니다.
이 작업에서 키를 교체하거나 실제 API를 호출하지 않았습니다.

1. 새 폴더에 압축을 풀어 원본과 분리합니다. 기존 폴더에 덮어쓰지 마세요.
2. .env.example을 .env로 복사하고 새 CLOVA_STUDIO_API_KEY를 직접 입력합니다.
3. 법령 동기화/외부 조회를 쓸 때만 LAW_API_OC를 설정합니다.
4. .env, 키, 토큰, 인증 헤더는 공유·커밋하지 마세요.

법령은 기본적으로 로컬 DB를 사용합니다. LAW_QUERY_FALLBACK_API=0은
외부 법령 호출을 의도적으로 끈 설정이지, API 인증 성공/실패의 증거가 아닙니다.
외부 fallback을 켜더라도 허용 목록과 시행일 검증을 통과해야 합니다.

## 실행

검증 환경은 Linux / Python 3.12.13입니다. Windows 실행 명령은 다음과 같지만
Windows 자체 실행과 신규 환경 설치 완료까지 검증한 것은 아닙니다.

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

.env에 새 키를 입력한 다음 run.bat으로 API 서버를 시작합니다.
Streamlit UI는 `.venv\Scripts\python.exe -m streamlit run app.py`입니다.
Linux에서는 `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000`으로 실행합니다.

- GET /health: 서버 설정 확인. 실제 HyperCLOVA 통신 검사가 아닙니다.
- POST /api/search: 기존 채팅 응답 형식.
- GET /answer?question=...&question_id=...&top_k=5: 제출용 응답 어댑터.

응답 필드는 question_id, question, retrieved_context(출처 객체 배열),
think_trace(라우트·검증 상태 요약 문자열), answer이며 기존 응답 필드도 유지합니다.
think_trace는 모델 내부 사고과정이 아닙니다.
공식 명세의 필드 타입/추가 필드 허용 여부는 주최 측 문서와 대조해야 합니다.

## 오프라인 테스트

```bat
run_tests.bat
```

또는 `python -m pytest -q TEST tests`를 실행합니다. 루트 conftest.py가
가짜 키, 복제한 임시 법령 DB, 소켓 차단을 설정하므로 pytest에서는
실제 HyperCLOVA/법령 API를 호출하지 않습니다. HTTP 계약 테스트는 실제
FastAPI 경로를 실행하지만 LLM 응답은 대체합니다.
UI 보조 테스트는 `node --test tests/test_response_normalizer.js`입니다.

requirements.txt는 직접 의존성을 고정하고 constraints.txt는 검증 환경의
전이 의존성을 고정합니다. Windows 전용 조건부 의존성은 Linux 스냅샷에
포함되지 않습니다. 새 환경 설치 재현성은 네트워크 승인 중단으로 미확인입니다.

## Gold-100: 입력 점검 → 라이브 2회 → 별도 Holdout

과거 보고서의 100개 문항을 바이트 변경 없이
tests/gold100/fixtures/cases.json에 보존했습니다. 원본 Excel은 포함되지 않았고,
문항/정답이 Excel과 같은지는 원본을 확보해 확인해야 합니다.

API 없이 입력만 점검:

```bat
.venv\Scripts\python.exe -m tests.gold100.run_gold100 --dry-run --out-dir reports\gold100-input-check
```

새 키 설정·호출 비용 승인 후에만 다음을 실행하세요. 이번 작업에서는 실행하지 않았습니다.

```bat
.venv\Scripts\python.exe -m tests.gold100.run_gold100 --allow-live --repeat 2 --out-dir reports\gold100-stabilized-v2
```

출력 폴더는 새 폴더 또는 빈 폴더여야 합니다. --excel 파일경로로 원본 Excel을
지정하거나 --cases-json 파일경로로 평가 입력을 선택할 수 있습니다.
반복 평가에는 동일 코드·데이터·평가기 해시를 기록하며 중간 변경을 감지하면 중단합니다.

평가기 v2는 숫자 부분일치와 가짜 출처 판정을 고쳤습니다. 과거 v1 평가기는
gold100_evaluator_v1.py에 보존했습니다. 합격 임계값은 낮추지 않았지만
측정 방식이 달라졌으므로 과거 89점과 v2 점수는 직접 비교하지 마세요.
reports/의 기존 점수는 모두 수정 전 기록이며 이번 패치의 성적이 아닙니다.

두 번의 전체 평가에서 오류·신규 회귀·사실 누락을 확인한 후, 개발에 사용하지 않은
별도 Holdout 문항으로 평가하세요. 제공된 파일에는 검증된 독립 Holdout이 없습니다.

## Docker 및 데이터 운영

```bat
docker build -t mirae-pension-agent .
docker run --rm -p 8000:8000 --env-file .env mirae-pension-agent
```

Docker 이미지는 실제 빌드·기동하지 않았습니다. .dockerignore는 인증 파일,
Git 이력, 가상환경, 캐시, 보고서 등을 이미지에서 제외합니다.
docker-compose.yml은 PostgreSQL만 실행하며 기본 standard_json 모드에는 필요 없습니다.

법령 제공 기준일은 한국 시간의 오늘입니다. 시행일이 미래이거나 버전이 충돌한 조문은
제공하지 않습니다. 법령 DB 변경과 기준일 변경은 법령 캐시를 무효화합니다.
이는 법령 DB가 최신이라는 보장은 아닙니다. 별도 승인된 동기화가 필요합니다.
상품 JSON/문서 색인을 교체한 뒤에는 인메모리 검색기를 다시 읽도록 서버를 재시작하세요.
