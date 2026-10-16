FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --chmod=0444 runner.py /opt/workbench/runner.py
# COPY mode can also affect newly created destination directories.
# Keep the trusted runner readable and its parent traversable by the sandbox UID.
RUN chmod 0555 /opt/workbench && chmod 0444 /opt/workbench/runner.py
USER 65532:65532
RUN ["python", "-I", "-c", "from pathlib import Path; p = Path('/opt/workbench/runner.py'); compile(p.read_text(), str(p), 'exec')"]
WORKDIR /scratch
ENTRYPOINT ["python", "-I", "/opt/workbench/runner.py"]
