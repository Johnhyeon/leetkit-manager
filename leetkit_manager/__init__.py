"""LeetKit Manager — StockLens·DartLens·TelegramLens 통합 설치·진단·업데이트 도구."""

# 이 리포에서 버전이 적히는 유일한 곳. pyproject.toml이 여기서 읽어간다
# (`[tool.hatch.version] path`). 예전엔 양쪽에 따로 적혀 있었는데 이쪽을 안 올려서
# 0.1.0에 멈췄고, 그 결과 앱이 자기 버전을 0.1.0으로 읽어 최신을 깔아도 항상
# "업데이트 있음"이 됐다. 릴리스할 때 이 한 줄만 올리면 된다.
__version__ = "0.1.9"
