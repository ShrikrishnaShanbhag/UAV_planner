# Every target delegates to scripts/dev.py so that Windows users without `make`
# can run the same commands: python scripts/dev.py <target>
PY ?= python
TARGETS := setup test test-backend test-frontend lint fmt run-api run-web experiments \
           experiments-quick experiments-full experiments-scenarios report e2e openapi docker

.PHONY: $(TARGETS)

$(TARGETS):
	$(PY) scripts/dev.py $@
