FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --chmod=0444 runner.py /opt/workbench/runner.py
USER 65532:65532
WORKDIR /scratch
ENTRYPOINT ["python", "-I", "/opt/workbench/runner.py"]
