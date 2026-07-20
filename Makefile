.PHONY: help install report triage issues escalate dump-context report-allure triage-allure clean

help:          ## показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:       ## поставить зависимости
	pip install -r requirements.txt

report:        ## живой прогон тестов -> reports/report.xml
	pytest -q --junitxml=reports/report.xml; true

triage:        ## ИИ-триаж падений -> output/
	python analyzer/analyzer.py reports/report.xml

escalate:      ## триаж со спорными, переспрошенными умной моделью
	python analyzer/analyzer.py reports/report.xml --escalate

issues:        ## триаж + завести черновики багов в POST /issues
	python analyzer/analyzer.py reports/report.xml --file-issues

dump-context:  ## показать пакеты контекста, отправляемые модели (слайд «пакет контекста»)
	python analyzer/analyzer.py reports/report.xml --dump-context

report-allure: ## Allure-отчёт прогона + категории (слайд «предел эвристик»)
	pytest -q --alluredir=reports/allure-results; true
	cp reports/categories.json reports/allure-results/categories.json
	allure serve reports/allure-results

triage-allure: ## триаж из allure-results вместо junit (жирный вход)
	python analyzer/analyzer.py --from-allure

clean:         ## убрать артефакты прогона
	rm -rf reports/report.xml reports/allure-results output/verdicts.json output/triage.md output/issues.json
