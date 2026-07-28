# 📈 Quant Project (글로벌 퀀트 연구소)

국내 주식(국장) 및 미국 주식(미장) 데이터 수집, 전략 스크리닝, 백테스팅 파이프라인 프로젝트입니다.

---

## 🛠️ 프로젝트 구조

* **`stock_screener/`**: 주식 스크리닝 및 백테스팅 엔진 소스 코드
* **`quant_env/`**: 파이썬 가상 환경 (Git 관리 제외)
* **`.env`**: API Key 및 보안 설정 파일 (Git 관리 제외)

## 종목 선별 로직
5단계 스크리닝 파이프라인의 평가 기준과 설계 근거는 [docs/screening_criteria.md](./docs/screening_criteria.md) 참고

---

## 💻 노트북 / 새 환경 초기 세팅 (1회만 진행)

1. **저장소 클론 (Git Clone)**
   ```bash
   git clone [https://github.com/본인ID/저장소이름.git .](https://github.com/본인ID/저장소이름.git .)
   cd QUANT_PROJECT

---

## 🚀 실행 방법

1. **가상환경 생성 및 활성화**
   ```bash
   python -m venv quant_env
   .\quant_env\Scripts\activate

2. **필요 패키지 설치**
   ```bash
   pip install -r requirements.txt

3. **환경 변수 파일 생성 (.env)**
최상위 루트 폴더(QUANT_PROJECT) 밑에 .env 파일을 새로 생성 후 API 키 입력
DART_API_KEY=your_api_key_here

---

## 🔄 교대 작업 루틴

1. 작업 시작 전 (최신 코드 당겨오기)
   ```bash
   git pull

2. 작업 완료 및 컴퓨터 이동 시 (변경사항 업로드)
   ```bash
   git add .
   git commit -m "feat: 변경 내용 요약 작성"
   git push