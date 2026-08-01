# 📈 Quant Project (글로벌 퀀트 연구소)

국내 주식(국장) 및 미국 주식(미장) 데이터 수집, 전략 스크리닝, 백테스팅 파이프라인 프로젝트입니다.

---

## 🛠️ 프로젝트 구조

* **`stock_screener/`**: 주식 스크리닝 및 백테스팅 엔진 핵심 소스 코드
* **`quant_env/`**: 파이썬 가상 환경 (Git 관리 제외)
* **`.env`**: API Key 및 보안 설정 파일 (Git 관리 제외)
* **`test_strategy.ipynb`**: 파이프라인 단계별 실전 검증 및 통합 테스트 노트북

## 📝 상세 문서 (Docs)

* **[아키텍처 및 책임 경계 (architecture.md)](./docs/architecture.md)**: 5단계 스크리닝 파이프라인의 시스템 설계 및 데이터 흐름
* **[종목 선별 로직 (screening_criteria.md)](./docs/screening_criteria.md)**: 단계별 평가 기준, 임계치(Percentile) 및 설계 근거
* **[진행 로그 (decisions_log.md)](./docs/decisions_log.md)**: 개발 중 논의 및 의사결정 기록

---

## 💻 노트북 / 새 환경 초기 세팅 (1회만 진행)

**1. 저장소 클론 (Git Clone)**

```bash
git clone https://github.com/본인ID/저장소이름.git .
cd QUANT_PROJECT
```

**2. 가상환경 생성 및 활성화**

```bash
python -m venv quant_env
# Windows
.\quant_env\Scripts\activate
# Mac/Linux
source quant_env/bin/activate
```

**3. 필요 패키지 설치**

```bash
pip install -r requirements.txt
```

**4. 환경 변수 파일 생성 (.env)**

최상위 루트 폴더(QUANT_PROJECT) 밑에 `.env` 파일을 새로 생성 후 API 키 입력

```bash
DART_API_KEY=your_api_key_here
KRX_API_KEY=your_api_key_here
KRX_ID=your_ID_here
KRX_PW=your_PW_here
```

---

## 🚀 실행 방법

주피터 노트북 실행

```bash
jupyter notebook test_strategy.ipynb
```

추후 백테스트 모듈 통합 시 `python main.py` 진입점이 추가될 예정입니다.

---

## 🔄 교대 작업 루틴

**1. 작업 시작 전 (최신 코드 당겨오기)**

```bash
git pull
```

**2. 작업 완료 및 컴퓨터 이동 시 (변경사항 업로드)**

```bash
git add .
git commit -m "feat: 변경 내용 요약 작성"
git push
```
