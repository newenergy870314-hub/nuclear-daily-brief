# 원자력 주요기사 Daily Brief

무료 구성:

- Google News RSS에서 기사 제목과 링크 수집
- GitHub Actions가 평일 오전 6시 10분(KST)에 자동 실행
- GitHub Pages에서 고정 주소로 공개
- 한글/영문 각각 최대 20건
- 검색어 그룹별 분류 및 유사 제목 중복 제거

## 설치

1. GitHub에서 Public 저장소 `nuclear-daily-brief`를 만듭니다.
2. 이 압축파일의 내용을 저장소 루트에 업로드합니다.
3. 저장소 `Settings > Pages`로 이동합니다.
4. `Build and deployment > Source`를 `Deploy from a branch`로 선택합니다.
5. Branch는 `main`, 폴더는 `/(root)`를 선택하고 저장합니다.
6. 저장소 `Actions` 탭에서 `Update Nuclear Daily Brief`를 열고
   `Run workflow`를 한 번 실행합니다.
7. 수 분 뒤 다음 주소로 접속합니다.

   `https://내-GitHub-아이디.github.io/nuclear-daily-brief/`

## 검색어 변경

`collect_news.py`의 `GROUPS` 목록을 수정합니다.

## 주의

- GitHub Pages는 공개 웹사이트입니다.
- 기사 본문을 복제하지 않고 기사 제목과 원문 링크만 제공합니다.
- 무료 RSS 방식이므로 모든 인터넷 기사를 100% 수집한다고 보장할 수 없습니다.
- 영문 제목의 자동 한글 번역은 유료 AI API 없이 안정적으로 처리하기 어려워 원제만 표시합니다.
